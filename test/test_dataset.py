"""Tests for dreamlake.dataset — the robot-training episode dataset SDK.

The anchor math gets pinned hardest: every other bug in this feature
announces itself, but a wrong anchor ingests successfully and the only
symptom is a skeleton drawn over the wrong frame. The pure-Python pieces
(anchor layout, playlist parsing, validation, classification, the aspect
pre-check) are hermetic; the end-to-end write/read passes need dreamdb +
ffmpeg and skip cleanly where they are absent.
"""

import json
import shutil
import subprocess

import pytest

from dreamlake.dataset._ffmpeg import FfmpegError, _parse_playlist
from dreamlake.dataset._schema import (
    EPISODE_STRIDE_NS,
    MAX_EPISODE_SECONDS,
    base_anchor,
    classify_track,
    frame_anchor,
    gid_of,
    joints_track,
    preview_track,
    raw_track,
    validate_camera_name,
    validate_joints_pose,
    validate_subtasks,
)
from dreamlake.dataset._core import _preview_width

# ─── anchor layout ───────────────────────────────────────────────────


def test_slot_layout_round_trips_and_slots_do_not_overlap():
    for gid in (0, 1, 7, 195_000):
        assert gid_of(base_anchor(gid)) == gid
    last = base_anchor(4) + EPISODE_STRIDE_NS - 1
    assert gid_of(last) == 4
    assert gid_of(last + 1) == 5


def test_slot_width_matches_the_documented_per_episode_limit():
    assert EPISODE_STRIDE_NS == MAX_EPISODE_SECONDS * 1_000_000_000


def test_frame_anchors_are_exact_for_fractional_frame_rates():
    fps = 29.98740647455367
    assert frame_anchor(2, 0, fps) == base_anchor(2)
    assert gid_of(frame_anchor(2, 100_000, fps)) == 2
    anchors = [frame_anchor(0, k, fps) for k in range(200)]
    assert all(b > a for a, b in zip(anchors, anchors[1:]))


# ─── track taxonomy (the cross-SDK contract artifact) ────────────────


def test_camera_names_map_to_prefixed_tracks():
    assert preview_track("head") == "video_preview__head"
    assert raw_track("wrist-l") == "video_raw__wrist-l"
    assert joints_track("head") == "joints_pose__head"


def test_classify_track_covers_the_whole_taxonomy():
    assert classify_track("video_preview__head") == ("video", "head", "video_preview")
    assert classify_track("video_raw__wrist") == ("video", "wrist", "video_raw")
    assert classify_track("joints_pose__main") == ("blob", "main", "joints_pose")
    assert classify_track("subtasks") == ("blob", None, "subtasks")
    assert classify_track("episode_meta") == ("scalar", None, "episode_meta")
    assert classify_track("frame_vec") == ("embedding", None, "search")
    assert classify_track("subtask_vec") == ("embedding", None, "search")
    assert classify_track("subtask_label") == ("scalar", None, "search")
    assert classify_track("x_reward") == ("unknown", None, "user")
    assert classify_track("mystery") == ("unknown", None, "unknown")


def test_camera_name_validation_blocks_separator_and_junk():
    assert validate_camera_name("head") is None
    assert validate_camera_name("wrist_l-2") is None
    assert validate_camera_name("a__b") is not None
    assert validate_camera_name("") is not None
    assert validate_camera_name("Head") is not None
    assert validate_camera_name("-lead") is not None


# ─── playlist → anchors ──────────────────────────────────────────────


def _write_playlist(tmp_path, body: str) -> str:
    p = tmp_path / "index.m3u8"
    p.write_text(body)
    return str(p)


def test_parse_playlist_accumulates_extinf_and_offsets_by_anchor(tmp_path):
    p = _write_playlist(
        tmp_path,
        "#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-MAP:URI=\"init.mp4\"\n"
        "#EXTINF:2.000000,\nseg_00000.m4s\n"
        "#EXTINF:2.000000,\nseg_00001.m4s\n"
        "#EXTINF:1.500000,\nseg_00002.m4s\n#EXT-X-ENDLIST\n",
    )
    anchor = base_anchor(3)
    frags = _parse_playlist(p, str(tmp_path), anchor)
    assert len(frags) == 3
    assert frags[0][1] == anchor
    assert frags[0][2] == anchor + 2_000_000_000
    assert frags[1][1] == frags[0][2]
    assert frags[2][2] == anchor + 5_500_000_000


def test_parse_playlist_refuses_an_empty_playlist(tmp_path):
    p = _write_playlist(tmp_path, "#EXTM3U\n#EXT-X-ENDLIST\n")
    with pytest.raises(FfmpegError, match="no fragments"):
        _parse_playlist(p, str(tmp_path), 0)


# ─── validation + geometry ───────────────────────────────────────────


def test_preview_width_matches_ffmpeg_scale_minus_2():
    assert _preview_width(1920, 1080, 720) == 1280
    assert _preview_width(640, 480, 720) == 960


