"""Tests for the DreamLake Python API (dreamlake.api).

Tests are organized into:
  - Unit tests: no server required (resource_id, prefix, ffmpeg)
  - Integration tests: require BSS + dreamlake-server running locally

Run unit tests only:
    pytest test/test_api.py -m "not integration"

Run all tests (requires servers):
    pytest test/test_api.py
"""

import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ═══════════════════════════════════════════════════════════════════════════════
# Unit tests — no server required
# ═══════════════════════════════════════════════════════════════════════════════


class TestResourceId:
    """Test resource ID encoding/decoding."""

    def test_encode_video(self):
        from dreamlake.api.resource_id import encode_resource_id
        rid = encode_resource_id("video", "69e7264a4357796451fa458b")
        assert rid.startswith("v-")
        assert len(rid) == 2 + 16  # "v-" + 16 chars

    def test_encode_audio(self):
        from dreamlake.api.resource_id import encode_resource_id
        rid = encode_resource_id("audio", "69e7264a4357796451fa458b")
        assert rid.startswith("a-")

    def test_encode_text_track(self):
        from dreamlake.api.resource_id import encode_resource_id
        rid = encode_resource_id("text-track", "69e7264a4357796451fa458b")
        assert rid.startswith("tt-")

    def test_roundtrip(self):
        from dreamlake.api.resource_id import encode_resource_id, decode_resource_id
        original_id = "69e7264a4357796451fa458b"
        rid = encode_resource_id("video", original_id)
        asset_type, decoded_id = decode_resource_id(rid)
        assert asset_type == "video"
        assert decoded_id == original_id

    def test_decode_invalid(self):
        from dreamlake.api.resource_id import decode_resource_id
        with pytest.raises(ValueError):
            decode_resource_id("invalid-id")

    def test_encode_unknown_type(self):
        from dreamlake.api.resource_id import encode_resource_id
        with pytest.raises(ValueError):
            encode_resource_id("unknown", "69e7264a4357796451fa458b")

    def test_parse_uri_resource_id(self):
        from dreamlake.api.resource_id import parse_uri, encode_resource_id
        rid = encode_resource_id("video", "69e7264a4357796451fa458b")
        parsed = parse_uri(rid)
        assert parsed["scheme"] == "resource"
        assert parsed["type"] == "video"

    def test_parse_uri_bss(self):
        from dreamlake.api.resource_id import parse_uri
        parsed = parse_uri("bss://localhost:10234/videos/69e7264a")
        assert parsed["scheme"] == "bss"
        assert parsed["host"] == "localhost:10234"
        assert parsed["id"] == "69e7264a"

    def test_parse_uri_file(self):
        from dreamlake.api.resource_id import parse_uri
        parsed = parse_uri("file:///tmp/video.mp4")
        assert parsed["scheme"] == "file"
        assert parsed["path"] == "/tmp/video.mp4"

    def test_parse_uri_s3(self):
        from dreamlake.api.resource_id import parse_uri
        parsed = parse_uri("s3://my-bucket/path/video.mp4")
        assert parsed["scheme"] == "s3"
        assert parsed["bucket"] == "my-bucket"
        assert parsed["key"] == "path/video.mp4"

    def test_parse_uri_unknown(self):
        from dreamlake.api.resource_id import parse_uri
        with pytest.raises(ValueError):
            parse_uri("ftp://unknown")


class TestPrefix:
    """Test Prefix context manager."""

    def test_basic(self):
        from dreamlake.api.prefix import Prefix, resolve_space, resolve_path
        with Prefix(space="robotics@alice", prefix="/2026/04/run-042"):
            assert resolve_space() == "robotics@alice"
            assert resolve_path("camera/front") == "/2026/04/run-042/camera/front"

    def test_absolute_path_ignores_prefix(self):
        from dreamlake.api.prefix import Prefix, resolve_path
        with Prefix(prefix="/2026/04/run-042"):
            assert resolve_path("/shared/ref.mp4") == "/shared/ref.mp4"

    def test_nested(self):
        from dreamlake.api.prefix import Prefix, resolve_space, resolve_path
        with Prefix(space="robotics@alice", prefix="/2026/04"):
            with Prefix(prefix="run-042"):
                assert resolve_space() == "robotics@alice"
                assert resolve_path("camera") == "/2026/04/run-042/camera"
            # Back to outer
            assert resolve_path("camera") == "/2026/04/camera"

    def test_space_override(self):
        from dreamlake.api.prefix import Prefix, resolve_space
        with Prefix(space="outer"):
            with Prefix(space="inner"):
                assert resolve_space() == "inner"
            assert resolve_space() == "outer"

    def test_no_context(self):
        from dreamlake.api.prefix import resolve_space, resolve_path
        assert resolve_space() is None
        assert resolve_path("test") == "test"

    def test_resolve_space_explicit(self):
        from dreamlake.api.prefix import Prefix, resolve_space
        with Prefix(space="context-space"):
            assert resolve_space("explicit") == "explicit"
            assert resolve_space() == "context-space"


