"""The DreamLake dataset storage structure — constants and anchor math.

A dataset is ONE DreamDB Space holding many EPISODES. An episode is one
recording: one or more camera videos plus per-frame joint annotations and
action segmentation. Episodes coexist on one timeline by occupying disjoint
one-hour anchor slots, so ``floor(anchor / 1h)`` recovers the episode and
nothing stores "which episode this is".

Cameras map to tracks by a naming convention that is itself part of the
contract: camera ``head`` plays back from ``video_preview__head``, archives
to ``video_raw__head``, and carries its joint annotations in
``joints_pose__head`` — presence IS binding, so a viewer overlays
``joints_pose__X`` on camera ``X`` with no pointer field to consult.
``subtasks`` stays a single episode-level track: action segments live on
the shared clock and are camera-independent. The default schema declares
the default camera's (``main``) tracks; other cameras' tracks are added on
first use (DreamDB supports schema evolution by addition — and only by
addition).

These values are shared, byte-for-byte, with the TypeScript CLI
(dreamlake-cli ``src/cli/dataset/schema.ts``). A dataset written by either
tool is readable and appendable by the other — which is only true while the
two files agree. Change one, change both.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple, TypedDict

from ._errors import DatasetError

# Anchor slot width per episode: one hour in nanoseconds. An episode's
# identity is a property of its anchor; a camera clip longer than the slot
# would bleed into the next episode's slot and corrupt both, so
# ``add_episode`` enforces the ceiling on every camera.
EPISODE_STRIDE_NS = 3_600_000_000_000

# Seconds any one camera video may run before it would overflow the slot.
MAX_EPISODE_SECONDS = 3600

# The ref every dataset publishes under. Single-segment: DreamDB rejects "a/b".
DATASET_REF = "main"

# Revision window (ns). DreamDB resolves same-anchor duplicates by CONTENT
# HASH, not write order (verified empirically — "last writer wins" does not
# exist at the engine level), and tombstones kill an anchor permanently. So
# a REVISION of an episode-level value is written at the next free nanosecond
# after the original anchor, and every reader resolves to the HIGHEST anchor
# within [t, t + REVISION_WINDOW_NS). 1024 ns is invisible at any media time
# scale (a 240 fps frame is 4.2 million ns) and bounds revisions per value.
# Cross-SDK contract — the viewer resolves the same way. Change one, change
# both (schema.ts).
REVISION_WINDOW_NS = 1024

# Frame rate every playback track is resampled to. Not the source's rate: two
# captures at 29.987 and 29.970 fps produce different init segments and
# therefore cannot share a video field. Pinning the output rate is what lets a
# camera track hold footage from different recording sessions.
DEFAULT_PREVIEW_FPS = 30

# ---- Camera tracks ---------------------------------------------------------
#
# One camera == one pair of video fields. The ``__`` separator is structural:
# viewers and the TS CLI discover a dataset's cameras by enumerating fields
# with these prefixes, so camera names may not contain ``__`` themselves.

DEFAULT_CAMERA = "main"
VIDEO_PREVIEW_PREFIX = "video_preview__"
VIDEO_RAW_PREFIX = "video_raw__"
JOINTS_POSE_PREFIX = "joints_pose__"

_CAMERA_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def preview_track(camera: str) -> str:
    """The playback track of one camera, e.g. ``video_preview__head``."""
    return VIDEO_PREVIEW_PREFIX + camera


def raw_track(camera: str) -> str:
    """The archival track of one camera, e.g. ``video_raw__head``."""
    return VIDEO_RAW_PREFIX + camera


def joints_track(camera: str) -> str:
    """The joint-annotation track of one camera, e.g. ``joints_pose__head``.

    Joints are 2D keypoints in ONE camera's pixel space — camera-level data
    exactly like the video itself, so they ride the same namespace. The
    viewer overlays ``joints_pose__X`` on camera ``X``; presence is binding.
    """
    return JOINTS_POSE_PREFIX + camera


# ---- Reconstruction tracks (3D hand-object reconstruction extension) --------
#
# A third annotation modality on a video.annotation episode, sibling to
# joints_pose/subtasks. ``recon_mesh`` is camera-independent object geometry;
# the per-frame data (pose/hands/gravity) and the intrinsics live in ONE
# camera's OpenCV frame, so they ride the same ``__<camera>`` namespace as
# joints_pose. Presence is binding (a viewer renders 3D iff these exist),
# exactly like joints_pose. See docs/reconstruction-schema.md.
FIELD_RECON_MESH = "recon_mesh"
RECON_POSE_PREFIX = "recon_pose__"
RECON_HANDS_PREFIX = "recon_hands__"
RECON_GRAVITY_PREFIX = "recon_gravity__"
RECON_CAMERA_PREFIX = "recon_camera__"


def recon_pose_track(camera: str) -> str:
    """Per-frame 6-DoF object poses in one camera's frame, e.g. ``recon_pose__main``."""
    return RECON_POSE_PREFIX + camera


