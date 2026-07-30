"""``dreamlake.dataset.Dataset`` — robot-training datasets on DreamDB.

The write path a labeling pipeline calls once per processed video::

    from dreamlake.dataset import Dataset

    ds = Dataset.create("wash-the-dishes")          # platform bucket (default)
    ds.add_video(
        video="Ceramics.mov",
        video_id="Ceramics",
        joints_pose=joints_dict,   # your pipeline's per-frame joints output
        subtasks=subtasks_dict,    # your pipeline's action segmentation
    )
    ds.embed_videos()                               # make it searchable
    ds.search("hands rinsing a bowl")

Storage: one DreamDB Space per dataset, one timeline. Videos occupy disjoint
one-hour anchor slots (see ``_schema``). The layout is shared byte-for-byte
with the TypeScript CLI, so ``dreamlake dataset ls`` and the web viewer read
datasets written here, and vice versa.

Backends: by default the dataset lives in the DreamLake platform bucket
(``Dataset.create("name")`` — server issues scoped credentials; requires
``dreamlake login`` or ``DREAMLAKE_API_KEY``). Pass ``backend=`` to write
anywhere dreamdb can reach instead: ``file:///path`` for a directory (serve
it statically and the browser reads it) or an ``https://…`` S3 URL with your
own credentials in the environment.

This preset is a thin layer over ``dreamlake.db`` — the same datasets are
fully readable and writable through the bare re-exported dreamdb API.
"""

from __future__ import annotations

import json
import os
import warnings
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from ._ffmpeg import FfmpegError, FragmentedVideo, fragment_video, probe
from ._schema import (
    DATASET_REF,
    DATASET_SCHEMA_TYPE,
    DEFAULT_PREVIEW_FPS,
    FIELD_FRAME_VEC,
    FIELD_JOINTS_POSE,
    FIELD_SUBTASK_LABEL,
    FIELD_SUBTASK_VEC,
    FIELD_SUBTASKS,
    FIELD_VIDEO_META,
    FIELD_VIDEO_PREVIEW,
    FIELD_VIDEO_RAW,
    FRAME_VEC_DIM,
    MAX_VIDEO_SECONDS,
    SUBTASK_VEC_DIM,
    VIDEO_STRIDE_NS,
    base_anchor,
    build_schema,
    gid_of,
    validate_joints_pose,
    validate_subtasks,
)

