"""``Episode`` — the identity cursor over one episode of a dataset.

An episode's identity triple ``(episode_id, gid, anchor)`` is immutable
forever (slots are never reused, episodes never deleted), so a handle that
caches only identity cannot dangle. The meta snapshot it carries is a READ
convenience: every write method re-reads the meta row at call time and
never consumes the cache — a stale handle can therefore never clobber a
newer revision.
"""

from __future__ import annotations

import json
import os
import warnings
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ._ffmpeg import probe
from ._fields import dumps_compact
from ._schema import (
    DEFAULT_CAMERA,
    EPISODE_STRIDE_NS,
    FIELD_EPISODE_META,
    FIELD_SUBTASKS,
    MAX_EPISODE_SECONDS,
    META_KEYS,
    USER_TRACK_RE,
    joints_track,
    validate_subtasks,
)


class Episode:
    """One episode of a :class:`Dataset`: identity + meta snapshot + the
    episode-scoped verbs. Obtained from ``ds.add_episode(...)``,
    ``ds.episode(id)`` or ``ds.episodes()`` — never constructed directly."""

    def __init__(self, dataset, row: Dict[str, Any], report: Optional[Dict[str, Any]] = None):
        self._d = dataset
        self._row = row
        #: Ingest report — populated only on the handle add_episode returns.
        self.report = report

    # ---- identity (immutable) -------------------------------------------

    @property
    def dataset(self):
        return self._d

    @property
    def episode_id(self) -> str:
        return self._row.get("episode_id")

    @property
    def gid(self) -> int:
        return self._row["gid"]

    @property
    def anchor(self) -> int:
        return self._row["anchor"]

    # ---- meta snapshot (read convenience; writes never consume it) ------

    @property
    def meta(self) -> Dict[str, Any]:
        """The episode_meta row content, as of the last fetch/refresh."""
        return {k: v for k, v in self._row.items() if k not in ("gid", "anchor", "_rev")}

    @property
    def cameras(self) -> Dict[str, Any]:
        return dict(self._row.get("cameras") or {})

    @property
    def task(self) -> Optional[str]:
        return self._row.get("task")

    @property
    def scene(self) -> Optional[str]:
        return self._row.get("scene")

    @property
    def duration_s(self) -> float:
        return float(self._row.get("duration_s") or 0.0)

    def refresh(self) -> "Episode":
        """Re-read the meta row and update the snapshot. Returns self."""
        self._d._invalidate()
        self._row = self._d._require_row(self.episode_id)
        return self

    def info(self) -> Dict[str, Any]:
        """Fresh meta plus annotation summaries — enough to verify an ingest
        landed correctly without opening a browser. Does IO."""
        self.refresh()
        out: Dict[str, Any] = dict(self._row)

        joints_report: Dict[str, Any] = {}
        for cam in self._row.get("cameras") or {}:
            blob = self._d._read_blob(joints_track(cam), self.gid)
            if blob:
                joints = json.loads(blob)
                joints_report[cam] = {"annotated_frames": len(joints.get("frames", {}))}
        if joints_report:
            out["joints_pose"] = joints_report
        subtasks_blob = self._d._read_blob(FIELD_SUBTASKS, self.gid)
        if subtasks_blob:
            subtasks = json.loads(subtasks_blob)
            segs = subtasks.get("labeled_subtasks", [])
            out["subtasks"] = {
                "segments": len(segs),
                "ends_at_sec": segs[-1]["end_sec"] if segs else 0,
            }
        return out

    # ---- preset reads ----------------------------------------------------

    def read_joints_pose(self, camera: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """One camera's joints_pose document (default: the primary camera),
        or None when that camera has no joint annotations."""
        cam = camera or self._row.get("primary_camera") or DEFAULT_CAMERA
        blob = self._d._read_blob(joints_track(cam), self.gid)
        return json.loads(blob) if blob else None

    def read_subtasks(self) -> Optional[Dict[str, Any]]:
        """The episode's subtasks document, or None."""
        blob = self._d._read_blob(FIELD_SUBTASKS, self.gid)
        return json.loads(blob) if blob else None

    # ---- writes (each re-reads meta at call time) ------------------------

    def add_cameras(
        self,
        videos,
        *,
        joints_pose=None,
        raw: bool = False,
    ) -> Dict[str, Any]:
        """Add late-arriving cameras. Add-only: a camera's fragments occupy
        its slot and cannot be replaced. Dict form adds N cameras with one
        meta rewrite. ``joints_pose`` binds like in ``add_episode``."""
        from ._core import DatasetError

        row = self._d._require_row(self.episode_id)  # write-time re-read
        cams = self._d._normalize_videos(videos)
        if not cams:
            raise DatasetError("add_cameras needs at least one camera video")
        primary = row.get("primary_camera") or DEFAULT_CAMERA
        joints_by_cam = self._d._normalize_joints(joints_pose, primary)

        have: Dict[str, Any] = dict(row.get("cameras") or {})
        for camera in cams:
            if camera in have:
                raise DatasetError(
                    f"camera '{camera}' already has video for episode "
                    f"'{self.episode_id}' — fragments cannot be replaced. "
                    f"Use a new camera name, or a new episode."
                )
        for camera in joints_by_cam:
            if camera not in cams and camera not in have:
                raise DatasetError(
                    f"joints_pose names camera '{camera}', but this episode has "
                    f"cameras {sorted(have)} — joints bind to the camera whose "
                    f"pixel space they live in"
                )

        probes = self._d._probe_cameras(cams)
        for camera, video in cams.items():
            self._d._check_camera_geometry(camera, probes[camera], video)

        out: Dict[str, Any] = {"episode_id": self.episode_id, "cameras": {}}
        for camera, video in cams.items():
            cam_meta, report = self._d._ingest_camera(
                camera, video, probes[camera], anchor=self.anchor, raw=raw
            )
            have[camera] = cam_meta
            out["cameras"][camera] = report

        meta = {k: v for k, v in row.items() if k not in ("gid", "anchor", "_rev")}
        meta["cameras"] = have
        durations = [c.get("duration_s") for c in have.values() if c.get("duration_s")]
        if durations:
            meta["duration_s"] = max(durations)

        wrote_fields = [joints_track(c) for c in joints_by_cam]
        rev = self._d._next_revision_anchor(row, wrote_fields)
        sample: Dict[str, Any] = {"_anchor": rev,
                                  FIELD_EPISODE_META: dumps_compact(meta).encode()}
        self._d._write_annotations(sample, joints_by_cam, None, out)
        self._d._append_and_invalidate([sample])
        self._d._register_slot_use(row["gid"], self.episode_id, probes)
        self._row = {**row, **meta, "_rev": rev}
        return out

    def revise(
        self,
        *,
        joints_pose=None,
        subtasks=None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Revise annotations and/or meta labels — ONE committed row for
        everything passed (atomic). Storage is append-only: a revision is a
        new version at the same anchor; readers resolve to the newest and
        the DreamDB timeline keeps history.

        ``meta`` accepts only the contract keys (``task``, ``scene``).
        There is no inference — a label exists iff you wrote it.
        """
        from ._core import DatasetError, _load_annotation

        row = self._d._require_row(self.episode_id)  # write-time re-read
        primary = row.get("primary_camera") or DEFAULT_CAMERA
        joints_by_cam = self._d._normalize_joints(joints_pose, primary)
        segments = _load_annotation(subtasks, "subtasks", validate_subtasks)
        labels = self._d._validate_meta_arg(meta)
        if not joints_by_cam and segments is None and labels is None:
            raise DatasetError(
                "nothing to revise — pass joints_pose=, subtasks= or meta="
            )
        have = row.get("cameras") or {}
        for camera in joints_by_cam:
            if camera not in have:
                raise DatasetError(
                    f"joints_pose names camera '{camera}', but this episode has "
                    f"cameras {sorted(have)}"
                )

        new_meta = {k: v for k, v in row.items() if k not in ("gid", "anchor", "_rev")}
        if labels:
            new_meta.update(labels)
        pj = joints_by_cam.get(primary)
        if pj is not None and pj.get("src_fps") is not None:
            new_meta["src_fps"] = pj["src_fps"]
        new_meta = {k: v for k, v in new_meta.items() if v is not None}

        wrote_fields = [joints_track(c) for c in joints_by_cam]
        if segments is not None:
            wrote_fields.append(FIELD_SUBTASKS)
        rev = self._d._next_revision_anchor(row, wrote_fields)
        sample: Dict[str, Any] = {"_anchor": rev}
        # Skip the meta rewrite when nothing meta-visible changed — a scalar
        # append rewrites the whole episode_meta track engine-side.
        old_meta = {k: v for k, v in row.items() if k not in ("gid", "anchor", "_rev")}
        if json.dumps(new_meta, sort_keys=True) != json.dumps(old_meta, sort_keys=True):
            sample[FIELD_EPISODE_META] = dumps_compact(new_meta).encode()

        out: Dict[str, Any] = {"episode_id": self.episode_id}
        self._d._write_annotations(sample, joints_by_cam, segments, out)
        if len(sample) == 1:
            raise DatasetError("nothing to revise — the passed values match what is stored")
        self._d._append_and_invalidate([sample])
        self._row = {"gid": row["gid"], "anchor": row["anchor"], "_rev": rev, **new_meta}
        return out

    # ---- user tracks (x_ namespace; declare with ds.add_track) -----------

    def _user_track(self, name: str) -> str:
        from ._core import DatasetError

        if not USER_TRACK_RE.match(name) or "__" in name:
            raise DatasetError(
                f"'{name}' is not a user track — user tracks match ^x_[a-z0-9_]+$ "
                f"(no '__'). Preset tracks are written through add_episode/"
                f"add_cameras/revise and read through the named methods."
            )
        return name

    def _t_anchor(self, t_sec: float) -> int:
        from ._core import DatasetError

        t = float(t_sec)
        if not (0 <= t * 1e9 < EPISODE_STRIDE_NS - 1024):
            raise DatasetError(
                f"t_sec {t} is outside the episode's slot (0..{MAX_EPISODE_SECONDS}s) — "
                f"timestamps are seconds on the episode's own clock"
            )
        return self.anchor + round(t * 1e9)

    def anchor_at(self, t_sec: float) -> int:
        """Absolute anchor (ns) of second ``t_sec`` on this episode's clock,
        bounds-checked to the slot. The bridge to ``ds.db`` for high-rate
        series: ``ds.db.append_many([{"_anchor": epo.anchor_at(t), ...}])``."""
        return self._t_anchor(t_sec)

    @staticmethod
    def _encode_value(value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return dumps_compact(value).encode()
        return value

    @staticmethod
    def _decode_value(value: Any) -> Any:
        if isinstance(value, (bytes, bytearray, memoryview)):
            raw = bytes(value)
            try:
                return json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                return raw
        return value

    def set_track(self, name: str, value: Any, *, t_sec: float = 0.0) -> None:
        """Write one value on a user track at ``t_sec`` (default 0 — the
        one-value-per-episode pattern). Same (track, time) again = revision
        (readers see the newest; history kept). Dicts/lists store as JSON."""
        self._append_track_rows(name, [(t_sec, value)])

    def append_track(self, name: str, items: Sequence[Tuple[float, Any]]) -> Dict[str, int]:
        """Batch-write ``[(t_sec, value), ...]`` on a user track — one
        commit. The right shape for scalar time series within an episode;
        for cross-episode bulk reads use ``ds.db.iter_all_batches``."""
        return self._append_track_rows(name, items)

    def _append_track_rows(self, name: str, items) -> Dict[str, int]:
        from ._core import DatasetError

        field = self._user_track(name)
        samples = []
        used: Dict[int, int] = {}  # per-batch anchor assignment: later items win
        for t, v in items:
            a0 = self._t_anchor(t)
            # Re-writing the same (track, time) is a revision: next free ns —
            # counting both stored versions and earlier items of THIS batch.
            last, _ = self._d._latest_in_window(field, a0)
            a = max(last + 1, a0, used.get(a0, a0 - 1) + 1)
            if a - a0 >= 1024:
                raise DatasetError(
                    f"track '{name}' at t_sec {t} has exhausted its 1024 revisions"
                )
            used[a0] = a
            samples.append({"_anchor": a, field: self._encode_value(v)})
        if not samples:
            return {"rows": 0}
        try:
            self._d._append_and_invalidate(samples)
        except Exception as e:
            if "not in schema" in str(e) or "no FieldTrack" in str(e):
                raise DatasetError(
                    f"no track '{name}' in this dataset — declare it first with "
                    f"ds.add_track({name!r}, kind=...)"
                ) from e
            raise
        return {"rows": len(samples)}

    def get_track(self, name: str, *, t_sec: float = 0.0) -> Any:
        """The value at exactly ``t_sec`` on a user track, or None."""
        field = self._user_track(name)
        a = self._t_anchor(t_sec)
        _, v = self._d._latest_in_window(field, a)
        return self._decode_value(v) if v is not None else None

    def read_track(
        self, name: str, *, start_sec: float = 0.0, end_sec: Optional[float] = None
    ) -> List[Tuple[float, Any]]:
        """All ``(t_sec, value)`` on a user track within the window, sorted
        by time. Defaults to the whole episode."""
        field = self._user_track(name)
        start = self._t_anchor(start_sec)
        end = (
            self._t_anchor(end_sec) if end_sec is not None
            else self.anchor + EPISODE_STRIDE_NS
        )
        rows = sorted(self._d._read_track_window(field, start, end))
        # Anchors within one revision window are versions of one instant —
        # collapse each cluster to its newest value, timed at cluster start.
        out: List[Tuple[float, Any]] = []
        i = 0
        while i < len(rows):
            a0, v = rows[i]
            j = i + 1
            while j < len(rows) and rows[j][0] - a0 < 1024:
                v = rows[j][1]
                j += 1
            out.append(((a0 - self.anchor) / 1e9, self._decode_value(v)))
            i = j
        return out

    # ---- search (the only single-episode spellings) ----------------------

    def add_search_vectors(
        self,
        *,
        frame_vecs: Optional[Sequence[Tuple[float, Any]]] = None,
        subtask_vecs: Optional[Sequence[Tuple[float, Any, str]]] = None,
    ) -> Dict[str, int]:
        """Upload search vectors you computed yourself — ``[(t_sec, vec512)]``
        CLIP-space frames and/or ``[(t_sec, vec384, label)]`` BGE-space
        segment texts, on the episode's own clock. Searchable immediately;
        re-uploads deduplicate by content."""
        return self._d._append_search_vectors(self._row, frame_vecs, subtask_vecs)

    def embed(
        self,
        *,
        camera: Optional[str] = None,
        fps: float = 1.0,
        video_path: Optional[str] = None,
        source_dir: Optional[str] = None,
        batch_size: int = 32,
    ) -> Dict[str, int]:
        """Encode and upload this episode's search vectors: frames sampled at
        ``fps`` from one camera's SOURCE file (primary unless ``camera=``)
        through CLIP, subtask texts through BGE. The source file resolves
        ``video_path`` → ``source_dir``/``source_rel`` → recorded
        ``source_uri``; a file whose duration disagrees with the recorded
        camera duration is refused (wrong file). Needs ``dreamlake[search]``."""
        from ._core import DatasetError

        try:
            from dreamlake.encoders import iter_video_frames
        except ImportError as e:
            raise DatasetError(str(e)) from e

        row = self._d._require_row(self.episode_id)
        cameras = row.get("cameras") or {}
        if camera is not None and camera not in cameras:
            raise DatasetError(
                f"no camera '{camera}' on episode '{self.episode_id}' "
                f"(have: {sorted(cameras)})"
            )
        cam = camera or row.get("primary_camera")
        if cam not in cameras and cameras:
            cam = next(iter(cameras))
        cam_meta = cameras.get(cam) or {}
        src = self._d._resolve_source(cam_meta, video_path, source_dir)

        frame_vecs: List[Tuple[float, Any]] = []
        if src:
            recorded = cam_meta.get("duration_s")
            frag = float(self._d.encoding.get("frag_seconds", 2.0))
            probed = probe(src)
            # An explicit video_path IS the override — only guard resolved paths.
            if recorded and not video_path and abs(probed.duration_sec - float(recorded)) > 2 * frag:
                raise DatasetError(
                    f"'{src}' runs {probed.duration_sec:.1f}s but camera '{cam}' of "
                    f"episode '{self.episode_id}' was ingested at {recorded:.1f}s — "
                    f"this looks like the wrong file. Pass video_path= to override."
                )
            clip = self._d._clip_encoder()
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
                f"'{self.episode_id}': camera '{cam}' source not found — skipping "
                f"frame vectors; pass video_path= or source_dir= to supply it",
                stacklevel=2,
            )

        subtask_vecs: List[Tuple[float, Any, str]] = []
        segments = self.read_subtasks()
        if segments:
            segs = segments.get("labeled_subtasks", [])
            if segs:
                labels = [s["subtask"] for s in segs]
                vecs = self._d._text_encoder().encode(labels)
                subtask_vecs = [
                    (float(seg["start_sec"]), vec, label)
                    for seg, vec, label in zip(segs, vecs, labels)
                ]

        return self._d._append_search_vectors(row, frame_vecs, subtask_vecs)

    def __repr__(self) -> str:  # snapshot only — zero IO
        cams = ", ".join(self._row.get("cameras") or {})
        return (
            f"<Episode '{self.episode_id}' gid={self.gid} "
            f"cameras=[{cams}] {self.duration_s:.1f}s>"
        )