def recon_hands_track(camera: str) -> str:
    """Per-frame MANO hands in one camera's frame, e.g. ``recon_hands__main``."""
    return RECON_HANDS_PREFIX + camera


def recon_gravity_track(camera: str) -> str:
    """Camera-frame gravity up-vector, e.g. ``recon_gravity__main``."""
    return RECON_GRAVITY_PREFIX + camera


def recon_camera_track(camera: str) -> str:
    """Pinhole intrinsics for mesh projection, e.g. ``recon_camera__main``."""
    return RECON_CAMERA_PREFIX + camera


def validate_camera_name(camera: str) -> Optional[str]:
    """Error string when ``camera`` cannot name a track, else None."""
    if not isinstance(camera, str) or not _CAMERA_NAME_RE.match(camera):
        return (
            f"camera name '{camera}' must be 1-64 chars of lowercase letters, "
            f"digits, '_' or '-', starting with a letter or digit"
        )
    if "__" in camera:
        return f"camera name '{camera}' may not contain '__' (it is the track-name separator)"
    return None


# ---- Episode-level tracks. One field == one DreamDB track. -----------------
FIELD_SUBTASKS = "subtasks"
FIELD_EPISODE_META = "episode_meta"
# The slim per-episode index: the episode_id STRING at each episode's base
# anchor, on a scalar_string track — the one case where spec/0011's
# inverted-by-value scalar layout is a perfect fit. Every distinct value
# (= every id, ~30 B) lives inline in the Track Object, so ONE fetch of one
# cacheable object yields the complete {episode_id -> anchor} map; the
# rewrite-on-append cost is O(N × 30 B) — smooth at any realistic scale.
# (A blob track was tried first: O(1) appends, but our one-item-per-commit
# pattern makes every id its own remote object and neither pack_items nor
# compact() repacks Fragment tracks, so reading all ids was N GETs.)
# id→slot lookups and duplicate checks read THIS track; the fat
# episode_meta column is only ranged-read for the slots actually shown.
FIELD_EPISODE_INDEX = "episode_index"
# Search fields (schema v2+). Declared at CREATE time because DreamDB only
# accepts embedding fields there; retrofitting needs a pre-trained index.
FIELD_FRAME_VEC = "frame_vec"          # CLIP ViT-B/32 frame vectors
FIELD_SUBTASK_VEC = "subtask_vec"      # BGE-small segment-text vectors
FIELD_SUBTASK_LABEL = "subtask_label"  # the segment's text, for showing hits

# Vector dimensions are a cross-SDK contract (CLIP ViT-B/32 = 512,
# bge-small-en-v1.5 = 384). The TypeScript CLI pins the same numbers.
FRAME_VEC_DIM = 512
SUBTASK_VEC_DIM = 384

# ---- Space-meta keys (cross-SDK contract, mirrored in schema.ts) -----------
#
# The encoding profile is a DATASET-lifetime property (every clip on one
# playback track must share one init segment, and the init encodes frame
# geometry) — so it is chosen once at create and stored in the space meta,
# not passed per call. Besides the flat defaults, the value carries
# "cameras": {name: {"width", "height", "fps"}} — each camera track's
# PLAYBACK profile, ADOPTED from its first clip (height/fps from the
# defaults, width from that clip's aspect ratio) and immutable after —
# the init-segment constraint made visible. Later ingests validate
# against it BEFORE transcoding, from the handle's in-memory copy (zero
# IO); the profile is written exactly once per camera.
ENCODING_META_KEY = "dreamdb.dataset.encoding"
DEFAULT_ENCODING = {
    "preview_height": 720,
    "preview_fps": DEFAULT_PREVIEW_FPS,
    "frag_seconds": 2.0,
}
# "1" when the dataset was created with visibility="public" — episode meta
# then omits absolute source paths (they leak the uploader's filesystem).
PUBLIC_META_KEY = "dreamdb.dataset.public"
# JSON object {track_name: kind} of user tracks declared via add_track — the
# preset's own registry, since the engine has no field-enumeration API.
USER_TRACKS_META_KEY = "dreamdb.dataset.user_tracks"
# Present ("v1") once any episode carries the reconstruction extension
# (recon_* fields). A version tag for the recon payload layout, additive to
# and independent of the dataset schemaType. See docs/reconstruction-schema.md.
RECON_SCHEMA_META_KEY = "dreamdb.dataset.recon"
RECON_SCHEMA_VERSION = "v1"
# The only keys `meta=` accepts on add_episode/revise. Everything else in the
# episode_meta row is SDK-assembled (probe/derivation) — user data goes to
# x_ tracks, not meta.
META_KEYS = ("task", "scene")

