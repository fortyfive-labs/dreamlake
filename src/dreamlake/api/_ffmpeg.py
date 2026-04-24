"""
FFmpeg helpers for local frame extraction.

Requires ffmpeg in PATH.
"""

import os
import subprocess
import tempfile


def _find_ffmpeg() -> str:
    for name in ("ffmpeg", "ffmpeg.exe"):
        try:
            subprocess.run([name, "-version"], capture_output=True, timeout=5)
            return name
        except FileNotFoundError:
            continue
    raise RuntimeError(
        "ffmpeg not found. Install it: brew install ffmpeg (macOS) "
        "or apt install ffmpeg (Linux)"
    )


_FFMPEG: str | None = None


def _ffmpeg() -> str:
    global _FFMPEG
    if _FFMPEG is None:
        _FFMPEG = _find_ffmpeg()
    return _FFMPEG


def probe(file_path: str) -> dict:
    """Probe a video file for duration, fps, width, height."""
    try:
        out = subprocess.run(
            [_ffmpeg().replace("ffmpeg", "ffprobe"), "-v", "quiet",
             "-print_format", "json", "-show_format", "-show_streams", file_path],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0:
            return {}
        import json
        data = json.loads(out.stdout)
        vs = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
        fmt = data.get("format", {})
        result = {"duration": float(fmt.get("duration", 0))}
        if vs:
            result["width"] = int(vs.get("width", 0))
            result["height"] = int(vs.get("height", 0))
            fr = vs.get("avg_frame_rate", "0/1")
            num, den = fr.split("/")
            if int(den) > 0:
                result["fps"] = int(num) / int(den)
        return result
    except Exception:
        return {}


def extract_frame_at(file_path: str, time_sec: float) -> bytes:
    """Extract a single frame at a given time offset. Returns PNG bytes."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        out_path = f.name

    try:
        subprocess.run(
            [_ffmpeg(), "-ss", str(time_sec), "-i", file_path,
             "-frames:v", "1", "-update", "1", "-y", out_path],
            capture_output=True, timeout=10,
        )
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            with open(out_path, "rb") as f:
                return f.read()
        # Fallback: extract first frame if seek failed
        subprocess.run(
            [_ffmpeg(), "-i", file_path,
             "-frames:v", "1", "-update", "1", "-y", out_path],
            capture_output=True, timeout=10,
        )
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            with open(out_path, "rb") as f:
                return f.read()
        return b""
    finally:
        if os.path.exists(out_path):
            os.unlink(out_path)


def extract_frames(file_path: str, start: float = 0, end: float | None = None,
                   fps: float | None = None) -> list[bytes]:
    """Extract multiple frames from a video file. Returns list of PNG bytes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = [_ffmpeg(), "-i", file_path]
        if start > 0:
            cmd = [_ffmpeg(), "-ss", str(start), "-i", file_path]
        if end is not None:
            cmd += ["-t", str(end - start)]

        vf = f"fps={fps}" if fps else None
        if vf:
            cmd += ["-vf", vf]

        pattern = os.path.join(tmpdir, "frame_%05d.png")
        cmd += ["-y", pattern]
        subprocess.run(cmd, capture_output=True, timeout=60)

        frames = []
        for name in sorted(os.listdir(tmpdir)):
            if name.endswith(".png"):
                with open(os.path.join(tmpdir, name), "rb") as f:
                    frames.append(f.read())
        return frames