class TestFfmpeg:
    """Test ffmpeg helpers. Requires ffmpeg installed."""

    @pytest.fixture
    def test_video(self, tmp_path):
        """Create a small test video with ffmpeg."""
        import subprocess
        out = tmp_path / "test.mp4"
        result = subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "testsrc=duration=2:size=160x120:rate=10",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out),
        ], capture_output=True, timeout=10)
        if result.returncode != 0:
            pytest.skip("ffmpeg not available")
        return str(out)

    def test_probe(self, test_video):
        from dreamlake.api._ffmpeg import probe
        info = probe(test_video)
        assert info["duration"] == pytest.approx(2.0, abs=0.1)
        assert info["width"] == 160
        assert info["height"] == 120
        assert info["fps"] == pytest.approx(10.0, abs=0.1)

    def test_extract_frame_at(self, test_video):
        from dreamlake.api._ffmpeg import extract_frame_at
        png = extract_frame_at(test_video, 0.5)
        assert len(png) > 0
        img = Image.open(io.BytesIO(png))
        assert img.size == (160, 120)

    def test_extract_frames(self, test_video):
        from dreamlake.api._ffmpeg import extract_frames
        frames = extract_frames(test_video, start=0, end=1.0)
        assert len(frames) == 10  # 1s at 10fps

    def test_extract_frames_with_offset(self, test_video):
        from dreamlake.api._ffmpeg import extract_frames
        frames = extract_frames(test_video, start=0.5, end=1.0)
        assert len(frames) == 5  # 0.5s at 10fps

    def test_extract_frames_with_fps(self, test_video):
        from dreamlake.api._ffmpeg import extract_frames
        frames = extract_frames(test_video, start=0, end=2.0, fps=5)
        assert len(frames) == 10  # 2s at 5fps


class TestVideoUnit:
    """Unit tests for Video object (no server)."""

    def test_repr_no_meta(self):
        from dreamlake.api.video import Video
        v = Video("bss://localhost:10234/videos/test123")
        r = repr(v)
        assert "Video" in r
        assert "test123" in r

    def test_slice_returns_video(self):
        from dreamlake.api.video import Video
        v = Video("bss://localhost:10234/videos/test123", _meta={
            "fps": 30, "st": 0, "durationSec": 60, "streams": [],
        }, _st=0, _et=60)
        clip = v[10.0:20.0]
        assert isinstance(clip, Video)
        assert clip.st == 10.0
        assert clip.et == 20.0
        assert clip.duration == pytest.approx(10.0)

    def test_slice_int_frames(self):
        from dreamlake.api.video import Video
        v = Video("bss://localhost:10234/videos/test123", _meta={
            "fps": 30, "st": 0, "durationSec": 60, "streams": [],
        }, _st=0, _et=60)
        clip = v[300:600]
        assert clip.duration == pytest.approx(10.0)

    def test_relative_slicing(self):
        from dreamlake.api.video import Video
        v = Video("bss://localhost:10234/videos/test123", _meta={
            "fps": 10, "st": 0, "durationSec": 60, "streams": [],
        }, _st=0, _et=60)
        clip = v[10.0:20.0]
        sub = clip[2.0:5.0]
        assert sub.st == pytest.approx(12.0)
        assert sub.et == pytest.approx(15.0)

    def test_chunk(self):
        from dreamlake.api.video import Video, VideoArray
        v = Video("bss://localhost:10234/videos/test123", _meta={
            "fps": 10, "st": 0, "durationSec": 10, "streams": [],
        }, _st=0, _et=10)
        chunks = v.chunk(2.0)
        assert isinstance(chunks, VideoArray)
        assert len(chunks) == 5
        assert chunks[0].st == 0.0
        assert chunks[0].et == 2.0
        assert chunks[4].st == 8.0
        assert chunks[4].et == 10.0

    def test_properties(self):
        from dreamlake.api.video import Video
        v = Video("bss://localhost:10234/videos/test123", _meta={
            "fps": 30, "st": 0, "durationSec": 60, "width": 1920, "height": 1080,
            "streams": [],
        }, _st=0, _et=60)
        assert v.fps == 30
        assert v.duration == 60.0
        assert v.frames == 1800
        assert v.width == 1920
        assert v.height == 1080


