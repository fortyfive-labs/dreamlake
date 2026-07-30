"""``dreamlake.dataset.Dataset`` — robot-training episode datasets on DreamDB.

The write path a labeling pipeline calls once per processed episode::

    from dreamlake.dataset import Dataset

    ds = Dataset.create("wash-the-dishes")          # platform bucket (default)
    epo = ds.add_episode(
        {"head": "head.mov", "wrist": "wrist.mov"}, # or a single path
        episode_id="ep-001",
        joints_pose=joints_dict,   # per-frame joints, binds per camera
        subtasks=subtasks_dict,    # action segmentation, episode-level
        meta={"task": "wash the dishes"},
    )
    epo.embed()                                     # make it searchable
    ds.search("hands rinsing a bowl")

Storage: one DreamDB Space per dataset, one timeline. Episodes occupy
disjoint one-hour anchor slots (see ``_schema``); every camera of an episode
shares its slot, which is what makes multi-camera time alignment free. One
camera == one set of tracks (``video_preview__{camera}`` /
``video_raw__{camera}`` / ``joints_pose__{camera}``); tracks for new cameras
are added on first use. The playback encoding profile (height/fps/fragment
length) is a DATASET-lifetime property chosen at ``create`` and stored in
the space meta — per-episode calls never pass it. The layout is shared
byte-for-byte with the TypeScript CLI.

Backends: by default the dataset lives in the DreamLake platform bucket
(``Dataset.create("name")`` — the preset registers the catalog row and
brokers scoped credentials internally; requires ``dreamlake login`` or
``DREAMLAKE_API_KEY``). Pass ``backend=`` to write anywhere dreamdb can
reach instead, with no platform involvement.

Per-episode operations live on the :class:`Episode` handle
(``ds.episode(id)`` / the return of ``add_episode``): reads, ``revise``,
``add_cameras``, user tracks (``x_`` namespace, declared with
``ds.add_track``), and single-episode search. This preset is a thin layer
over ``dreamlake.db``; the ``.db`` property hands you the live dreamdb
handle for everything outside the contract.
"""

from __future__ import annotations

import json
import os
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from ._episode import Episode
from ._ffmpeg import FfmpegError, FragmentedVideo, ProbeResult, fragment_video, probe
from ._schema import (
    DATASET_SCHEMA_TYPE,
    DEFAULT_CAMERA,
    DEFAULT_ENCODING,
    ENCODING_META_KEY,
    EPISODE_STRIDE_NS,
    FIELD_EPISODE_META,
    FIELD_FRAME_VEC,
    FIELD_SUBTASK_LABEL,
    FIELD_SUBTASK_VEC,
    FIELD_SUBTASKS,
    MAX_EPISODE_SECONDS,
    META_KEYS,
    PUBLIC_META_KEY,
    REVISION_WINDOW_NS,
    TRACK_KINDS,
    TrackInfo,
    USER_TRACK_RE,
    USER_TRACKS_META_KEY,
    base_anchor,
    build_schema,
    classify_track,
    gid_of,
    joints_track,
    preview_track,
    raw_track,
    validate_camera_name,
    validate_joints_pose,
    validate_subtasks,
)

Annotation = Union[Dict[str, Any], str, Path, None]
Videos = Union[str, Path, Dict[str, Union[str, Path]]]


class DatasetError(RuntimeError):
    """A dataset operation that failed for a reason the caller can act on."""


def _load_annotation(value: Annotation, what: str, validate) -> Optional[Dict[str, Any]]:
    """Accept a dict (the pipeline's in-memory output) or a JSON file path."""
    if value is None:
        return None
    if isinstance(value, (str, Path)):
        try:
            with open(value) as f:
                doc = json.load(f)
        except OSError as e:
            raise DatasetError(f"could not read {what} '{value}': {e}") from e
        except json.JSONDecodeError as e:
            raise DatasetError(f"{what} '{value}' is not valid JSON: {e}") from e
    elif isinstance(value, dict):
        doc = value
    else:
        raise DatasetError(f"{what} must be a dict or a path to a JSON file, got {type(value).__name__}")

    err = validate(doc)
    if err:
        raise DatasetError(f"{what}: {err}")
    return doc


def _preview_width(w: int, h: int, preview_height: int) -> int:
    """The width ffmpeg's ``scale=-2:H`` produces: aspect preserved, even."""
    return 2 * round(w * preview_height / h / 2)


def _fps_drift_frames(ann_fps: float, video_fps: float, duration_sec: float) -> float:
    """Disagreement between annotation and video frame rates, expressed as
    accumulated drift in FRAMES over the clip — the quantity that matters,
    since the viewer maps playback time to a frame index."""
    return abs(ann_fps - video_fps) * duration_sec


_ENCODER_CACHE: Dict[str, Any] = {}


def _check_schema_type(inner, where: str) -> None:
    """Refuse to interpret a space written under a DIFFERENT schemaType as an
    episode dataset. A space with no stamp at all is accepted: the field
    layout check happens naturally on first read."""
    try:
        meta = inner.meta() or {}
    except Exception:
        return
    stamped = meta.get("dreamdb.schema_type")
    if stamped and stamped != DATASET_SCHEMA_TYPE:
        raise DatasetError(
            f"{where} holds schemaType '{stamped}', not '{DATASET_SCHEMA_TYPE}' — "
            f"open it with dreamlake.db instead of this preset"
        )