def test_annotation_validators_name_whats_missing():
    assert validate_joints_pose({"frames": {}}) is not None
    assert (
        validate_joints_pose({"width": 1, "height": 1, "src_fps": 30.0, "frames": {}})
        is None
    )
    assert validate_subtasks({}) is not None
    assert (
        validate_subtasks({"labeled_subtasks": [{"start_sec": 0, "end_sec": 1, "subtask": "x"}]})
        is None
    )


# ─── end-to-end (needs dreamdb + ffmpeg) ─────────────────────────────

_has_ffmpeg = shutil.which("ffmpeg") is not None
try:
    import dreamdb  # noqa: F401

    _has_dreamdb = True
except ImportError:
    _has_dreamdb = False

pytestmark_e2e = pytest.mark.skipif(
    not (_has_ffmpeg and _has_dreamdb), reason="needs ffmpeg and dreamdb"
)

JOINTS = {
    "width": 640, "height": 480, "src_fps": 30.0,
    "joint_order": ["wrist"], "bones": [],
    "frames": {"0": [{"is_right": 0, "det_conf": 0.9, "keypoints_2d": [[1, 2]]}]},
}
SUBTASKS = {"task": "t", "labeled_subtasks": [
    {"start_sec": 0.0, "end_sec": 3.0, "subtask": "s"}]}


def _make_clip(path, size="640x480", seconds=3):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", f"testsrc2=duration={seconds}:size={size}:rate=30",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return str(path)


@pytestmark_e2e
def test_end_to_end_write_then_read(tmp_path):
    from dreamlake.dataset import VideoAnnotationDataset as Dataset, DatasetError, Episode

    video = _make_clip(tmp_path / "clip.mp4")
    backend = f"file://{tmp_path}/space"
    ds = Dataset.create(backend=backend)
    with pytest.raises(DatasetError, match="already exists"):
        Dataset.create(backend=backend)

    # add_episode returns the handle; raw=True exercises the archival path.
    epo = ds.add_episode(video, episode_id="ep0", joints_pose=JOINTS,
                         subtasks=SUBTASKS, meta={"task": "t"}, raw=True)
    assert isinstance(epo, Episode)
    assert epo.gid == 0 and epo.task == "t"
    assert epo.report["cameras"]["main"]["preview"]["fragments"] >= 1
    assert epo.report["cameras"]["main"]["raw"]["fragments"] >= 1

    ds2 = Dataset.open(backend=backend)
    eps = ds2.episodes()
    assert [(e.gid, e.episode_id) for e in eps] == [(0, "ep0")]
    assert eps[0].meta["primary_camera"] == "main"
    assert eps[0].cameras["main"]["width"] == 640
    # Meta slimming: no top-level width/height/total_frames; src_fps kept.
    assert "width" not in eps[0].meta and "total_frames" not in eps[0].meta
    assert eps[0].meta["src_fps"] == 30.0
    # source_rel = last two path components.
    assert eps[0].cameras["main"]["source_rel"].endswith("/clip.mp4")

    # Blobs round-trip byte-exact through the handle.
    assert ds2.episode("ep0").read_joints_pose() == JOINTS
    assert ds2.episode("ep0").read_subtasks() == SUBTASKS

    # Duplicate id refused; same-camera aspect mismatch refused pre-transcode.
    with pytest.raises(DatasetError, match="already in this dataset"):
        ds.add_episode(video, episode_id="ep0")
    wide = _make_clip(tmp_path / "wide.mp4", size="1280x720", seconds=2)
    with pytest.raises(DatasetError, match="aspect ratio"):
        ds.add_episode(wide, episode_id="ep1")

    # NO task inference from subtasks: meta only holds what was passed.
    epo2 = ds.add_episode(_make_clip(tmp_path / "c2.mp4"), episode_id="ep2",
                          subtasks=SUBTASKS)
    assert epo2.task is None

    # meta= whitelist.
    with pytest.raises(DatasetError, match="unknown meta key"):
        ds.add_episode(_make_clip(tmp_path / "c3.mp4"), episode_id="ep3",
                       meta={"operator": "me"})


@pytestmark_e2e
def test_encoding_profile_persists_and_open_verifies(tmp_path):
    from dreamlake.dataset import VideoAnnotationDataset as Dataset, DatasetError

    backend = f"file://{tmp_path}/space"
    ds = Dataset.create(backend=backend, preview_height=480, preview_fps=24)
    assert ds.encoding["preview_height"] == 480

    reopened = Dataset.open(backend=backend)
    assert reopened.encoding == {"preview_height": 480, "preview_fps": 24.0,
                                 "frag_seconds": 2.0}
    # verify-not-ignore: matching passes, mismatch errors.
    Dataset.open(backend=backend, preview_height=480)
    with pytest.raises(DatasetError, match="preview_height"):
        Dataset.open(backend=backend, preview_height=720)

    # The profile drives the transcode: a 640x480 source at height 480.
    clip = _make_clip(tmp_path / "c.mp4")
    epo = ds.add_episode(clip, episode_id="e0")
    assert epo.report["cameras"]["main"]["preview"]["fragments"] >= 1