class TestVideoArrayUnit:
    """Unit tests for VideoArray."""

    def _make_array(self, n=5):
        from dreamlake.api.video import Video, VideoArray
        videos = []
        for i in range(n):
            v = Video("bss://localhost:10234/videos/test", _meta={
                "fps": 10, "st": 0, "durationSec": 10, "streams": [],
            }, _st=i * 2.0, _et=(i + 1) * 2.0)
            videos.append(v)
        return VideoArray(videos)

    def test_len(self):
        arr = self._make_array(5)
        assert len(arr) == 5

    def test_int_index(self):
        from dreamlake.api.video import Video
        arr = self._make_array(5)
        v = arr[0]
        assert isinstance(v, Video)
        assert v.st == 0.0

    def test_slice_index(self):
        from dreamlake.api.video import VideoArray
        arr = self._make_array(5)
        sub = arr[1:3]
        assert isinstance(sub, VideoArray)
        assert len(sub) == 2

    def test_fancy_index(self):
        from dreamlake.api.video import VideoArray
        arr = self._make_array(5)
        result = arr[:, 0]
        assert isinstance(result, VideoArray)
        assert len(result) == 5

    def test_iter(self):
        arr = self._make_array(3)
        items = list(arr)
        assert len(items) == 3


# ═══════════════════════════════════════════════════════════════════════════════
# Integration tests — require BSS + dreamlake-server
# ═══════════════════════════════════════════════════════════════════════════════

BSS_URL = os.environ.get("DREAMLAKE_BSS_URL", "http://localhost:10234")
# Use a known video ID from the test data
TEST_VIDEO_ID = os.environ.get("TEST_VIDEO_ID", "69e7264a4357796451fa4592")


