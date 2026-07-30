"""The DreamLake dataset storage structure — constants and anchor math.

A dataset is ONE DreamDB Space holding many videos, each with per-frame joint
annotations and action segmentation. Five tracks on one timeline; videos
coexist by occupying disjoint one-hour anchor slots, so ``floor(anchor / 1h)``
recovers the video and nothing stores "which video this is".

These values are shared, byte-for-byte, with the TypeScript CLI
(dreamlake-cli ``src/cli/dataset/schema.ts``). A dataset written by either
tool is readable and appendable by the other — which is only true while the
two files agree. Change one, change both.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict

# Anchor slot width per video: one hour in nanoseconds. A video's identity is
# a property of its anchor; a clip longer than the slot would bleed into the
# next video's slot and corrupt both, so ``add_video`` enforces the ceiling.
VIDEO_STRIDE_NS = 3_600_000_000_000

# Seconds a video may run before it would overflow its slot.
MAX_VIDEO_SECONDS = 3600

# The ref every dataset publishes under. Single-segment: DreamDB rejects "a/b".
DATASET_REF = "main"

# Frame rate every playback track is resampled to. Not the source's rate: two
# captures at 29.987 and 29.970 fps produce different init segments and
# therefore cannot share a video field. Pinning the output rate is what lets a
# dataset hold footage from different rigs.
DEFAULT_PREVIEW_FPS = 30

# Track names. One field == one DreamDB track.
FIELD_VIDEO_RAW = "video_raw"
FIELD_VIDEO_PREVIEW = "video_preview"
FIELD_JOINTS_POSE = "joints_pose"
FIELD_SUBTASKS = "subtasks"
FIELD_VIDEO_META = "video_meta"
# Search fields (schema v2). Declared at CREATE time because DreamDB only
# accepts embedding fields there; retrofitting needs a pre-trained index.
FIELD_FRAME_VEC = "frame_vec"          # CLIP ViT-B/32 frame vectors
FIELD_SUBTASK_VEC = "subtask_vec"      # BGE-small segment-text vectors
FIELD_SUBTASK_LABEL = "subtask_label"  # the segment's text, for showing hits

# Vector dimensions are a cross-SDK contract (CLIP ViT-B/32 = 512,
# bge-small-en-v1.5 = 384). The TypeScript CLI pins the same numbers.
FRAME_VEC_DIM = 512
SUBTASK_VEC_DIM = 384

# The schemaType dispatch key this preset writes — into the platform catalog
# row AND the space's own meta — so list pages and viewers know which data
# structure / visualization to use without opening the tracks.
DATASET_SCHEMA_TYPE = "robot.video/v2"


def base_anchor(gid: int) -> int:
    """Base anchor of video ``gid`` (nanoseconds)."""
    return gid * VIDEO_STRIDE_NS


def gid_of(anchor: int) -> int:
    """Which video an anchor belongs to. Inverse of :func:`base_anchor`."""
    return anchor // VIDEO_STRIDE_NS


def frame_anchor(gid: int, k: int, fps: float) -> int:
    """Anchor of frame ``k`` of video ``gid``.

    Kept here so the writer and any future per-frame reader cannot drift: the
    moment two places compute this differently, annotations silently desync
    from video.
    """
    return base_anchor(gid) + round(k * 1e9 / fps)


def build_schema():
    """The dataset schema, as a ``dreamdb.Schema``.

    Every field is ``required=False``, and that is load-bearing: a required
    field cannot be added later without invalidating every existing record, so
    all-optional is what keeps ``add_*`` schema evolution available. Tracks are
    immutable once published — evolution is only ever addition.
    """
    import dreamdb

    schema = dreamdb.Schema()
    # Lossless remux of the source — archival, keeps the original codec.
    schema.add_video(FIELD_VIDEO_RAW, mime="h264", required=False)
    # Re-encoded to one uniform MSE-playable profile. The track the browser
    # plays — and the reason mixed-source datasets are possible at all: every
    # clip on one video field must share an init segment.
    schema.add_video(FIELD_VIDEO_PREVIEW, mime="h264", required=False)
    # Whole-video per-frame joint detections, one JSON blob per video. Named
    # for joints rather than hands: the payload self-describes its skeleton
    # via joint_order/bones, so the same track carries a hand, a body, or an
    # arm. `image` is the blob class in this SDK; the payload is JSON.
    schema.add_image(FIELD_JOINTS_POSE, mime="json", required=False)
    # Whole-video action segmentation, one JSON blob per video.
    schema.add_image(FIELD_SUBTASKS, mime="json", required=False)
    # One JSON row per video. One track rather than eight scalar tracks:
    # listing a dataset is then a single column read.
    schema.add_scalar_string(FIELD_VIDEO_META, required=False)
    # Search vectors (schema v2). LSH index: maintained on append, so vectors
    # are searchable the moment they land — no separate build step.
    # lsh_bits=14 targets the 10k-100k-vector regime a real dataset reaches
    # (the default 20 spreads a small corpus over 2^20 cells and near-miss
    # queries land in empty ones). Below that regime search() compensates
    # with an exact-scan fallback, so recall is right at every size.
    schema.add_embedding(FIELD_FRAME_VEC, dim=FRAME_VEC_DIM, required=False, lsh_bits=14)
    schema.add_embedding(FIELD_SUBTASK_VEC, dim=SUBTASK_VEC_DIM, required=False, lsh_bits=14)
    schema.add_scalar_string(FIELD_SUBTASK_LABEL, required=False)
    return schema


class VideoMeta(TypedDict, total=False):
    """The ``video_meta`` scalar: one JSON row per video.

    ``video_id`` is the stable, human-meaningful id — distinct from ``gid``,
    which is positional and changes if videos are re-ingested in a different
    order. Key your own pipeline off ``video_id``.
    """

    video_id: str
    source_uri: str
    task: str
    scene: str
    src_fps: float
    width: int
    height: int
    total_frames: int
    duration_s: float


def validate_joints_pose(doc: Dict[str, Any]) -> Optional[str]:
    """Shallow-validate a joints_pose document. Returns an error string or None.

    The shape is wire-compatible with the viewer's ``handJoints`` overlay
    adapter — ``frames`` keyed by 0-based ORIGINAL-video frame index, sparse,
    each entry a list of detections with ``keypoints_2d``.
    """
    if not isinstance(doc, dict):
        return "joints_pose must be a dict"
    if not isinstance(doc.get("frames"), dict):
        return "joints_pose needs a 'frames' object (frame index -> detections)"
    for key in ("width", "height", "src_fps"):
        if not isinstance(doc.get(key), (int, float)):
            return f"joints_pose needs numeric '{key}' (the annotation-time frame geometry)"
    return None


def validate_subtasks(doc: Dict[str, Any]) -> Optional[str]:
    """Shallow-validate a subtasks document. Returns an error string or None."""
    if not isinstance(doc, dict):
        return "subtasks must be a dict"
    segs = doc.get("labeled_subtasks")
    if not isinstance(segs, list):
        return "subtasks needs a 'labeled_subtasks' list"
    for i, seg in enumerate(segs):
        if not isinstance(seg, dict) or not all(
            k in seg for k in ("start_sec", "end_sec", "subtask")
        ):
            return f"labeled_subtasks[{i}] needs start_sec, end_sec and subtask"
    return None


__all__: List[str] = [
    "VIDEO_STRIDE_NS",
    "MAX_VIDEO_SECONDS",
    "DATASET_REF",
    "DEFAULT_PREVIEW_FPS",
    "base_anchor",
    "gid_of",
    "frame_anchor",
    "build_schema",
    "VideoMeta",
]