# User tracks: enforced namespace. The preset promises never to claim x_*,
# so user columns and future preset tracks cannot collide. "__" is banned —
# it is the camera separator.
USER_TRACK_RE = re.compile(r"^x_[a-z0-9_]{1,60}$")

# add_track kinds == the dreamdb schema-evolution surface, verbatim — the
# kind string IS the dreamdb method suffix, zero translation. "embedding"
# is create-time-only in the engine; "audio" has no post-create method.
TRACK_KINDS = (
    "image", "video",
    "scalar_float", "scalar_int", "scalar_bool",
    "scalar_string", "scalar_categorical", "scalar_timestamp",
)

# The schemaType dispatch key this preset writes — into the platform catalog
# row AND the space's own meta — so list pages and viewers know which data
# structure / visualization to use without opening the tracks. Within this
# type, evolution is only ever additive (presence of a track IS the version
# signal); the type string bumps only when layout semantics change. The
# earlier robot.video/v2 / robot.episode/v3 experiments are simply foreign
# types now — open() steers them to dreamlake.db.
#
# v1 → v2: episode_meta moved from a scalar_string column (spec/0011 inlines
# every distinct value in the Track Object — appending one episode rewrote
# ~1KB × N) to a per-episode json blob, loaded per slot window like
# joints_pose/subtasks; episode_index (scalar_string of bare ids) became the
# lookup path. v1 spaces are refused at open with an actionable message.
DATASET_SCHEMA_TYPE = "video.annotation/v2"


def base_anchor(gid: int) -> int:
    """Base anchor of episode ``gid`` (nanoseconds)."""
    return gid * EPISODE_STRIDE_NS


def gid_of(anchor: int) -> int:
    """Which episode an anchor belongs to. Inverse of :func:`base_anchor`."""
    return anchor // EPISODE_STRIDE_NS


def frame_anchor(gid: int, k: int, fps: float) -> int:
    """Anchor of frame ``k`` of episode ``gid``.

    Kept here so the writer and any future per-frame reader cannot drift: the
    moment two places compute this differently, annotations silently desync
    from video.
    """
    return base_anchor(gid) + round(k * 1e9 / fps)


