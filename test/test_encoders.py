"""Tests for dreamlake.encoders — pure encoding, no dataset/storage coupling.

Three gates, in increasing cost: torch+transformers installed (the [search]
extra), ffmpeg on PATH (frame sampling), and DREAMLAKE_SEARCH_TESTS=1
(model smokes — first run downloads ~500MB of weights to the HF cache).
Each tier skips cleanly where its prerequisites are absent.
"""

import os
import shutil
import subprocess

import numpy as np
import pytest

_has_ffmpeg = shutil.which("ffmpeg") is not None
try:
    import torch  # noqa: F401
    import transformers  # noqa: F401
    from PIL import Image

    _has_deps = True
except ImportError:
    _has_deps = False

needs_deps = pytest.mark.skipif(
    not _has_deps, reason="needs torch+transformers (pip install 'dreamlake[search]')"
)
needs_ffmpeg = pytest.mark.skipif(
    not (_has_deps and _has_ffmpeg), reason="needs [search] deps and ffmpeg"
)
needs_models = pytest.mark.skipif(
    not (_has_deps and os.environ.get("DREAMLAKE_SEARCH_TESTS") == "1"),
    reason="set DREAMLAKE_SEARCH_TESTS=1 to run model-download smokes",
)


# ─── import gate ─────────────────────────────────────────────────────
# The negative path — ImportError carrying the actionable
# pip install "dreamlake[search]" hint — can't be exercised from a venv
# where the deps ARE installed without uninstalling them mid-run, so it
# is verified by inspection of encoders/__init__.py rather than a test.


@needs_deps
def test_package_imports_and_constructors_are_free():
    from dreamlake.encoders import ClipEncoder, TextEncoder, iter_video_frames

    assert callable(iter_video_frames)
    # Constructing must not load (or download) any model.
    enc, txt = ClipEncoder(), TextEncoder()
    assert enc._model is None and txt._model is None
    # model_name / device overrides are plain attribute plumbing.
    assert ClipEncoder(model_name="x", device="cpu").device == "cpu"
    assert TextEncoder(model_name="y", device="cpu").model_name == "y"


# ─── frame sampling (needs ffmpeg) ───────────────────────────────────


def _make_clip(tmp_path, seconds=2):
    clip = str(tmp_path / "clip.mp4")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", f"testsrc2=duration={seconds}:size=320x240:rate=30",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", clip],
        check=True,
    )  # fmt: skip
    return clip


@needs_ffmpeg
def test_iter_video_frames_samples_at_requested_fps(tmp_path):
    from dreamlake.encoders import iter_video_frames

    clip = _make_clip(tmp_path, seconds=2)
    frames = list(iter_video_frames(clip, fps=1.0))

    assert [t for t, _ in frames] == [0.0, 1.0]  # exactly 2 frames at 1 fps
    for _, img in frames:
        assert img.size == (320, 240)
        assert np.asarray(img).shape == (240, 320, 3)  # decodable pixels


@needs_ffmpeg
def test_iter_video_frames_early_close_reaps_ffmpeg(tmp_path):
    from dreamlake.encoders import iter_video_frames

    clip = _make_clip(tmp_path, seconds=2)
    gen = iter_video_frames(clip, fps=30.0)
    t, img = next(gen)
    assert t == 0.0 and img.size == (320, 240)
    gen.close()  # must kill+reap ffmpeg without raising


@needs_deps
def test_iter_video_frames_errors_are_actionable(tmp_path, monkeypatch):
    from dreamlake.encoders import _video
    from dreamlake.encoders import iter_video_frames

    with pytest.raises(FileNotFoundError, match="video not found"):
        next(iter_video_frames(str(tmp_path / "nope.mp4")))

    (tmp_path / "real.mp4").write_bytes(b"not a video")
    monkeypatch.setattr(_video.shutil, "which", lambda _: None)
    with pytest.raises(FileNotFoundError, match=r"ffmpeg on PATH"):
        next(iter_video_frames(str(tmp_path / "real.mp4")))


@needs_ffmpeg
def test_iter_video_frames_surfaces_ffmpeg_failure(tmp_path):
    from dreamlake.encoders import iter_video_frames

    garbage = tmp_path / "garbage.mp4"
    garbage.write_bytes(b"\x00" * 1024)
    with pytest.raises(RuntimeError, match="ffmpeg failed"):
        list(iter_video_frames(str(garbage)))


# ─── model smokes (download weights; opt-in) ─────────────────────────


@needs_models
def test_clip_shapes_norms_and_ranking():
    from dreamlake.encoders import ClipEncoder

    red = Image.new("RGB", (224, 224), (255, 0, 0))
    green = Image.new("RGB", (224, 224), (0, 160, 60))
    enc = ClipEncoder()

    iv = enc.encode_images([red, green])
    tv = enc.encode_text(["a red square", "a photo of the ocean"])
    assert iv.shape == (2, 512) and iv.dtype == np.float32
    assert tv.shape == (2, 512) and tv.dtype == np.float32
    assert np.allclose(np.linalg.norm(iv, axis=1), 1.0, atol=1e-3)
    assert np.allclose(np.linalg.norm(tv, axis=1), 1.0, atol=1e-3)

    # Sanity ranking: the all-red image matches "a red square" better
    # than "a photo of the ocean".
    sims = iv @ tv.T
    assert sims[0, 0] > sims[0, 1]

    one = enc.encode_text("a red square")
    assert one.shape == (512,)
    assert np.allclose(one, tv[0], atol=1e-5)  # str and [str] agree


@needs_models
def test_bge_shapes_norms_and_ranking():
    from dreamlake.encoders import TextEncoder

    txt = TextEncoder()
    v = txt.encode(["washing dishes", "rinse the bowl", "tighten a screw"])
    assert v.shape == (3, 384) and v.dtype == np.float32
    assert np.allclose(np.linalg.norm(v, axis=1), 1.0, atol=1e-3)

    query, close, far = v
    assert query @ close > query @ far

    one = txt.encode("washing dishes")
    assert one.shape == (384,)
    assert np.allclose(one, v[0], atol=1e-5)


@needs_models
def test_batching_matches_single_batch():
    # 5 texts through batch_size=2 must equal one big batch — padding
    # differences across batches must not change the vectors materially.
    from dreamlake.encoders import TextEncoder

    texts = ["a", "bb ccc", "dddd", "e f g h", "long " * 50]
    small = TextEncoder(batch_size=2).encode(texts)
    big = TextEncoder(batch_size=64).encode(texts)
    assert small.shape == big.shape == (5, 384)
    assert np.allclose(small, big, atol=1e-4)