class Dataset:
    """One robot-training dataset: many episodes, each with one or more
    camera videos plus annotations."""

    def __init__(self, inner, backend: str, name: Optional[str] = None):
        self._ds = inner
        self.backend = backend
        self.name = name
        self._encoding = dict(DEFAULT_ENCODING)
        self._public = False
        self._row_cache: Optional[List[Dict[str, Any]]] = None
        # Exact-scan matrices for search(), per vector field.
        self._vec_scan_cache: Dict[str, Any] = {}
        self._load_space_meta()

    def _load_space_meta(self) -> None:
        try:
            meta = self._ds.meta() or {}
        except Exception:
            return
        raw = meta.get(ENCODING_META_KEY)
        if raw:
            try:
                self._encoding.update(json.loads(raw))
            except (TypeError, json.JSONDecodeError):
                pass
        self._public = meta.get(PUBLIC_META_KEY) == "1"

    @property
    def db(self):
        """The live ``dreamdb.Dataset`` handle under this preset — the escape
        hatch for anything the preset does not wrap (absolute-anchor appends,
        columnar bulk reads, branching). The preset's layout invariants are
        yours to respect on this handle."""
        return self._ds

    @property
    def encoding(self) -> Dict[str, Any]:
        """The dataset's playback encoding profile (read-only view)."""
        return dict(self._encoding)

    # ---- Construction --------------------------------------------------
    #
    # `dreamlake.db` is the platform-free engine; the PRESET binds a dataset
    # to the DreamLake platform (catalog row + brokered credentials via
    # `dreamlake._platform`). Creation is deliberately not idempotent.

    @classmethod
    def create(
        cls,
        name: Optional[str] = None,
        *,
        backend: Optional[str] = None,
        visibility: Optional[str] = None,
        preview_height: int = 720,
        preview_fps: float = 30.0,
        frag_seconds: float = 2.0,
    ) -> "Dataset":
        """Create an empty dataset.

        The encoding profile (``preview_height``/``preview_fps``/
        ``frag_seconds``) is chosen HERE, once, for the dataset's lifetime —
        every clip on one camera track must share one init segment, so these
        cannot vary per episode. Stored in the space meta; every ingest
        reads it.
        """
        if backend is None and not name:
            raise DatasetError(
                "Dataset.create needs a name (platform mode) or backend= (self-hosted)"
            )
        profile = {
            "preview_height": int(preview_height),
            "preview_fps": float(preview_fps),
            "frag_seconds": float(frag_seconds),
        }
        if not (1.0 <= profile["frag_seconds"] <= 30.0):
            raise DatasetError("frag_seconds must be between 1 and 30 seconds")

        if backend is not None:
            from dreamlake import db

            try:
                db.open(backend=backend)
            except Exception:
                pass  # nothing there — the good case
            else:
                raise DatasetError(
                    f"a dataset already exists at {backend} — use Dataset.open() to add to it"
                )
            inner = db.create(
                build_schema(), backend=backend,
                schema_type=DATASET_SCHEMA_TYPE, title=name,
            )
        else:
            from dreamlake import _platform

            try:
                inner = _platform.create_dataset(
                    name, build_schema(),
                    schema_type=DATASET_SCHEMA_TYPE, visibility=visibility,
                )
            except _platform.DatasetExistsError as e:
                raise DatasetError(str(e)) from e

        inner.set_meta(ENCODING_META_KEY, json.dumps(profile))
        if visibility == "public":
            inner.set_meta(PUBLIC_META_KEY, "1")
        lease = getattr(inner, "dreamlake_lease", None)
        return cls(inner, backend or (lease or {}).get("backend_url", ""), name)

    @classmethod
    def open(
        cls,
        name: Optional[str] = None,
        *,
        backend: Optional[str] = None,
        preview_height: Optional[int] = None,
        preview_fps: Optional[float] = None,
        frag_seconds: Optional[float] = None,
    ) -> "Dataset":
        """Open an existing dataset by platform name or by backend URI.

        The optional encoding kwargs VERIFY, never set: pass the values your
        script assumes and open() errors on mismatch with the stored profile
        (so the ``try open / except create`` idiom cannot silently eat a
        config edit). Omitted kwargs are not checked.
        """
        if backend is None and not name:
            raise DatasetError(
                "Dataset.open needs a name (platform mode) or backend= (self-hosted)"
            )

        if backend is not None:
            from dreamlake import db

            try:
                inner = db.open(backend=backend)
            except Exception as e:
                raise DatasetError(
                    f"no dataset at {backend} — create one with Dataset.create() ({e})"
                ) from e
            _check_schema_type(inner, backend)
            ds = cls(inner, backend, name)
        else:
            from dreamlake import _platform

            try:
                inner = _platform.open_dataset(name)
            except _platform.DatasetNotFoundError as e:
                raise DatasetError(str(e)) from e
            _check_schema_type(inner, f"dataset '{name}'")
            lease = getattr(inner, "dreamlake_lease", None)
            ds = cls(inner, (lease or {}).get("backend_url", ""), name)

        for key, want in (
            ("preview_height", preview_height),
            ("preview_fps", preview_fps),
            ("frag_seconds", frag_seconds),
        ):
            if want is not None and float(ds._encoding[key]) != float(want):
                raise DatasetError(
                    f"this dataset's encoding profile has {key}={ds._encoding[key]}, "
                    f"but open() was told to expect {want} — the profile is fixed at "
                    f"create time and cannot be changed"
                )
        return ds

    # ---- Episode listing / lookup ---------------------------------------

    def _invalidate(self) -> None:
        self._row_cache = None

    def _append_and_invalidate(self, samples: List[Dict[str, Any]]) -> None:
        self._ds.append_many(samples)
        self._invalidate()

    def _rows(self) -> List[Dict[str, Any]]:
        """Episode meta rows (cached until the next write through this
        handle). One column read serves every listing and lookup."""
        if self._row_cache is not None:
            return self._row_cache
        rows: List[Dict[str, Any]] = []
        try:
            batches = list(self._ds.iter_all_batches(fields=[FIELD_EPISODE_META]))
        except Exception as e:
            if "no FieldTrack" in str(e) or "not in schema" in str(e):
                raise DatasetError(
                    "this space has no 'episode_meta' track — it was written "
                    "by a different (probably older) layout. Read it with "
                    "dreamlake.db, or re-ingest into a new dataset."
                ) from e
            raise
        # Revisions live at base+0, base+1, ... — the HIGHEST anchor per slot
        # is the current version (engine same-anchor resolution is content-
        # ordered, so revisions never share an anchor; see REVISION_WINDOW_NS).
        best: Dict[int, Tuple[int, Dict[str, Any]]] = {}
        for batch in batches:
            anchors = batch.get("_time_anchors") or []
            values = batch.get(FIELD_EPISODE_META) or []
            for anchor, value in zip(anchors, values):
                a = int(anchor)
                gid = gid_of(a)
                try:
                    meta = json.loads(value)
                except (TypeError, json.JSONDecodeError):
                    meta = {"episode_id": f"<unparseable @ {a}>"}
                if gid not in best or a > best[gid][0]:
                    best[gid] = (a, meta)
        for gid in sorted(best):
            rev_anchor, meta = best[gid]
            rows.append({
                "gid": gid,
                "anchor": base_anchor(gid),   # slot base — the episode's identity
                "_rev": rev_anchor,           # highest meta anchor, for the next write
                **meta,
            })
        self._row_cache = rows
        return rows

    def _require_row(self, episode_id: str) -> Dict[str, Any]:
        # Fresh read: lookups that precede writes must not trust the cache.
        self._invalidate()
        for row in self._rows():
            if row.get("episode_id") == episode_id or str(row["gid"]) == str(episode_id):
                return row
        have = ", ".join(str(r.get("episode_id")) for r in self._rows()) or "none"
        raise DatasetError(f"no episode '{episode_id}' in this dataset (have: {have})")

    def episodes(self) -> List[Episode]:
        """Every episode, as handles, ordered by slot — one column read.
        ``[e.meta for e in ds.episodes()]`` recovers plain dicts."""
        return [Episode(self, row) for row in self._rows()]

    def episode(self, episode_id: str) -> Episode:
        """The handle for one episode (by id, or by slot number as a string).
        Raises :class:`DatasetError` when absent."""
        return Episode(self, self._require_row(episode_id))

    # ---- Introspection ---------------------------------------------------

    def cameras(self) -> List[str]:
        """The dataset's cameras — union over episodes, primary-style order
        (``main`` first, then alphabetical; same order the viewer uses)."""
        seen = set()
        for row in self._rows():
            seen.update((row.get("cameras") or {}).keys())
        ordered = sorted(seen)
        if DEFAULT_CAMERA in seen:
            ordered.remove(DEFAULT_CAMERA)
            ordered.insert(0, DEFAULT_CAMERA)
        return ordered

    def tracks(self) -> List[TrackInfo]:
        """The dataset's track catalog as the preset knows it: the fixed
        episode-level tracks, each camera's namespace triple, and user
        tracks declared through :meth:`add_track`. (Columns added through
        the bare ``ds.db`` handle are outside the contract and not listed.)
        """
        names: List[str] = [
            FIELD_EPISODE_META, FIELD_SUBTASKS,
            FIELD_FRAME_VEC, FIELD_SUBTASK_VEC, FIELD_SUBTASK_LABEL,
        ]
        for cam in self.cameras() or [DEFAULT_CAMERA]:
            names += [preview_track(cam), raw_track(cam), joints_track(cam)]
        names += sorted(self._user_tracks().keys())

        out: List[TrackInfo] = []
        for n in names:
            kind, camera, role = classify_track(n)
            if role == "user":
                kind = self._user_tracks().get(n, kind)
            out.append(TrackInfo(name=n, kind=kind, role=role, camera=camera,
                                 preset=role not in ("user", "unknown")))
        return out

    def _user_tracks(self) -> Dict[str, str]:
        try:
            raw = (self._ds.meta() or {}).get(USER_TRACKS_META_KEY)
            return json.loads(raw) if raw else {}
        except Exception:
            return {}

    # ---- User tracks -----------------------------------------------------

    def add_track(self, name: str, kind: str, *, mime: Optional[str] = None) -> None:
        """Declare a user track. ``kind`` is the dreamdb vocabulary,
        verbatim: ``"image"``/``"video"`` (with ``mime=``; JSON documents
        are ``kind="image", mime="json"`` — exactly how the preset's own
        annotation tracks are declared) or a scalar (``"scalar_float"``,
        ``"scalar_int"``, ``"scalar_bool"``, ``"scalar_string"``,
        ``"scalar_categorical"``, ``"scalar_timestamp"``).

        Names are enforced to ``^x_[a-z0-9_]+$`` (no ``__``) — the user
        namespace the preset promises never to claim. Write and read through
        the Episode handle (``set_track``/``append_track``/``get_track``/
        ``read_track``). Declaring an existing track is idempotent success.
        The web viewer does not render user tracks.
        """
        if not USER_TRACK_RE.match(name) or "__" in name:
            raise DatasetError(
                f"user track names must match ^x_[a-z0-9_]+$ with no '__' "
                f"(got '{name}') — the x_ prefix is the namespace future preset "
                f"versions promise never to claim"
            )
        if kind == "embedding":
            raise DatasetError(
                "embedding tracks can only be declared when a space is created "
                "(the LSH index is part of the schema — a DreamDB constraint). "
                "Use the built-in search fields, or a custom schema via dreamlake.db."
            )
        if kind not in TRACK_KINDS:
            raise DatasetError(f"unknown track kind '{kind}' — one of {list(TRACK_KINDS)}")

        declared = self._user_tracks()
        if name in declared and declared[name] != kind:
            raise DatasetError(
                f"track '{name}' is already declared as kind '{declared[name]}' — "
                f"a track's kind cannot change"
            )
        adder = getattr(self._ds, f"add_{kind}")
        try:
            if kind in ("image", "video"):
                adder(name, mime=mime or ("json" if kind == "image" else "h264"),
                      required=False)
            else:
                adder(name, required=False)
        except Exception as e:
            if "already exists" not in str(e):
                raise
        if declared.get(name) != kind:
            declared[name] = kind
            self._ds.set_meta(USER_TRACKS_META_KEY, json.dumps(declared))

    def _read_track_window(self, field: str, start_ns: int, end_ns: int):
        out = []
        try:
            batches = list(self._ds.iter_all_batches(
                fields=[field], start_ns=start_ns, end_ns=end_ns
            ))
        except Exception as e:
            if "not in schema" in str(e) or "no FieldTrack" in str(e):
                raise DatasetError(
                    f"no track '{field}' in this dataset — declare it first with "
                    f"ds.add_track({field!r}, kind=...)"
                ) from e
            raise
        for batch in batches:
            anchors = batch.get("_time_anchors") or []
            values = batch.get(field) or []
            for a, v in zip(anchors, values):
                if v is not None:
                    out.append((int(a), v))
        return out

    # ---- Write path ------------------------------------------------------

    @staticmethod
    def _normalize_videos(videos: Optional[Videos]) -> Dict[str, str]:
        """``videos`` as the canonical ``{camera: path}`` dict. A bare path
        is the single-camera case: it becomes the default camera."""
        if videos is None:
            return {}
        if isinstance(videos, (str, Path)):
            videos = {DEFAULT_CAMERA: videos}
        if not isinstance(videos, dict):
            raise DatasetError(
                f"videos must be a path (single camera) or a dict of "
                f"camera name -> path, got {type(videos).__name__}"
            )
        out: Dict[str, str] = {}
        for camera, path in videos.items():
            err = validate_camera_name(camera)
            if err:
                raise DatasetError(err)
            out[camera] = str(path)
        return out

    def _normalize_joints(self, joints_pose: Any, primary: str) -> Dict[str, Dict[str, Any]]:
        """``joints_pose`` as ``{camera: doc}``. A bare doc (or JSON path)
        binds to the PRIMARY camera. Unambiguous discriminator: every joints
        doc has a ``frames`` key; a camera map never does."""
        if joints_pose is None:
            return {}
        if isinstance(joints_pose, dict) and "frames" not in joints_pose:
            out: Dict[str, Dict[str, Any]] = {}
            for camera, doc in joints_pose.items():
                err = validate_camera_name(camera)
                if err:
                    raise DatasetError(err)
                out[camera] = _load_annotation(
                    doc, f"joints_pose[{camera!r}]", validate_joints_pose
                )
            return out
        return {primary: _load_annotation(joints_pose, "joints_pose", validate_joints_pose)}

    @staticmethod
    def _validate_meta_arg(meta: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """The ``meta=`` whitelist: episode labels the contract defines.
        Everything else in the episode_meta row is SDK-assembled; custom
        per-episode data goes to x_ tracks, not meta."""
        if meta is None:
            return None
        if not isinstance(meta, dict):
            raise DatasetError(f"meta must be a dict, got {type(meta).__name__}")
        unknown = set(meta) - set(META_KEYS)
        if unknown:
            raise DatasetError(
                f"unknown meta key(s) {sorted(unknown)} — meta accepts {list(META_KEYS)}. "
                f"Custom per-episode data belongs on an x_ track (ds.add_track)."
            )
        return {k: v for k, v in meta.items() if v is not None}

    def _probe_cameras(self, cams: Dict[str, str]) -> Dict[str, ProbeResult]:
        probes: Dict[str, ProbeResult] = {}
        for camera, video in cams.items():
            src = probes[camera] = probe(video)
            if src.duration_sec >= MAX_EPISODE_SECONDS:
                raise DatasetError(
                    f"camera '{camera}' ({os.path.basename(video)}) is "
                    f"{src.duration_sec:.0f}s — the per-episode limit is "
                    f"{MAX_EPISODE_SECONDS}s (one-hour anchor slot). Split it first."
                )
        return probes

    def _ensure_camera_tracks(self, camera: str) -> None:
        for field in (preview_track(camera), raw_track(camera)):
            try:
                self._ds.add_video(field, mime="h264", required=False)
            except Exception as e:
                if "already exists" not in str(e):
                    raise

    def _ensure_joints_track(self, camera: str) -> None:
        try:
            self._ds.add_image(joints_track(camera), mime="json", required=False)
        except Exception as e:
            if "already exists" not in str(e):
                raise

    def _check_camera_geometry(
        self, camera: str, src: ProbeResult, existing: List[Dict[str, Any]], video: str
    ) -> None:
        """Aspect-ratio pre-check, per camera track, at the DATASET's profile
        height (never a per-call height — that was a latent bug). Different
        cameras are different tracks, so mixed geometries coexist."""
        height = int(self._encoding["preview_height"])
        ref = None
        for r in existing:
            cam = (r.get("cameras") or {}).get(camera)
            if cam and cam.get("width") and cam.get("height"):
                ref = cam
                break
        if ref is None:
            return
        have = _preview_width(int(ref["width"]), int(ref["height"]), height)
        want = _preview_width(src.width, src.height, height)
        if have != want:
            raise DatasetError(
                f"aspect ratio mismatch on camera '{camera}': its playback track is "
                f"{have}x{height} (from {ref['width']}x{ref['height']} sources), but "
                f"{os.path.basename(video)} ({src.width}x{src.height}) would encode to "
                f"{want}x{height}. All clips on one camera track must share an aspect "
                f"ratio — use another camera name, or pad/crop the source first."
            )

    def _source_fields(self, video: str) -> Dict[str, str]:
        """The source-location fields for one camera's meta: ``source_rel``
        (last two path components — joins with embed's ``source_dir=``, and
        disambiguates fixed-filename rigs) always; absolute ``source_uri``
        only on non-public datasets (paths leak the uploader's machine)."""
        p = Path(video).resolve()
        out = {"source_rel": "/".join(p.parts[-2:])}
        if not self._public:
            out["source_uri"] = str(p)
        return out

    def _resolve_source(
        self, cam_meta: Dict[str, Any], video_path: Optional[str], source_dir: Optional[str]
    ) -> Optional[str]:
        """Where the camera's source file is now: explicit override, then
        ``source_dir/source_rel``, then the recorded absolute path."""
        if video_path:
            return video_path if os.path.exists(video_path) else None
        rel = cam_meta.get("source_rel")
        if source_dir and rel:
            cand = os.path.join(source_dir, rel)
            if os.path.exists(cand):
                return cand
        uri = cam_meta.get("source_uri")
        if uri and os.path.exists(uri):
            return uri
        return None

    def _ingest_camera(
        self, camera: str, video: str, src: ProbeResult, *, anchor: int, raw: bool
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Transcode + ingest one camera's clip into its track pair, using
        the dataset's encoding profile. Returns ``(camera_meta, report)``.
        Playback first, archival second — an archival failure costs the
        archive, not the camera."""
        self._ensure_camera_tracks(camera)
        enc = self._encoding
        report: Dict[str, Any] = {}

        preview = fragment_video(
            video,
            anchor_ns=anchor,
            frag_seconds=float(enc["frag_seconds"]),
            scale_height=int(enc["preview_height"]),
            preview_fps=float(enc["preview_fps"]),
        )
        try:
            result = self._ds.ingest_cmaf(preview_track(camera), preview.init_path, preview.fragments)
            report["preview"] = {"fragments": len(preview.fragments), "ingest": result}
        finally:
            preview.cleanup()

        if raw:
            archival: Optional[FragmentedVideo] = None
            try:
                archival = fragment_video(
                    video, anchor_ns=anchor, frag_seconds=float(enc["frag_seconds"])
                )
                result = self._ds.ingest_cmaf(raw_track(camera), archival.init_path, archival.fragments)
                report["raw"] = {"fragments": len(archival.fragments), "ingest": result}
            except (FfmpegError, Exception) as e:
                if "init segment" in str(e):
                    warnings.warn(
                        f"skipped the archival track for camera '{camera}' — this clip's "
                        f"codec configuration differs from the one already in "
                        f"'{raw_track(camera)}'. A lossless track can only hold identically "
                        f"encoded clips. Playback and annotations are unaffected.",
                        stacklevel=3,
                    )
                    report["raw"] = None
                else:
                    raise
            finally:
                if archival is not None:
                    archival.cleanup()

        cam_meta = {
            "width": src.width,
            "height": src.height,
            "fps": src.fps,
            "duration_s": src.duration_sec,
            "codec": src.codec,
            **self._source_fields(video),
        }
        return cam_meta, report

    def _write_annotations(
        self,
        sample: Dict[str, Any],
        joints_by_cam: Dict[str, Dict[str, Any]],
        segments: Optional[Dict[str, Any]],
        out: Dict[str, Any],
    ) -> None:
        """Fold annotation blobs into the one committed sample row."""
        if joints_by_cam:
            for camera, joints in joints_by_cam.items():
                self._ensure_joints_track(camera)
                sample[joints_track(camera)] = json.dumps(joints).encode()
            out["joints_pose"] = {
                c: {"annotated_frames": len(j["frames"])} for c, j in joints_by_cam.items()
            }
        if segments is not None:
            sample[FIELD_SUBTASKS] = json.dumps(segments).encode()
            out["subtasks"] = {"segments": len(segments["labeled_subtasks"])}

    def add_episode(
        self,
        videos: Videos,
        *,
        episode_id: Optional[str] = None,
        joints_pose: Union[Annotation, Dict[str, Annotation]] = None,
        subtasks: Annotation = None,
        meta: Optional[Dict[str, Any]] = None,
        gid: Optional[int] = None,
        raw: bool = False,
    ) -> Episode:
        """Transcode one episode's camera video(s) into the dataset with its
        annotations, and return its :class:`Episode` handle (the ingest
        report is on ``.report``).

        ``videos``: a single path (one camera, stored as ``main``) or a dict
        of camera name → path; all cameras share the episode's slot, so
        multi-view playback is time-aligned by construction. ``joints_pose``
        binds per camera (bare doc → primary camera; dict → named cameras);
        ``subtasks`` is episode-level. ``meta`` takes the contract labels
        (``task``, ``scene``) — nothing is inferred. ``raw=True``
        additionally archives a lossless remux (roughly doubles the upload;
        best-effort on mixed sources).

        Encoding (height/fps/fragment length) comes from the dataset's
        profile — set at :meth:`create`, never per call.
        """
        cams = self._normalize_videos(videos)
        if not cams:
            raise DatasetError("add_episode needs at least one camera video")
        eid = episode_id or Path(next(iter(cams.values()))).stem
        primary = DEFAULT_CAMERA if DEFAULT_CAMERA in cams else next(iter(cams))

        # Annotations first: ffmpeg on a long episode is minutes of work, and
        # discovering a malformed input afterwards wastes all of it.
        joints_by_cam = self._normalize_joints(joints_pose, primary)
        segments = _load_annotation(subtasks, "subtasks", validate_subtasks)
        labels = self._validate_meta_arg(meta) or {}
        for camera in joints_by_cam:
            if camera not in cams:
                raise DatasetError(
                    f"joints_pose names camera '{camera}', but this episode's videos are "
                    f"{sorted(cams)} — joints bind to the camera whose pixel space they live in"
                )

        probes = self._probe_cameras(cams)

        for camera, joints in joints_by_cam.items():
            if not joints.get("src_fps"):
                continue
            p = probes[camera]
            drift = _fps_drift_frames(float(joints["src_fps"]), p.fps, p.duration_sec)
            if drift > 10:
                raise DatasetError(
                    f"the annotation's src_fps ({joints['src_fps']}) disagrees with camera "
                    f"'{camera}''s ({p.fps:.5f}) by {drift:.1f} frames over {p.duration_sec:.1f}s — "
                    f"these almost certainly describe different videos."
                )
            if drift > 1:
                warnings.warn(
                    f"annotation src_fps {joints['src_fps']} vs camera '{camera}' "
                    f"{p.fps:.5f} — {drift:.1f} frames of drift by the end of the clip",
                    stacklevel=2,
                )

        # ---- pick the slot ----
        self._invalidate()
        existing = self._rows()

        if gid is not None:
            if gid < 0:
                raise DatasetError(f"gid must be a non-negative integer, got {gid}")
            clash = next((r for r in existing if r["gid"] == gid), None)
            if clash:
                raise DatasetError(
                    f"slot {gid} is already taken by '{clash.get('episode_id')}' — omit gid to append"
                )
        else:
            # Slots are never reused: a slot is an episode's identity.
            gid = (max(r["gid"] for r in existing) + 1) if existing else 0

        dupe = next((r for r in existing if r.get("episode_id") == eid), None)
        if dupe:
            raise DatasetError(
                f"episode id '{eid}' is already in this dataset at slot {dupe['gid']} — "
                f"use ds.episode({eid!r}).add_cameras()/revise() to extend it, or pass "
                f"episode_id= to disambiguate"
            )

        for camera, video in cams.items():
            self._check_camera_geometry(camera, probes[camera], existing, video)

        anchor = base_anchor(gid)
        report: Dict[str, Any] = {"episode_id": eid, "gid": gid, "anchor": anchor, "cameras": {}}

        # Primary camera first: if a later camera fails, the episode's
        # annotated view is already playable.
        cameras_meta: Dict[str, Dict[str, Any]] = {}
        ordered = [primary] + [c for c in cams if c != primary]
        for camera in ordered:
            cam_meta, cam_report = self._ingest_camera(
                camera, cams[camera], probes[camera], anchor=anchor, raw=raw
            )
            cameras_meta[camera] = cam_meta
            report["cameras"][camera] = cam_report

        # ---- metadata + annotation blobs, one committed row ----
        pj = joints_by_cam.get(primary)
        row_meta = {
            "episode_id": eid,
            **labels,
            "primary_camera": primary,
            "cameras": cameras_meta,
            "src_fps": (pj or {}).get("src_fps", probes[primary].fps),
            "duration_s": max(p.duration_sec for p in probes.values()),
        }
        row_meta = {k: v for k, v in row_meta.items() if v is not None}

        sample: Dict[str, Any] = {"_anchor": anchor, FIELD_EPISODE_META: json.dumps(row_meta)}
        self._write_annotations(sample, joints_by_cam, segments, report)
        self._append_and_invalidate([sample])

        report["meta"] = row_meta
        return Episode(self, {"gid": gid, "anchor": anchor, **row_meta}, report=report)

    # ---- Search ----------------------------------------------------------

    @staticmethod
    def _clip_encoder():
        enc = _ENCODER_CACHE.get("clip")
        if enc is None:
            from dreamlake.encoders import ClipEncoder

            enc = _ENCODER_CACHE["clip"] = ClipEncoder()
        return enc

    @staticmethod
    def _text_encoder():
        enc = _ENCODER_CACHE.get("text")
        if enc is None:
            from dreamlake.encoders import TextEncoder

            enc = _ENCODER_CACHE["text"] = TextEncoder()
        return enc

    def _append_search_vectors(
        self,
        row: Dict[str, Any],
        frame_vecs: Optional[Sequence[Tuple[float, Any]]],
        subtask_vecs: Optional[Sequence[Tuple[float, Any, str]]],
    ) -> Dict[str, int]:
        base = row["anchor"]

        def _a(t: float) -> int:
            ns = round(float(t) * 1e9)
            if not (0 <= ns < EPISODE_STRIDE_NS):
                raise DatasetError(
                    f"t_sec {t} is outside the episode's slot (0..{MAX_EPISODE_SECONDS}s) — "
                    f"vector timestamps are seconds on the episode's own clock"
                )
            return base + ns

        # Validate EVERYTHING before appending anything: the two batches
        # commit separately, and a bad second batch must not strand the first.
        frame_rows = [
            {"_anchor": _a(t), FIELD_FRAME_VEC: vec}
            for t, vec in frame_vecs or []
        ]
        subtask_rows = [
            {
                "_anchor": _a(t),
                FIELD_SUBTASK_VEC: vec,
                FIELD_SUBTASK_LABEL: str(label),
            }
            for t, vec, label in subtask_vecs or []
        ]
        for batch in (frame_rows, subtask_rows):
            if not batch:
                continue
            try:
                self._ds.append_many(batch)
            except Exception as e:
                if "not in schema" in str(e):
                    raise DatasetError(
                        "this dataset has no search fields (older schema) — "
                        "re-create it with the current SDK to make it searchable"
                    ) from e
                raise
        if frame_rows or subtask_rows:
            self._vec_scan_cache.clear()
        return {
            "frame_vecs": len(frame_vecs or []),
            "subtask_vecs": len(subtask_vecs or []),
        }

    def embed_episodes(
        self,
        *,
        camera: Optional[str] = None,
        fps: float = 1.0,
        source_dir: Optional[str] = None,
        batch_size: int = 32,
    ) -> Dict[str, Any]:
        """Encode and upload search vectors for EVERY episode (the sweep;
        one episode is ``ds.episode(id).embed(...)``). Episodes commit one
        at a time — interrupt and re-run freely, uploads deduplicate.
        ``source_dir`` locates moved source files via each camera's
        ``source_rel``. Needs ``pip install "dreamlake[search]"``."""
        report: Dict[str, Any] = {}
        for epo in self.episodes():
            report[epo.episode_id] = epo.embed(
                camera=camera, fps=fps, source_dir=source_dir, batch_size=batch_size
            )
        return report

    def search(
        self,
        query: str,
        top_k: int = 10,
        kind: str = "both",
    ) -> List[Dict[str, Any]]:
        """Find episode moments by natural language. CLIP text tower against
        frame vectors and/or BGE against subtask texts, fused with
        reciprocal-rank fusion. Returns ``[{"episode_id", "time_sec",
        "score", "source", "subtask"?}]``. ``kind``: ``"frames"``,
        ``"subtasks"`` or ``"both"``. First call loads the encoders."""
        if kind not in ("frames", "subtasks", "both"):
            raise DatasetError('kind must be "frames", "subtasks" or "both"')
        try:
            import dreamlake.encoders  # noqa: F401 — the [search] gate
        except ImportError as e:
            raise DatasetError(str(e)) from e

        by_gid = {r["gid"]: r for r in self._rows()}
        RRF_K = 60.0
        merged: Dict[int, Dict[str, Any]] = {}

        def fold(hits_in: List[Tuple[int, Optional[str]]], source: str):
            for rank, (anchor, label) in enumerate(hits_in):
                row = by_gid.get(gid_of(anchor))
                if row is None:
                    continue
                hit = merged.get(anchor)
                if hit is None:
                    hit = {
                        "episode_id": row["episode_id"],
                        "time_sec": (anchor - row["anchor"]) / 1e9,
                        "score": 0.0,
                        "source": source,
                    }
                    merged[anchor] = hit
                hit["score"] += 1.0 / (RRF_K + rank + 1)
                if label is not None:
                    hit["subtask"] = label
                    hit["source"] = source

        if kind in ("frames", "both"):
            qv = self._clip_encoder().encode_text(query)
            fold(self._vector_hits(FIELD_FRAME_VEC, qv, top_k), "frame")

        if kind in ("subtasks", "both"):
            qv = self._text_encoder().encode(query)
            fold(
                self._vector_hits(FIELD_SUBTASK_VEC, qv, top_k, label_field=FIELD_SUBTASK_LABEL),
                "subtask",
            )

        hits = sorted(merged.values(), key=lambda h: h["score"], reverse=True)
        return hits[:top_k]

    # ---- Blob + vector internals ----------------------------------------

    def _read_blob(self, field: str, gid: int) -> Optional[bytes]:
        """The blob field's bytes for one episode — the HIGHEST-anchor version
        within the revision window — or None when absent (an unannotated
        camera / never-created track is normal, not an error)."""
        a, v = self._latest_in_window(field, base_anchor(gid))
        return bytes(v) if v is not None else None

    def _latest_in_window(self, field: str, start: int) -> Tuple[int, Any]:
        """``(anchor, value)`` of the newest revision of ``field`` in
        ``[start, start + REVISION_WINDOW_NS)`` — ``(start - 1, None)`` when
        absent (so ``anchor + 1`` is always the next free write anchor)."""
        best_a, best_v = start - 1, None
        try:
            batches = list(self._ds.iter_all_batches(
                fields=[field], start_ns=start, end_ns=start + REVISION_WINDOW_NS
            ))
        except Exception as e:
            if "no FieldTrack" in str(e) or "not in schema" in str(e):
                return best_a, None
            raise
        for batch in batches:
            anchors = batch.get("_time_anchors") or []
            values = batch.get(field) or []
            for a, v in zip(anchors, values):
                if v is not None and int(a) >= best_a:
                    best_a, best_v = int(a), v
        return best_a, best_v

    def _next_revision_anchor(self, row: Dict[str, Any], fields: List[str]) -> int:
        """The write anchor for a revision touching ``fields``: one past the
        newest existing version of ANY of them (and of the meta row), still
        inside the revision window."""
        base = row["anchor"]
        nxt = max(int(row.get("_rev", base)),
                  *[self._latest_in_window(f, base)[0] for f in fields] or [base - 1]) + 1
        if nxt >= base + REVISION_WINDOW_NS:
            raise DatasetError(
                f"episode '{row.get('episode_id')}' has exhausted its "
                f"{REVISION_WINDOW_NS} revisions — re-ingest it as a new episode"
            )
        return max(nxt, base)

    def _vector_hits(
        self,
        field: str,
        qvec: Any,
        top_k: int,
        label_field: Optional[str] = None,
    ) -> List[Tuple[int, Optional[str]]]:
        """Ranked ``(anchor, label?)`` for one vector field. ANN first, exact
        scan when ANN under-returns — LSH cells are sparse exactly when the
        corpus is small enough that a numpy scan is trivial."""
        import numpy as np

        fields = [label_field] if label_field else []
        try:
            batches = self._ds.iter_vector(
                field, qvec.tolist(), top_k=top_k, nprobe=256, fields=fields
            )
        except Exception as e:
            msg = str(e)
            if "no FieldTrack" in msg or "not in schema" in msg:
                raise DatasetError(
                    "this dataset has no search fields (older schema) — "
                    "re-create it with the current SDK to make it searchable"
                ) from e
            batches = []

        out: List[Tuple[int, Optional[str]]] = []
        for batch in batches:
            anchors = batch.get("_time_anchors") or []
            labels = batch.get(label_field) if label_field else None
            for i, a in enumerate(anchors):
                out.append((int(a), labels[i] if labels else None))
        if len(out) >= top_k:
            return out[:top_k]

        cached = self._vec_scan_cache.get(field)
        if cached is None:
            anchors_l: List[int] = []
            vecs: List[Any] = []
            labels_l: List[Optional[str]] = []
            read_fields = [field] + ([label_field] if label_field else [])
            for batch in self._ds.iter_all_batches(fields=read_fields, batch_size=2048):
                b_anchors = batch.get("_time_anchors") or []
                b_vecs = batch.get(field) or []
                b_labels = batch.get(label_field) if label_field else None
                for i, a in enumerate(b_anchors):
                    v = b_vecs[i] if i < len(b_vecs) else None
                    if v is None:
                        continue
                    anchors_l.append(int(a))
                    vecs.append(v)
                    labels_l.append(b_labels[i] if b_labels else None)
            matrix = (
                np.asarray(vecs, dtype=np.float32)
                if vecs
                else np.zeros((0, 1), dtype=np.float32)
            )
            cached = (anchors_l, matrix, labels_l)
            self._vec_scan_cache[field] = cached

        anchors_l, matrix, labels_l = cached
        if matrix.shape[0] == 0:
            return out
        q = np.asarray(qvec, dtype=np.float32)
        # Vectors are L2-normalized by the encoders, so dot product == cosine.
        sims = matrix @ q
        order = np.argsort(-sims)[:top_k]
        return [(anchors_l[i], labels_l[i]) for i in order]
