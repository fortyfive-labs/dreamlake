"""Tests for dreamlake.dataset — the robot-training dataset SDK.

The anchor math gets pinned hardest: every other bug in this feature
announces itself, but a wrong anchor ingests successfully and the only
symptom is a skeleton drawn over the wrong frame, or two videos playing as
one stream. The pure-Python pieces (anchor layout, playlist parsing,
validation, the aspect pre-check) are hermetic; the end-to-end write/read
pass needs dreamdb + ffmpeg and skips cleanly where they are absent.
"""

import json
import shutil
import subprocess

import pytest

from dreamlake.dataset._ffmpeg import FfmpegError, _parse_playlist
from dreamlake.dataset._schema import (
    MAX_VIDEO_SECONDS,
    VIDEO_STRIDE_NS,
    base_anchor,
    frame_anchor,
    gid_of,
    validate_joints_pose,
    validate_subtasks,
)
from dreamlake.dataset._core import _preview_width

# ─── anchor layout ───────────────────────────────────────────────────


def test_slot_layout_round_trips_and_slots_do_not_overlap():
    for gid in (0, 1, 7, 195_000):
        assert gid_of(base_anchor(gid)) == gid

    # The last nanosecond of a slot still resolves to that slot — the
    # boundary a long video walks up to.
    last = base_anchor(4) + VIDEO_STRIDE_NS - 1
    assert gid_of(last) == 4
    assert gid_of(last + 1) == 5


def test_slot_width_matches_the_documented_per_video_limit():
    # If these disagree, add_video's length check stops protecting the
    # layout it exists to protect.
    assert VIDEO_STRIDE_NS == MAX_VIDEO_SECONDS * 1_000_000_000


def test_frame_anchors_are_exact_for_fractional_frame_rates():
    # A real annotation rate from a claru_ego capture. Frame 0 must land
    # exactly on the base or every overlay is off from the first frame.
    fps = 29.98740647455367
    assert frame_anchor(2, 0, fps) == base_anchor(2)
    assert gid_of(frame_anchor(2, 100_000, fps)) == 2
    # Monotone: a rounding scheme that ties adjacent frames would collapse
    # two annotations onto one anchor.
    anchors = [frame_anchor(0, k, fps) for k in range(200)]
    assert all(b > a for a, b in zip(anchors, anchors[1:]))


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

    assert len(frags) == 3  # the #EXT-X-MAP line must not read as a segment
    assert frags[0][1] == anchor
    assert frags[0][2] == anchor + 2_000_000_000
    assert frags[1][1] == frags[0][2]  # contiguous
    # Durations accumulate; the short tail is not rounded up.
    assert frags[2][2] == anchor + 5_500_000_000


def test_parse_playlist_widens_zero_length_instead_of_dropping(tmp_path):
    p = _write_playlist(
        tmp_path, "#EXTINF:0.000000,\nseg_00000.m4s\n#EXTINF:1.0,\nseg_00001.m4s\n"
    )
    frags = _parse_playlist(p, str(tmp_path), 0)
    assert len(frags) == 2
    assert frags[0][2] > frags[0][1]


def test_parse_playlist_refuses_an_empty_playlist(tmp_path):
    p = _write_playlist(tmp_path, "#EXTM3U\n#EXT-X-ENDLIST\n")
    with pytest.raises(FfmpegError, match="no fragments"):
        _parse_playlist(p, str(tmp_path), 0)


# ─── validation + geometry ───────────────────────────────────────────


def test_preview_width_matches_ffmpeg_scale_minus_2():
    assert _preview_width(1920, 1080, 720) == 1280
    assert _preview_width(640, 480, 720) == 960  # 4:3 ≠ 16:9 at any height


def test_annotation_validators_name_whats_missing():
    assert validate_joints_pose({"frames": {}}) is not None  # no geometry
    assert (
        validate_joints_pose(
            {"width": 1, "height": 1, "src_fps": 30.0, "frames": {}}
        )
        is None
    )
    assert validate_subtasks({}) is not None
    assert validate_subtasks({"labeled_subtasks": [{"start_sec": 0}]}) is not None
    assert (
        validate_subtasks(
            {"labeled_subtasks": [{"start_sec": 0, "end_sec": 1, "subtask": "x"}]}
        )
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


@pytestmark_e2e
def test_end_to_end_write_then_read(tmp_path):
    from dreamlake.dataset import Dataset, DatasetError

    video = str(tmp_path / "clip.mp4")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc2=duration=3:size=640x480:rate=30",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", video],
        check=True,
    )

    backend = f"file://{tmp_path}/space"
    ds = Dataset.create(backend=backend)
    with pytest.raises(DatasetError, match="already exists"):
        Dataset.create(backend=backend)

    joints = {
        "width": 640, "height": 480, "src_fps": 30.0,
        "joint_order": ["wrist"], "bones": [],
        "frames": {"0": [{"is_right": 0, "det_conf": 0.9, "keypoints_2d": [[1, 2]]}]},
    }
    subtasks = {"task": "t", "labeled_subtasks": [
        {"start_sec": 0.0, "end_sec": 3.0, "subtask": "s"}]}

    out = ds.add_video(video, video_id="clip", joints_pose=joints, subtasks=subtasks)
    assert out["gid"] == 0
    assert out["video_preview"]["fragments"] >= 1

    rows = Dataset.open(backend=backend).videos()
    assert [(r["gid"], r["video_id"]) for r in rows] == [(0, "clip")]

    # Blobs round-trip byte-exact, addressed by THEIR video's slot.
    assert ds.read_joints_pose("clip") == joints
    assert ds.read_subtasks("clip") == subtasks

    # A duplicate id is refused; a mismatched aspect ratio is refused
    # BEFORE any transcoding.
    with pytest.raises(DatasetError, match="already in this dataset"):
        ds.add_video(video, video_id="clip")
    wide = str(tmp_path / "wide.mp4")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc2=duration=2:size=1280x720:rate=30",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", wide],
        check=True,
    )
    with pytest.raises(DatasetError, match="aspect ratio"):
        ds.add_video(wide, video_id="wide")
