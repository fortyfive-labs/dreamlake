"""``dreamlake.dataset.Dataset`` — robot-training datasets on DreamDB.

The write path a labeling pipeline calls once per processed video::

    from dreamlake.dataset import Dataset

    ds = Dataset.create(backend="file:///tmp/datasets/wash-the-dishes")
    ds.add_video(
        video="Ceramics.mov",
        video_id="Ceramics",
        joints_pose=joints_dict,   # your pipeline's per-frame joints output
        subtasks=subtasks_dict,    # your pipeline's action segmentation
    )

Storage: one DreamDB Space per dataset, five tracks, one timeline. Videos
occupy disjoint one-hour anchor slots (see ``_schema``). The layout is shared
byte-for-byte with the TypeScript CLI, so ``dreamlake dataset ls`` and the web
viewer read datasets written here, and vice versa.

This phase is local-first: ``backend`` is any URI dreamdb accepts —
``file:///path`` for a directory (serve it statically and the browser reads
it), or ``s3://bucket/prefix`` with your own AWS credentials. Server-brokered
credentials arrive in a later phase and change only the backend string.
"""

from __future__ import annotations

import json
import os
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ._ffmpeg import FfmpegError, FragmentedVideo, fragment_video, probe
from ._schema import (
    DATASET_REF,
    DEFAULT_PREVIEW_FPS,
    FIELD_JOINTS_POSE,
    FIELD_SUBTASKS,
    FIELD_VIDEO_META,
    FIELD_VIDEO_PREVIEW,
    FIELD_VIDEO_RAW,
    MAX_VIDEO_SECONDS,
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


class Dataset:
    """One robot-training dataset: many videos plus their annotations."""

    def __init__(self, inner, backend: str):
        self._ds = inner
        self.backend = backend

    # ---- Construction --------------------------------------------------

    @classmethod
    def create(cls, backend: str) -> "Dataset":
        """Create an empty dataset at ``backend``. Fails if one already exists
        there — creation is deliberately not idempotent, so a typo'd path
        cannot silently fork a second history."""
        import dreamdb

        try:
            dreamdb.Dataset.open(DATASET_REF, backend=backend)
        except Exception:
            pass  # nothing there — the good case
        else:
            raise DatasetError(
                f"a dataset already exists at {backend} — use Dataset.open() to add to it"
            )

        inner = dreamdb.Dataset.create(DATASET_REF, build_schema(), backend)
        return cls(inner, backend)

    @classmethod
    def open(cls, backend: str) -> "Dataset":
        """Open the dataset at ``backend``."""
        import dreamdb

        try:
            inner = dreamdb.Dataset.open(DATASET_REF, backend=backend)
        except Exception as e:
            raise DatasetError(
                f"no dataset at {backend} — create one with Dataset.create() ({e})"
            ) from e
        return cls(inner, backend)

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
