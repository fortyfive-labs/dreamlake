"""Lazy frame sampling: video file -> ``(t_sec, PIL.Image)`` via ffmpeg.

ffmpeg re-encodes sampled frames as an MJPEG byte stream on stdout; we
split it on the JPEG SOI/EOI markers. That split is safe here: JPEG
entropy-coded data byte-stuffs ``0xFF`` as ``0xFF 0x00``, so ``0xFF 0xD9``
can only be a real end-of-image, and ffmpeg's mjpeg output embeds no
thumbnails that could carry a nested EOI.
"""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:  # pragma: no cover
    from PIL.Image import Image

_SOI = b"\xff\xd8"  # start of image
_EOI = b"\xff\xd9"  # end of image
_CHUNK = 1 << 16


def iter_video_frames(
    path: str | Path, fps: float = 1.0
) -> Iterator[tuple[float, "Image"]]:
    """Yield ``(t_sec, PIL.Image)`` sampled at ``fps`` frames per second.

    Frames are decoded lazily as you iterate; timestamps are ``i / fps``
    (the sampling instants of ffmpeg's ``fps`` filter, starting at 0.0).
    Closing the generator early kills the ffmpeg process — no zombies.

    Requires ``ffmpeg`` on PATH.
    """
    if fps <= 0:
        raise ValueError(f"fps must be positive, got {fps}")
    path = str(path)
    if not Path(path).is_file():
        raise FileNotFoundError(f"video not found: {path}")
    if shutil.which("ffmpeg") is None:
        raise FileNotFoundError(
            "iter_video_frames needs ffmpeg on PATH. Install it with your "
            "package manager (e.g. `brew install ffmpeg` or "
            "`apt-get install ffmpeg`)."
        )

    from PIL import Image as PILImage

    cmd = [
        "ffmpeg", "-v", "error",
        "-i", path,
        "-vf", f"fps={fps}",
        "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "2",
        "-",
    ]  # fmt: skip
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None and proc.stderr is not None
    buf = bytearray()
    i = 0
    try:
        while True:
            chunk = proc.stdout.read(_CHUNK)
            if not chunk:
                break
            buf += chunk
            while True:
                start = buf.find(_SOI)
                if start < 0:
                    # No frame start yet (a marker may be split across
                    # chunks) — keep the buffer and read more.
                    break
                end = buf.find(_EOI, start + 2)
                if end < 0:
                    if start:
                        del buf[:start]
                    break
                jpeg = bytes(buf[start : end + 2])
                del buf[: end + 2]
                img = PILImage.open(io.BytesIO(jpeg))
                img.load()  # decode now, so the buffer can be released
                yield (i / fps, img)
                i += 1
        rc = proc.wait()
        if rc != 0:
            err = proc.stderr.read().decode(errors="replace").strip()
            raise RuntimeError(
                f"ffmpeg failed (exit {rc}) reading {path}: "
                f"{err[-500:] or 'no stderr output'}"
            )
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdout.close()
        proc.stderr.close()
        proc.wait()