def build_schema():
    """The dataset schema, as a ``dreamdb.Schema``.

    Every field is ``required=False``, and that is load-bearing: a required
    field cannot be added later without invalidating every existing record, so
    all-optional is what keeps schema evolution available. Tracks are
    immutable once published — evolution is only ever addition, which is how
    additional cameras join later.
    """
    import dreamdb

    schema = dreamdb.Schema()
    # The default camera's pair. Preview is re-encoded to one uniform
    # MSE-playable profile — the track the browser plays. Raw is a lossless
    # remux of the source, archival, keeping the original codec. Further
    # cameras' pairs are added dynamically on first use.
    schema.add_video(preview_track(DEFAULT_CAMERA), mime="h264", required=False)
    schema.add_video(raw_track(DEFAULT_CAMERA), mime="h264", required=False)
    # Per-frame joint detections for the default camera, one JSON blob per
    # episode. Per-camera like the video (2D keypoints live in ONE camera's
    # pixel space); named for joints rather than hands: the payload
    # self-describes its skeleton via joint_order/bones, so the same track
    # carries a hand, a body, or an arm. `image` is the blob class in this
    # SDK; the payload is JSON.
    schema.add_image(joints_track(DEFAULT_CAMERA), mime="json", required=False)
    # Whole-episode action segmentation, one JSON blob per episode —
    # episode-level on purpose: segments live on the shared clock and are
    # camera-independent.
    schema.add_image(FIELD_SUBTASKS, mime="json", required=False)
    # One JSON blob per episode (v2): cameras/labels/geometry, loaded per
    # slot window like the annotation blobs. A blob on purpose — as a
    # scalar column, spec/0011 inlined every (all-distinct, ~1KB) row in
    # the Track Object, so each append rewrote the whole thing.
    schema.add_image(FIELD_EPISODE_META, mime="json", required=False)
    # The slim per-episode index (see the constant's comment): the lookup
    # path that keeps id checks off the fat meta column — one cacheable
    # fetch yields the whole id map.
    schema.add_scalar_string(FIELD_EPISODE_INDEX, required=False)
    # Search vectors. LSH index: maintained on append, so vectors are
    # searchable the moment they land — no separate build step.
    # lsh_bits=14 targets the 10k-100k-vector regime a real dataset reaches
    # (the default 20 spreads a small corpus over 2^20 cells and near-miss
    # queries land in empty ones). Below that regime search() compensates
    # with an exact-scan fallback, so recall is right at every size.
    schema.add_embedding(FIELD_FRAME_VEC, dim=FRAME_VEC_DIM, required=False, lsh_bits=14)
    schema.add_embedding(FIELD_SUBTASK_VEC, dim=SUBTASK_VEC_DIM, required=False, lsh_bits=14)
    schema.add_scalar_string(FIELD_SUBTASK_LABEL, required=False)
    # Reconstruction extension (optional, additive). One JSON blob per episode
    # each — object geometry is episode-level; pose/hands/gravity/intrinsics are
    # camera-scoped like joints_pose. Further cameras' recon tracks are declared
    # on first use (see _ensure_recon_tracks).
    schema.add_image(FIELD_RECON_MESH, mime="json", required=False)
    schema.add_image(recon_pose_track(DEFAULT_CAMERA), mime="json", required=False)
    schema.add_image(recon_hands_track(DEFAULT_CAMERA), mime="json", required=False)
    schema.add_image(recon_gravity_track(DEFAULT_CAMERA), mime="json", required=False)
    schema.add_image(recon_camera_track(DEFAULT_CAMERA), mime="json", required=False)
    return schema


def classify_track(name: str) -> Tuple[str, Optional[str], str]:
    """``(kind, camera, role)`` for a track name — the two-sided prefix
    taxonomy as one testable function, mirrored byte-for-byte in the
    TypeScript CLI's schema.ts. Change one, change both."""
    if name.startswith(VIDEO_PREVIEW_PREFIX):
        return "video", name[len(VIDEO_PREVIEW_PREFIX):], "video_preview"
    if name.startswith(VIDEO_RAW_PREFIX):
        return "video", name[len(VIDEO_RAW_PREFIX):], "video_raw"
    if name.startswith(JOINTS_POSE_PREFIX):
        return "blob", name[len(JOINTS_POSE_PREFIX):], "joints_pose"
    if name == FIELD_SUBTASKS:
        return "blob", None, "subtasks"
    if name == FIELD_EPISODE_META:
        return "blob", None, "episode_meta"
    if name == FIELD_RECON_MESH:
        return "blob", None, "recon_mesh"
    if name.startswith(RECON_POSE_PREFIX):
        return "blob", name[len(RECON_POSE_PREFIX):], "recon_pose"
    if name.startswith(RECON_HANDS_PREFIX):
        return "blob", name[len(RECON_HANDS_PREFIX):], "recon_hands"
    if name.startswith(RECON_GRAVITY_PREFIX):
        return "blob", name[len(RECON_GRAVITY_PREFIX):], "recon_gravity"
    if name.startswith(RECON_CAMERA_PREFIX):
        return "blob", name[len(RECON_CAMERA_PREFIX):], "recon_camera"
    if name == FIELD_EPISODE_INDEX:
        return "scalar", None, "episode_index"
    if name in (FIELD_FRAME_VEC, FIELD_SUBTASK_VEC):
        return "embedding", None, "search"
    if name == FIELD_SUBTASK_LABEL:
        return "scalar", None, "search"
    if name.startswith("x_"):
        return "unknown", None, "user"
    return "unknown", None, "unknown"


class CameraMeta(TypedDict, total=False):
    """One camera's entry in ``EpisodeMeta.cameras``."""

    width: int
    height: int
    fps: float
    duration_s: float
    codec: str
    source_rel: str   # last two path components — joins with embed's source_dir=
    source_uri: str   # absolute; omitted on public datasets (path leak)