def _bss_available():
    import httpx
    try:
        r = httpx.get(f"{BSS_URL}/health", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")


def _qdrant_available():
    import httpx
    try:
        r = httpx.get(f"{QDRANT_URL}/collections", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


integration = pytest.mark.skipif(not _bss_available(), reason="BSS not available")
qdrant_required = pytest.mark.skipif(not _qdrant_available(), reason="Qdrant not available")


@integration
class TestVideoIntegration:
    """Integration tests for Video with real BSS server."""

    def test_load_video(self):
        import dreamlake as dl
        video = dl.load_video(f"bss://{BSS_URL.split('//')[1]}/videos/{TEST_VIDEO_ID}")
        assert video.fps > 0
        assert video.duration > 0

    def test_thumbnail(self):
        import dreamlake as dl
        video = dl.load_video(f"bss://{BSS_URL.split('//')[1]}/videos/{TEST_VIDEO_ID}")
        thumb = video.thumbnail
        assert isinstance(thumb, Image.Image)
        assert thumb.size[0] > 0

    def test_single_frame(self):
        import dreamlake as dl
        video = dl.load_video(f"bss://{BSS_URL.split('//')[1]}/videos/{TEST_VIDEO_ID}")
        frame = video[0].image
        assert isinstance(frame, Image.Image)

    def test_numpy(self):
        import dreamlake as dl
        video = dl.load_video(f"bss://{BSS_URL.split('//')[1]}/videos/{TEST_VIDEO_ID}")
        clip = video[0.0:0.5]
        arr = clip.numpy()
        assert isinstance(arr, np.ndarray)
        assert arr.ndim == 4  # (N, H, W, 3)
        assert arr.shape[-1] == 3

    def test_chunk_and_fancy_index(self):
        import dreamlake as dl
        video = dl.load_video(f"bss://{BSS_URL.split('//')[1]}/videos/{TEST_VIDEO_ID}")
        clip = video[0.0:1.0]
        chunks = clip.chunk(0.5)
        assert len(chunks) == 2

        first_frames = chunks[:, 0]
        assert len(first_frames) == 2

        arr = first_frames.numpy()
        assert arr.shape[0] == 2

    def test_chunk_numpy_batch(self):
        import dreamlake as dl
        video = dl.load_video(f"bss://{BSS_URL.split('//')[1]}/videos/{TEST_VIDEO_ID}")
        chunks = video[0.0:1.0].chunk(0.5)
        arr = chunks.numpy()
        assert arr.ndim == 5  # (batch, frames, H, W, 3)
        assert arr.shape[0] == 2

    def test_slice_then_slice(self):
        import dreamlake as dl
        video = dl.load_video(f"bss://{BSS_URL.split('//')[1]}/videos/{TEST_VIDEO_ID}")
        clip = video[0.0:5.0]
        sub = clip[1.0:3.0]
        assert sub.duration == pytest.approx(2.0)
        thumb = sub.thumbnail
        assert isinstance(thumb, Image.Image)


@integration
@qdrant_required
class TestVectorIndexIntegration:
    """Integration tests for VectorIndex with Qdrant."""

    def test_create_and_add(self):
        import dreamlake as dl
        index = dl.vec_index("test-api-index")
        vec = np.random.randn(768).astype(np.float32)
        index.add(vector=vec, caption="test caption", st=0.0, et=2.0)
        assert index.count >= 1

    def test_search(self):
        import dreamlake as dl
        index = dl.vec_index("test-api-index")
        # Add a vector first
        vec = np.random.randn(768).astype(np.float32)
        index.add(vector=vec, caption="robot arm picking up cup")

        # Search with vector
        results = index.search(vec, limit=5)
        assert len(results) >= 1
        assert results[0].score > 0

    def test_add_with_source(self):
        import dreamlake as dl
        video = dl.load_video(f"bss://{BSS_URL.split('//')[1]}/videos/{TEST_VIDEO_ID}")
        clip = video[0.0:2.0]

        index = dl.vec_index("test-api-source")
        vec = np.random.randn(768).astype(np.float32)
        index.add(vector=vec, source=clip, caption="test with source")
        assert index.count >= 1


@integration
class TestTextTrackIntegration:
    """Integration tests for TextTrack."""

    def test_create_and_add(self):
        import dreamlake as dl
        track = dl.text_track(prefix="/test/captions", space="robotics@tom-tao-6f6b82")
        track.add("First caption", st=0.0, et=2.0)
        track.add("Second caption", st=2.0, et=4.0)
        assert track.count == 2

    def test_add_with_source(self):
        import dreamlake as dl
        video = dl.load_video(f"bss://{BSS_URL.split('//')[1]}/videos/{TEST_VIDEO_ID}")
        clip = video[0.0:2.0]

        track = dl.text_track(prefix="/test/captions-source", space="robotics@tom-tao-6f6b82")
        track.add("Robot arm caption", source=clip)
        assert track.count == 1


@integration
class TestPrefixIntegration:
    """Integration tests for Prefix context with real uploads."""

    def test_prefix_with_text_track(self):
        import dreamlake as dl
        with dl.Prefix(space="robotics@tom-tao-6f6b82", prefix="/2026/04/run-test"):
            track = dl.text_track(path="captions/test")
            assert track.prefix == "/2026/04/run-test/captions/test"
            assert track.space == "robotics@tom-tao-6f6b82"


class TestTopLevelImports:
    """Test that all top-level imports work."""

    def test_imports(self):
        import dreamlake as dl
        assert hasattr(dl, "Video")
        assert hasattr(dl, "VideoArray")
        assert hasattr(dl, "TextTrack")
        assert hasattr(dl, "VectorIndex")
        assert hasattr(dl, "Prefix")
        assert hasattr(dl, "load_video")
        assert hasattr(dl, "load")
        assert hasattr(dl, "upload")
        assert hasattr(dl, "text_track")
        assert hasattr(dl, "vec_index")

    def test_load_returns_video(self):
        import dreamlake as dl
        from dreamlake.api.resource_id import encode_resource_id
        rid = encode_resource_id("video", "69e7264a4357796451fa458b")
        result = dl.load(rid)
        assert isinstance(result, dl.Video)


import io  # for TestFfmpeg
