"""Tests for the DreamLake Python API (dreamlake.api).

Tests are organized into:
  - Unit tests: no server required (resource_id, prefix, ffmpeg)
  - Integration tests: require dreamlake-server running locally

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
        from dreamlake.api.prefix import Prefix, resolve_project, resolve_path
        with Prefix(project="robotics@alice", prefix="/2026/04/run-042"):
            assert resolve_project() == "robotics@alice"
            assert resolve_path("camera/front") == "/2026/04/run-042/camera/front"

    def test_absolute_path_ignores_prefix(self):
        from dreamlake.api.prefix import Prefix, resolve_path
        with Prefix(prefix="/2026/04/run-042"):
            assert resolve_path("/shared/ref.mp4") == "/shared/ref.mp4"

    def test_nested(self):
        from dreamlake.api.prefix import Prefix, resolve_project, resolve_path
        with Prefix(project="robotics@alice", prefix="/2026/04"):
            with Prefix(prefix="run-042"):
                assert resolve_project() == "robotics@alice"
                assert resolve_path("camera") == "/2026/04/run-042/camera"
            # Back to outer
            assert resolve_path("camera") == "/2026/04/camera"

    def test_project_override(self):
        from dreamlake.api.prefix import Prefix, resolve_project
        with Prefix(project="outer"):
            with Prefix(project="inner"):
                assert resolve_project() == "inner"
            assert resolve_project() == "outer"

    def test_no_context(self):
        from dreamlake.api.prefix import resolve_project, resolve_path
        assert resolve_project() is None
        assert resolve_path("test") == "test"

    def test_resolve_project_explicit(self):
        from dreamlake.api.prefix import Prefix, resolve_project
        with Prefix(project="context-project"):
            assert resolve_project("explicit") == "explicit"
            assert resolve_project() == "context-project"


class TestFfmpeg:
    """Test ffmpeg helpers. Requires ffmpeg installed."""

    @pytest.fixture
    def test_video(self, tmp_path):
        """Create a small test video with ffmpeg."""
        import shutil
        import subprocess
        if shutil.which("ffmpeg") is None:
            pytest.skip("ffmpeg not installed (needed to synthesize the test video)")
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




# ═══════════════════════════════════════════════════════════════════════════════
# Integration tests — require a reachable dreamlake-server
#
# Opt-in: set DREAMLAKE_REMOTE to enable. Without it they are skipped.
# ═══════════════════════════════════════════════════════════════════════════════



QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")


def _qdrant_available():
    import httpx
    try:
        r = httpx.get(f"{QDRANT_URL}/collections", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def _server_available():
    if not SERVER_URL:
        return False
    import httpx
    try:
        return httpx.get(f"{SERVER_URL}/health", timeout=2).status_code == 200
    except Exception:
        return False


SERVER_URL = os.environ.get("DREAMLAKE_REMOTE")
# Gated on dreamlake-server, not BSS. These tests never needed BSS itself —
# they were gated on it because the video tests next to them did, and those
# are gone. TextTrack now uploads through the server like everything else.
integration = pytest.mark.skipif(
    not _server_available(),
    reason="integration tests are opt-in (set DREAMLAKE_REMOTE)",
)
qdrant_required = pytest.mark.skipif(not _qdrant_available(), reason="Qdrant not available")


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


@integration
class TestTextTrackIntegration:
    """Integration tests for TextTrack."""

    def test_create_and_add(self):
        import dreamlake as dl
        track = dl.text_track(prefix="/test/captions", project="robotics@tom-tao-6f6b82")
        track.add("First caption", st=0.0, et=2.0)
        track.add("Second caption", st=2.0, et=4.0)
        assert track.count == 2


@integration
class TestPrefixIntegration:
    """Integration tests for Prefix context with real uploads."""

    def test_prefix_with_text_track(self):
        import dreamlake as dl
        with dl.Prefix(project="robotics@tom-tao-6f6b82", prefix="/2026/04/run-test"):
            track = dl.text_track(path="captions/test")
            assert track.prefix == "/2026/04/run-test/captions/test"
            assert track.project == "robotics@tom-tao-6f6b82"


class TestTopLevelImports:
    """Test that all top-level imports work."""

    def test_imports(self):
        import dreamlake as dl
        assert hasattr(dl, "TextTrack")
        assert hasattr(dl, "VectorIndex")
        assert hasattr(dl, "Prefix")
        assert hasattr(dl, "upload")
        assert hasattr(dl, "text_track")
        assert hasattr(dl, "vec_index")


import io  # for TestFfmpeg