class EpisodeMeta(TypedDict, total=False):
    """The ``episode_meta`` scalar: one JSON row per episode.

    ``episode_id`` is the stable, human-meaningful id — distinct from ``gid``,
    which is positional and changes if episodes are re-ingested in a different
    order. Key your own pipeline off ``episode_id``.

    The top-level geometry (``src_fps``/``width``/``height``) describes the
    PRIMARY camera — the default binding for a bare ``joints_pose=`` doc and
    the camera search embeds. Every camera's own geometry lives under
    ``cameras``; joint annotations bind to cameras by track name
    (``joints_pose__{camera}``), not through this meta.
    """

    episode_id: str
    task: str
    scene: str
    primary_camera: str
    cameras: Dict[str, CameraMeta]
    # The one top-level geometry field that earned its place: the viewer's
    # timeline frame-stepping reads it. Primary camera's joints-doc value
    # wins over probe. (Top-level width/height/total_frames were dropped —
    # duplicates of cameras[primary]; meta keys are optional forever, so
    # ceasing to write them is not a semantic change.)
    src_fps: float
    duration_s: float


def validate_joints_pose(doc: Dict[str, Any]) -> Optional[str]:
    """Shallow-validate a joints_pose document. Returns an error string or None.

    The shape is wire-compatible with the viewer's ``handJoints`` overlay
    adapter — ``frames`` keyed by 0-based ORIGINAL-video frame index, sparse,
    each entry a list of detections with ``keypoints_2d``. Coordinates live
    in the pixel space of the camera whose ``joints_pose__{camera}`` track
    holds the document.
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


# ---- reconstruction modality: normalize+validate to the stored JSON docs ----
# (see docs/reconstruction-schema.md — the contract these produce). Lives here
# beside validate_subtasks/validate_joints_pose so the whole field contract —
# names, schema, classification AND payload validation — is in one module.

def _recon_meshes_doc(meshes: Any) -> Dict[str, Any]:
    if not isinstance(meshes, dict) or not meshes:
        raise DatasetError(
            "reconstruction: meshes must be a non-empty {object: mesh} dict"
        )
    out: Dict[str, Any] = {}
    for name, m in meshes.items():
        if isinstance(m, str):
            out[str(name)] = {"obj": m, "scale": 1.0}
        elif isinstance(m, dict) and "obj" in m:
            out[str(name)] = {"obj": str(m["obj"]), "scale": float(m.get("scale", 1.0))}
        else:
            raise DatasetError(
                f"reconstruction: mesh '{name}' must be an .obj string or "
                f"{{'obj': <text>, 'scale': <float>}}"
            )
    return out


def _recon_poses_doc(poses: Any) -> Dict[str, Any]:
    # accept {frame: {object: {t,q}}} or an already-wrapped {"frames": {...}}
    frames = poses.get("frames") if isinstance(poses, dict) and "frames" in poses else poses
    if not isinstance(frames, dict) or not frames:
        raise DatasetError(
            "reconstruction: poses must be {frame: {object: {'t':[x,y,z], "
            "'q':[w,x,y,z]}}}"
        )
    out: Dict[str, Any] = {}
    for f, objs in frames.items():
        if not isinstance(objs, dict):
            raise DatasetError(f"reconstruction: poses[{f}] must be {{object: {{t,q}}}}")
        row: Dict[str, Any] = {}
        for name, p in objs.items():
            t = [float(x) for x in p["t"]]
            q = [float(x) for x in p["q"]]
            if len(t) != 3 or len(q) != 4:
                raise DatasetError(
                    f"reconstruction: pose {name}@{f} needs t[3] and q[4 wxyz]"
                )
            row[str(name)] = {"t": t, "q": q}
        out[str(int(f))] = row
    return {"frames": out}


def _recon_camera_doc(intrinsics: Any) -> Dict[str, Any]:
    if not isinstance(intrinsics, dict):
        raise DatasetError("reconstruction: intrinsics must be a dict")
    try:
        fx, fy = float(intrinsics["fx"]), float(intrinsics["fy"])
        cx, cy = float(intrinsics["cx"]), float(intrinsics["cy"])
    except KeyError as e:
        raise DatasetError(f"reconstruction: intrinsics missing {e}") from e
    return {
        "fx": fx, "fy": fy, "cx": cx, "cy": cy,
        "width": int(intrinsics.get("width", round(2 * cx))),
        "height": int(intrinsics.get("height", round(2 * cy))),
    }


def _recon_hands_doc(hands: Any) -> Dict[str, Any]:
    if not isinstance(hands, dict) or "frames" not in hands:
        raise DatasetError(
            "reconstruction: hands must be {'faces': {'left','right'}, "
            "'frames': {frame: {side: {'verts','joints'}}}}"
        )
    faces_in = hands.get("faces") or {}
    faces = {
        side: [[int(i) for i in tri] for tri in tris]
        for side, tris in faces_in.items()
    }
    out_frames: Dict[str, Any] = {}
    for f, sides in hands["frames"].items():
        if not isinstance(sides, dict):
            continue
        row: Dict[str, Any] = {}
        for side in ("left", "right"):
            hd = sides.get(side)
            if not hd:
                continue
            row[side] = {
                "verts": [[float(x) for x in v] for v in hd["verts"]],
                "joints": [[float(x) for x in j] for j in hd.get("joints", [])],
            }
        if row:
            out_frames[str(int(f))] = row
    return {"faces": faces, "frames": out_frames}


def _recon_gravity_doc(gravity: Any) -> Dict[str, Any]:
    v = list(gravity)
    if len(v) != 3:
        raise DatasetError("reconstruction: gravity must be a 3-vector [x,y,z]")
    return {"vec3d": [float(x) for x in v]}


def _recon_field_map(camera: str) -> Dict[str, Any]:
    """bundle key → (track name, normalizer) for ``camera``. The single source
    of truth for which of the five recon pieces exist and how they map."""
    return {
        "meshes": (FIELD_RECON_MESH, _recon_meshes_doc),
        "poses": (recon_pose_track(camera), _recon_poses_doc),
        "intrinsics": (recon_camera_track(camera), _recon_camera_doc),
        "hands": (recon_hands_track(camera), _recon_hands_doc),
        "gravity": (recon_gravity_track(camera), _recon_gravity_doc),
    }


def reconstruction_fields(bundle: Dict[str, Any], camera: str) -> Dict[str, Any]:
    """A reconstruction bundle → ``{track_name: doc}`` for ``camera``, the
    normalized+validated blobs ready to encode. Backs the ``reconstruction=``
    argument on ``add_episode`` / ``revise``.

    Every piece is INDEPENDENT and OPTIONAL: pass any subset of ``meshes``,
    ``poses``, ``intrinsics``, ``hands``, ``gravity`` — only the keys present are
    written, so the five recon fields can be filled all at once or one at a
    time across calls (each is its own blob). At least one must be present.
    An extra ``camera`` key, if any, is ignored — the caller passes the name in."""
    fmap = _recon_field_map(camera)
    fields: Dict[str, Any] = {}
    for key, (track, normalize) in fmap.items():
        if bundle.get(key) is not None:
            fields[track] = normalize(bundle[key])
    if not fields:
        raise DatasetError(
            "reconstruction bundle is empty — pass at least one of "
            "meshes / poses / intrinsics / hands / gravity"
        )
    return fields


def reconstruction_summary(fields: Dict[str, Any], camera: str) -> Dict[str, Any]:
    """A compact report of what ``reconstruction_fields`` produced — which
    pieces were written, plus object/frame counts when those pieces are present."""
    track_to_key = {track: key for key, (track, _) in _recon_field_map(camera).items()}
    mesh = fields.get(FIELD_RECON_MESH, {})
    pose = fields.get(recon_pose_track(camera), {})
    return {
        "camera": camera,
        "wrote": [track_to_key[t] for t in fields if t in track_to_key],
        "objects": sorted(mesh.keys()),
        "frames": len(pose.get("frames", {})),
    }


__all__: List[str] = [
    "EPISODE_STRIDE_NS",
    "MAX_EPISODE_SECONDS",
    "REVISION_WINDOW_NS",
    "DATASET_REF",
    "DEFAULT_PREVIEW_FPS",
    "DEFAULT_CAMERA",
    "preview_track",
    "raw_track",
    "joints_track",
    "validate_camera_name",
    "base_anchor",
    "gid_of",
    "frame_anchor",
    "build_schema",
    "CameraMeta",
    "EpisodeMeta",
    "classify_track",
    "META_KEYS",
    "TRACK_KINDS",
]