Annotation = Union[Dict[str, Any], str, Path, None]


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
    since the viewer maps playback time to a frame index. 0.3 frames is
    harmless; 48 frames is a different video."""
    return abs(ann_fps - video_fps) * duration_sec


_ENCODER_CACHE: Dict[str, Any] = {}


def _clip_encoder():
    """Process-wide encoder singletons. A CLIP load is ~7 s; paying it once
    per search() call would make interactive use miserable."""
    enc = _ENCODER_CACHE.get("clip")
    if enc is None:
        from dreamlake.encoders import ClipEncoder

        enc = _ENCODER_CACHE["clip"] = ClipEncoder()
    return enc


def _text_encoder():
    enc = _ENCODER_CACHE.get("text")
    if enc is None:
        from dreamlake.encoders import TextEncoder

        enc = _ENCODER_CACHE["text"] = TextEncoder()
    return enc


def _check_schema_type(inner, where: str) -> None:
    """Refuse to interpret a space written under a DIFFERENT schemaType as a
    robot dataset — the slot conventions and blob shapes would be garbage.
    A space with no stamp at all is accepted: TS-CLI-created datasets predate
    the meta, and the field layout check happens naturally on first read."""
    try:
        meta = inner.meta() or {}
    except Exception:
        return
    stamped = meta.get("dreamdb.schema_type")
    if stamped and stamped != DATASET_SCHEMA_TYPE:
        raise DatasetError(
            f"{where} holds schemaType '{stamped}', not '{DATASET_SCHEMA_TYPE}' — "
            f"open it with dreamlake.db instead of the robot-dataset preset"
        )


class Dataset:
    """One robot-training dataset: many videos plus their annotations."""

    def __init__(self, inner, backend: str, name: Optional[str] = None):
        self._ds = inner
        self.backend = backend
        self.name = name
        # Exact-scan matrices for search(), per vector field. Invalidated on
        # every add_search_vectors — see _vector_hits.
        self._vec_scan_cache: Dict[str, Any] = {}

    # ---- Construction --------------------------------------------------
    #
    # Both constructors are thin over `dreamlake.db`: platform mode (name)
    # brokers scoped credentials from dreamlake-server and writes the catalog
    # row; backend mode writes anywhere dreamdb can reach. Creation is
    # deliberately not idempotent — a typo'd name/path cannot silently fork a
    # second history.

    @classmethod
    def create(
        cls,
        name: Optional[str] = None,
        *,
        backend: Optional[str] = None,
        visibility: Optional[str] = None,
    ) -> "Dataset":
        """Create an empty dataset.

        ``Dataset.create("wash-the-dishes")`` puts it in the DreamLake
        platform bucket (needs ``dreamlake login`` or ``DREAMLAKE_API_KEY``).
        ``Dataset.create(backend="file:///data/x")`` writes to any dreamdb
        backend instead.
        """
        from dreamlake import db

        if backend is None and not name:
            raise DatasetError(
                "Dataset.create needs a name (platform mode) or backend= (self-hosted)"
            )
        if backend is not None:
            # Refuse an existing space, mirroring the platform's 409.
            try:
                db.open(backend=backend)
            except Exception:
                pass  # nothing there — the good case
            else:
                raise DatasetError(
                    f"a dataset already exists at {backend} — use Dataset.open() to add to it"
                )

        try:
            inner = db.create(
                name,
                build_schema(),
                backend=backend,
                schema_type=DATASET_SCHEMA_TYPE,
                visibility=visibility,
            )
        except db.DatasetExistsError as e:
            raise DatasetError(str(e)) from e
        lease = getattr(inner, "dreamlake_lease", None)
        return cls(inner, backend or (lease or {}).get("backend_url", ""), name)

    @classmethod
    def open(cls, name: Optional[str] = None, *, backend: Optional[str] = None) -> "Dataset":
        """Open an existing dataset by platform name or by backend URI."""
        from dreamlake import db

        if backend is None and not name:
            raise DatasetError(
                "Dataset.open needs a name (platform mode) or backend= (self-hosted)"
            )
        try:
            inner = db.open(name, backend=backend)
        except db.DatasetNotFoundError as e:
            raise DatasetError(str(e)) from e
        except Exception as e:
            if backend is not None:
                raise DatasetError(
                    f"no dataset at {backend} — create one with Dataset.create() ({e})"
                ) from e
            raise
        _check_schema_type(inner, backend or f"dataset '{name}'")
        lease = getattr(inner, "dreamlake_lease", None)
        return cls(inner, backend or (lease or {}).get("backend_url", ""), name)

    # ---- Read path ------------------------------------------------------

    def videos(self) -> List[Dict[str, Any]]:
        """Every video in the dataset, ordered by slot.

        Returns one dict per video: ``{"gid", "anchor", **video_meta}``.
        One column read serves the whole listing — the reason ``video_meta``
        is a single JSON column instead of eight scalar tracks.
        """
        rows: List[Dict[str, Any]] = []
        for batch in self._ds.iter_all_batches(fields=[FIELD_VIDEO_META]):
            anchors = batch.get("_time_anchors") or []
            values = batch.get(FIELD_VIDEO_META) or []
            for anchor, value in zip(anchors, values):
                try:
                    meta = json.loads(value)
                except (TypeError, json.JSONDecodeError):
                    # Surface an unparseable row rather than dropping the
                    # video from the list with no indication it exists.
                    meta = {"video_id": f"<unparseable @ {anchor}>"}
                rows.append({"gid": gid_of(int(anchor)), "anchor": int(anchor), **meta})
        rows.sort(key=lambda r: r["gid"])
        return rows

    def _read_blob(self, field: str, gid: int) -> Optional[bytes]:
        """The blob field's bytes for one video, or None when absent (a video
        ingested without that annotation is normal, not an error)."""
        start = base_anchor(gid)
        for batch in self._ds.iter_all_batches(
            fields=[field], start_ns=start, end_ns=start + VIDEO_STRIDE_NS
        ):
            for value in batch.get(field) or []:
                if value is not None:
                    return bytes(value)
        return None

    def read_joints_pose(self, video_id: str) -> Optional[Dict[str, Any]]:
        """The joints_pose document for one video, or None."""
        row = self._require_video(video_id)
        blob = self._read_blob(FIELD_JOINTS_POSE, row["gid"])
        return json.loads(blob) if blob else None

    def read_subtasks(self, video_id: str) -> Optional[Dict[str, Any]]:
        """The subtasks document for one video, or None."""
        row = self._require_video(video_id)
        blob = self._read_blob(FIELD_SUBTASKS, row["gid"])
        return json.loads(blob) if blob else None

    def info(self, video_id: str) -> Dict[str, Any]:
        """One video's metadata plus annotation summaries — enough to verify
        an ingest landed correctly without opening a browser."""
        row = self._require_video(video_id)
        out: Dict[str, Any] = dict(row)

        joints_blob = self._read_blob(FIELD_JOINTS_POSE, row["gid"])
        if joints_blob:
            joints = json.loads(joints_blob)
            out["joints_pose"] = {"annotated_frames": len(joints.get("frames", {}))}
        subtasks_blob = self._read_blob(FIELD_SUBTASKS, row["gid"])
        if subtasks_blob:
            subtasks = json.loads(subtasks_blob)
            segs = subtasks.get("labeled_subtasks", [])
            out["subtasks"] = {
                "segments": len(segs),
                "ends_at_sec": segs[-1]["end_sec"] if segs else 0,
            }
        return out

    def _require_video(self, video_id: str) -> Dict[str, Any]:
        rows = self.videos()
        for row in rows:
            if row.get("video_id") == video_id or str(row["gid"]) == str(video_id):
                return row
        have = ", ".join(str(r.get("video_id")) for r in rows) or "none"
        raise DatasetError(f"no video '{video_id}' in this dataset (have: {have})")

    # ---- Write path ------------------------------------------------------

    def add_video(
        self,
        video: Union[str, Path],
        *,
        video_id: Optional[str] = None,
        joints_pose: Annotation = None,
        subtasks: Annotation = None,
        task: Optional[str] = None,
        scene: Optional[str] = None,
        gid: Optional[int] = None,
        preview_height: int = 720,
        preview_fps: float = DEFAULT_PREVIEW_FPS,
        frag_seconds: float = 2.0,
        raw: bool = True,
    ) -> Dict[str, Any]:
        """Transcode one video into the dataset with its annotations.

        ``joints_pose`` and ``subtasks`` accept the pipeline's in-memory dicts
        directly (or paths to JSON files); both are optional. Returns a
        summary dict (slot, fragment counts, annotation counts).

        Two encodes happen: a normalized H.264 playback track (what the
        browser streams) and, when ``raw=True``, a lossless archival remux.
        The archival track is best-effort — being lossless it carries the
        source's own codec configuration, and a video field only holds one,
        so mixed-source datasets keep playback and lose the shared archive.
        """
        video = str(video)

        # Annotations first: ffmpeg on a long video is minutes of work, and
        # discovering a malformed file afterwards wastes all of it.
        joints = _load_annotation(joints_pose, "joints_pose", validate_joints_pose)
        segments = _load_annotation(subtasks, "subtasks", validate_subtasks)

        src = probe(video)

        if src.duration_sec >= MAX_VIDEO_SECONDS:
            raise DatasetError(
                f"{os.path.basename(video)} is {src.duration_sec:.0f}s — the per-video limit is "
                f"{MAX_VIDEO_SECONDS}s (each video occupies a one-hour anchor slot). Split it first."
            )

        if joints and joints.get("src_fps"):
            drift = _fps_drift_frames(float(joints["src_fps"]), src.fps, src.duration_sec)
            if drift > 10:
                raise DatasetError(
                    f"the annotation's src_fps ({joints['src_fps']}) disagrees with the video's "
                    f"({src.fps:.5f}) by {drift:.1f} frames over {src.duration_sec:.1f}s — these "
                    f"almost certainly describe different videos. Overlays would be visibly out of sync."
                )
            if drift > 1:
                warnings.warn(
                    f"annotation src_fps {joints['src_fps']} vs video {src.fps:.5f} — "
                    f"{drift:.1f} frames of drift by the end of the clip",
                    stacklevel=2,
                )

        # ---- pick the slot ----
        existing = self.videos()
        vid = video_id or Path(video).stem

        if gid is not None:
            if gid < 0:
                raise DatasetError(f"gid must be a non-negative integer, got {gid}")
            clash = next((r for r in existing if r["gid"] == gid), None)
            if clash:
                raise DatasetError(
                    f"slot {gid} is already taken by '{clash.get('video_id')}' — omit gid to append"
                )
        else:
            # One past the highest slot in use. Slots are never reused even if
            # an earlier one is free: a slot is a video's identity, and
            # handing it to a second video would merge it with whatever
            # fragments the first left behind.
            gid = (max(r["gid"] for r in existing) + 1) if existing else 0

        dupe = next((r for r in existing if r.get("video_id") == vid), None)
        if dupe:
            raise DatasetError(
                f"video id '{vid}' is already in this dataset at slot {dupe['gid']} — "
                f"pass video_id= to disambiguate"
            )

        # Aspect-ratio pre-check. Every clip on the playback track must share
        # one init segment, and the init encodes the frame size — which
        # `scale=-2:H` derives from the SOURCE's aspect ratio. A 4:3 video can
        # never join a 16:9 dataset at any height. Catch it here, before
        # minutes of transcoding, with the numbers that explain it.
        first = next((r for r in existing if r.get("width") and r.get("height")), None)
        if first:
            have = _preview_width(int(first["width"]), int(first["height"]), preview_height)
            want = _preview_width(src.width, src.height, preview_height)
            if have != want:
                raise DatasetError(
                    f"aspect ratio mismatch: this dataset's playback track is {have}x{preview_height} "
                    f"(from {first['width']}x{first['height']} sources), but {os.path.basename(video)} "
                    f"({src.width}x{src.height}) would encode to {want}x{preview_height}. All videos in "
                    f"one dataset must share an aspect ratio — keep one dataset per camera geometry, "
                    f"or pad/crop the source first."
                )

        anchor = base_anchor(gid)
        out: Dict[str, Any] = {"video_id": vid, "gid": gid, "anchor": anchor}

        # ---- fragment + ingest ----
        # Playback first, archival second: the archival pass is the one that
        # can fail on mixed-source datasets, and ordering it second means that
        # failure costs the archive, not the video.
        preview = fragment_video(
            video,
            anchor_ns=anchor,
            frag_seconds=frag_seconds,
            scale_height=preview_height,
            preview_fps=preview_fps,
        )
        try:
            result = self._ds.ingest_cmaf(FIELD_VIDEO_PREVIEW, preview.init_path, preview.fragments)
            out["video_preview"] = {"fragments": len(preview.fragments), "ingest": result}
        finally:
            preview.cleanup()

        if raw:
            archival: Optional[FragmentedVideo] = None
            try:
                archival = fragment_video(video, anchor_ns=anchor, frag_seconds=frag_seconds)
                result = self._ds.ingest_cmaf(FIELD_VIDEO_RAW, archival.init_path, archival.fragments)
                out["video_raw"] = {"fragments": len(archival.fragments), "ingest": result}
            except (FfmpegError, Exception) as e:
                if "init segment" in str(e):
                    # Expected whenever a dataset holds footage from more than
                    # one rig. Say what it means, not the protocol message.
                    warnings.warn(
                        "skipped the archival track — this video's codec configuration differs "
                        "from the one already in 'video_raw'. A lossless track can only hold "
                        "clips that were encoded identically. Playback and annotations are "
                        "unaffected; pass raw=False to stop trying.",
                        stacklevel=2,
                    )
                    out["video_raw"] = None
                else:
                    raise
            finally:
                if archival is not None:
                    archival.cleanup()

        # ---- metadata + annotation blobs, one committed row ----
        meta = {
            "video_id": vid,
            "source_uri": os.path.abspath(video),
            "task": task or (segments.get("task") if segments else None),
            "scene": scene,
            "src_fps": (joints or {}).get("src_fps", src.fps),
            "width": (joints or {}).get("width", src.width),
            "height": (joints or {}).get("height", src.height),
            "total_frames": (joints or {}).get("total_frames"),
            "duration_s": src.duration_sec,
        }
        meta = {k: v for k, v in meta.items() if v is not None}

        sample: Dict[str, Any] = {"_anchor": anchor, FIELD_VIDEO_META: json.dumps(meta)}
        if joints is not None:
            sample[FIELD_JOINTS_POSE] = json.dumps(joints).encode()
            out["joints_pose"] = {"annotated_frames": len(joints["frames"])}
        if segments is not None:
            sample[FIELD_SUBTASKS] = json.dumps(segments).encode()
            out["subtasks"] = {"segments": len(segments["labeled_subtasks"])}
        self._ds.append_many([sample])

        out["meta"] = meta
        return out

    # ---- Natural-language search ----------------------------------------
    #
    # The schema declares the vector fields with LSH indexes, which DreamDB
    # maintains on append — so a vector is searchable the moment it lands.
    # There is no separate index-build phase. `add_search_vectors` is the
    # pure-upload half (bring your own vectors); `embed_videos` is the
    # convenience that runs the encoders and then uploads.

    def add_search_vectors(
        self,
        video_id: str,
        *,
        frame_vecs: Optional[Sequence[Tuple[float, Any]]] = None,
        subtask_vecs: Optional[Sequence[Tuple[float, Any, str]]] = None,
    ) -> Dict[str, int]:
        """Upload search vectors you computed yourself.

        ``frame_vecs``: ``[(t_sec, vec512)]`` — one CLIP-space vector per
        sampled frame, timestamped on the video's own clock.
        ``subtask_vecs``: ``[(t_sec, vec384, label)]`` — one BGE-space vector
        per segment, timestamped at the segment start; ``label`` is the text
        shown when the segment is a search hit.

        Vectors become searchable immediately. Re-uploading the same
        ``(t_sec, vec)`` is deduplicated by content addressing.
        """
        row = self._require_video(video_id)
        base = row["anchor"]

        # Two separate appends on purpose: within one append_many call every
        # sample must carry the SAME field set (frame rows and subtask rows
        # differ), and dreamdb rejects mixed batches.
        frame_rows = [
            {"_anchor": base + round(float(t_sec) * 1e9), FIELD_FRAME_VEC: vec}
            for t_sec, vec in frame_vecs or []
        ]
        subtask_rows = [
            {
                "_anchor": base + round(float(t_sec) * 1e9),
                FIELD_SUBTASK_VEC: vec,
                FIELD_SUBTASK_LABEL: str(label),
            }
            for t_sec, vec, label in subtask_vecs or []
        ]

        for batch in (frame_rows, subtask_rows):
            if not batch:
                continue
            try:
                self._ds.append_many(batch)
            except Exception as e:
                if "not in schema" in str(e):
                    raise DatasetError(
                        "this dataset predates schema v2 (no search fields) — "
                        "re-create it with the current SDK to make it searchable"
                    ) from e
                raise
        if frame_rows or subtask_rows:
            self._vec_scan_cache.clear()
        return {
            "frame_vecs": len(frame_vecs or []),
            "subtask_vecs": len(subtask_vecs or []),
        }

    def embed_videos(
        self,
        video_id: Optional[str] = None,
        *,
        fps: float = 1.0,
        video_path: Optional[str] = None,
        batch_size: int = 32,
    ) -> Dict[str, Any]:
        """Encode and upload search vectors for one video (or every video).

        Frames are sampled at ``fps`` from the SOURCE file and encoded with
        CLIP; subtask segment texts are encoded with BGE. The source file is
        found via the ``source_uri`` recorded at ingest — pass ``video_path=``
        when the dataset moved machines. Videos are committed one at a time,
        so an interrupted run resumes by simply re-running (already-uploaded
        vectors deduplicate).

        Needs the search extra: ``pip install "dreamlake[search]"``.
        """
        try:
            from dreamlake.encoders import iter_video_frames
        except ImportError as e:
            raise DatasetError(str(e)) from e

        if video_path is not None and video_id is None:
            raise DatasetError("video_path= only makes sense with a specific video_id")

        rows = [self._require_video(video_id)] if video_id else self.videos()
        clip = _clip_encoder()
        text = _text_encoder()
        report: Dict[str, Any] = {}

        for row in rows:
            vid = row["video_id"]
            src = video_path if (video_path and vid == video_id) else row.get("source_uri")

            frame_vecs: List[Tuple[float, Any]] = []
            if src and os.path.exists(src):
                stamps: List[float] = []
                images = []
                for t_sec, img in iter_video_frames(src, fps=fps):
                    stamps.append(t_sec)
                    images.append(img)
                    if len(images) == batch_size:
                        for s, v in zip(stamps, clip.encode_images(images)):
                            frame_vecs.append((s, v))
                        stamps, images = [], []
                if images:
                    for s, v in zip(stamps, clip.encode_images(images)):
                        frame_vecs.append((s, v))
            else:
                warnings.warn(
                    f"'{vid}': source file not found ({src or 'no source_uri'}) — "
                    f"skipping frame vectors; pass video_path= to supply it",
                    stacklevel=2,
                )

            subtask_vecs: List[Tuple[float, Any, str]] = []
            segments = self.read_subtasks(vid)
            if segments:
                segs = segments.get("labeled_subtasks", [])
                if segs:
                    labels = [s["subtask"] for s in segs]
                    vecs = text.encode(labels)
                    subtask_vecs = [
                        (float(seg["start_sec"]), vec, label)
                        for seg, vec, label in zip(segs, vecs, labels)
                    ]

            counts = self.add_search_vectors(
                vid, frame_vecs=frame_vecs, subtask_vecs=subtask_vecs
            )
            report[vid] = counts
        return report

    def search(
        self,
        query: str,
        top_k: int = 10,
        kind: str = "both",
    ) -> List[Dict[str, Any]]:
        """Find video moments by natural language.

        Runs the query through CLIP's text tower against frame vectors and/or
        through BGE against subtask-text vectors, then fuses the two rankings
        with reciprocal-rank fusion. Returns
        ``[{"video_id", "time_sec", "score", "source", "subtask"?}]`` sorted
        by score. ``kind`` is ``"frames"``, ``"subtasks"`` or ``"both"``.

        First call loads the encoder models (a few seconds; cached after).
        """
        if kind not in ("frames", "subtasks", "both"):
            raise DatasetError('kind must be "frames", "subtasks" or "both"')
        try:
            import dreamlake.encoders  # noqa: F401 — the [search] gate
        except ImportError as e:
            raise DatasetError(str(e)) from e

        by_gid = {r["gid"]: r for r in self.videos()}
        RRF_K = 60.0
        merged: Dict[int, Dict[str, Any]] = {}

        def fold(hits_in: List[Tuple[int, Optional[str]]], source: str):
            for rank, (anchor, label) in enumerate(hits_in):
                row = by_gid.get(gid_of(anchor))
                if row is None:
                    continue  # a vector whose video row vanished — skip, don't crash
                hit = merged.get(anchor)
                if hit is None:
                    hit = {
                        "video_id": row["video_id"],
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
            qv = _clip_encoder().encode_text(query)
            fold(self._vector_hits(FIELD_FRAME_VEC, qv, top_k), "frame")

        if kind in ("subtasks", "both"):
            qv = _text_encoder().encode(query)
            fold(
                self._vector_hits(FIELD_SUBTASK_VEC, qv, top_k, label_field=FIELD_SUBTASK_LABEL),
                "subtask",
            )

        hits = sorted(merged.values(), key=lambda h: h["score"], reverse=True)
        return hits[:top_k]

    def _vector_hits(
        self,
        field: str,
        qvec: Any,
        top_k: int,
        label_field: Optional[str] = None,
    ) -> List[Tuple[int, Optional[str]]]:
        """Ranked ``(anchor, label?)`` for one vector field.

        ANN first (cheap at scale), exact scan when ANN under-returns. The
        two regimes are complementary: LSH cells are sparse exactly when the
        corpus is small — which is exactly when reading every vector and
        doing the cosine in numpy is trivial. Big corpora fill their cells,
        ANN returns a full top-k, and the fallback never runs.
        """
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
                    "this dataset predates schema v2 (no search fields) — "
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

        # Exact scan. Cached per field: the whole point of falling back is
        # that the corpus is small, so holding it in memory is nothing.
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