@pytestmark_e2e
def test_multi_camera_handle_lifecycle(tmp_path):
    from dreamlake.dataset import VideoAnnotationDataset as Dataset, DatasetError

    head = _make_clip(tmp_path / "head.mp4", size="1280x720")
    wrist = _make_clip(tmp_path / "wrist.mp4", size="640x480")
    ds = Dataset.create(backend=f"file://{tmp_path}/space")

    # Different aspect ratios coexist — different camera tracks.
    epo = ds.add_episode({"head": head, "wrist": wrist}, episode_id="ep0")
    assert set(epo.report["cameras"]) == {"head", "wrist"}
    assert epo.meta["primary_camera"] == "head"
    assert ds.cameras() == ["head", "wrist"]

    # Late camera via the handle; existing camera refused.
    tail = _make_clip(tmp_path / "tail.mp4", size="1280x720", seconds=2)
    out = epo.add_cameras({"tail": tail})
    assert "tail" in out["cameras"]
    with pytest.raises(DatasetError, match="already has video"):
        epo.add_cameras({"head": head})

    # revise: atomic LWW — newest wins on read; scene lands; no inference.
    v2 = {"task": "t", "labeled_subtasks": [
        {"start_sec": 0.0, "end_sec": 1.0, "subtask": "new"}]}
    epo.revise(subtasks=v2, meta={"scene": "kitchen"})
    fresh = ds.episode("ep0")
    assert fresh.scene == "kitchen"
    assert fresh.read_subtasks() == v2
    assert set(fresh.cameras) == {"head", "wrist", "tail"}
    with pytest.raises(DatasetError, match="nothing to revise"):
        epo.revise()

    # Per-camera joints binding via dict; default read = primary.
    jh = {"width": 1280, "height": 720, "src_fps": 30.0,
          "frames": {"0": [{"keypoints_2d": [[1, 2]]}]}}
    jw = {"width": 640, "height": 480, "src_fps": 30.0,
          "frames": {"0": [{"keypoints_2d": [[3, 4]]}]}}
    epo.revise(joints_pose={"head": jh, "wrist": jw})
    assert ds.episode("ep0").read_joints_pose() == jh
    assert ds.episode("ep0").read_joints_pose(camera="wrist") == jw
    assert ds.episode("ep0").read_joints_pose(camera="tail") is None
    with pytest.raises(DatasetError, match="names camera"):
        epo.revise(joints_pose={"nope": jh})

    # Stale handle cannot clobber: writes re-read meta at call time.
    stale = ds.episode("ep0")
    ds.episode("ep0").revise(meta={"task": "newest"})
    stale.revise(meta={"scene": "bench"})   # scene write on stale handle
    latest = ds.episode("ep0")
    assert latest.task == "newest" and latest.scene == "bench"


@pytestmark_e2e
def test_user_tracks_and_introspection(tmp_path):
    from dreamlake.dataset import VideoAnnotationDataset as Dataset, DatasetError

    clip = _make_clip(tmp_path / "clip.mp4")
    ds = Dataset.create(backend=f"file://{tmp_path}/space")
    epo = ds.add_episode(clip, episode_id="ep0")

    # Enforcement: x_ namespace, dreamdb kinds, embedding refused.
    with pytest.raises(DatasetError, match="x_"):
        ds.add_track("reward", "scalar_float")
    with pytest.raises(DatasetError, match="x_"):
        ds.add_track("x_a__b", "scalar_float")
    with pytest.raises(DatasetError, match="created"):
        ds.add_track("x_vecs", "embedding")
    with pytest.raises(DatasetError, match="unknown track kind"):
        ds.add_track("x_thing", "float")
    with pytest.raises(DatasetError, match="declare it first"):
        epo.set_track("x_ghost", 1.0)

    ds.add_track("x_reward", "scalar_float")
    ds.add_track("x_reward", "scalar_float")  # idempotent
    ds.add_track("x_quality", "image", mime="json")

    # One-value-per-episode (t=0) with LWW revision.
    epo.set_track("x_reward", 0.5)
    epo.set_track("x_reward", 0.75)
    assert epo.get_track("x_reward") == 0.75
    # JSON docs round-trip.
    epo.set_track("x_quality", {"blurry": False})
    assert epo.get_track("x_quality") == {"blurry": False}

    # Timestamped items + window read; slot bounds enforced.
    epo.append_track("x_reward", [(1.0, 0.1), (2.0, 0.2)])
    assert epo.read_track("x_reward", start_sec=0.5) == [(1.0, 0.1), (2.0, 0.2)]
    assert [t for t, _ in epo.read_track("x_reward")] == [0.0, 1.0, 2.0]
    with pytest.raises(DatasetError, match="outside the episode's slot"):
        epo.set_track("x_reward", 1.0, t_sec=MAX_EPISODE_SECONDS + 1)
    assert epo.anchor_at(1.5) == epo.anchor + 1_500_000_000

    # Introspection: user tracks registered; preset taxonomy classified.
    infos = {t.name: t for t in ds.tracks()}
    assert infos["x_reward"].role == "user" and infos["x_reward"].kind == "scalar_float"
    assert infos["x_quality"].kind == "image"
    assert infos["video_preview__main"].camera == "main"
    assert infos["episode_meta"].preset is True

