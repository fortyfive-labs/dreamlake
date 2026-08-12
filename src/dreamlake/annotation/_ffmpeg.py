"""Fragmenting a source video into CMAF for DreamDB ingest.

A direct port of dreamlake-cli's ``src/cli/dataset/ffmpeg.ts`` — the two must
produce interchangeable output, because an annotation may be written by both tools
and every clip on one video field must share ONE init segment. The ffmpeg
argument list below is therefore not a style choice; it is a compatibility
contract. Change it in both places or not at all.

Why the playback encode is pinned so hard: the init segment is derived from
the whole track configuration, so ANY source property that survives into the
output splits the field and makes the next video un-ingestable. Two real
captures differed in four independent ways — stream layout (2 audio + 6 data
streams vs none), handler name ("Core Media Video" vs "VideoHandler"), sample
aspect ratio, and colour tags (which move both the ``colr`` box and the SPS
inside ``avcC``) — plus frame rate, 29.987 vs 29.970. Hence: exactly one video
stream, no audio, metadata stripped, and rate/SAR/colour forced. Colour must
be set with ``setparams`` on the FRAMES — the ``-color_*`` output options do
not reach the encoder and the source's tags propagate regardless.

Alignment: annotations address frames by index against the ORIGINAL video and
the viewer maps playback time with ``round(time * src_fps)``. Resampling the
rate is safe because ``-r`` preserves WHEN content appears (drops/duplicates
frames, never shifts the timeline). Measured: 67.495 s in, 67.500 s out.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ._schema import DEFAULT_PREVIEW_FPS

_NS = 1_000_000_000


class FfmpegError(RuntimeError):
    pass


def _require(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        raise FfmpegError(f"{binary} not found on PATH — install ffmpeg (brew install ffmpeg)")
    return path


def _run(args: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def _parse_rate(raw: Optional[str]) -> float:
    """Parse ffprobe's ``num/den`` frame-rate form."""
    if not raw:
        return 0.0
    num, _, den = raw.partition("/")
    try:
        n = float(num)
        d = float(den) if den else 1.0
    except ValueError:
        return 0.0
    return n / d if d else 0.0


@dataclass
class ProbeResult:
    fps: float
    duration_sec: float
    width: int
    height: int
    codec: str


def probe(src: str) -> ProbeResult:
    """Read the source's stream properties via ffprobe."""
    _require("ffprobe")
    proc = _run([
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=avg_frame_rate,r_frame_rate,width,height,codec_name",
        "-show_entries", "format=duration",
        "-of", "json",
        src,
    ])
    if proc.returncode != 0:
        raise FfmpegError(f"ffprobe failed on {src}: {proc.stderr.strip()}")

    parsed = json.loads(proc.stdout)
    streams = parsed.get("streams") or []
    if not streams:
        raise FfmpegError(f"{src} has no video stream")
    s = streams[0]

    # avg_frame_rate over r_frame_rate: for VFR sources r_frame_rate is the
    # MAXIMUM rate ffmpeg had to represent, which can be wildly higher than
    # the real one and would skew every frame-index lookup.
    fps = _parse_rate(s.get("avg_frame_rate")) or _parse_rate(s.get("r_frame_rate"))
    if not fps:
        raise FfmpegError(f"could not determine frame rate of {src}")

    return ProbeResult(
        fps=fps,
        duration_sec=float((parsed.get("format") or {}).get("duration") or 0.0),
        width=int(s.get("width") or 0),
        height=int(s.get("height") or 0),
        codec=str(s.get("codec_name") or "unknown"),
    )


@dataclass
class FragmentedVideo:
    """One fragmenting pass: the shared init segment plus anchored fragments."""

    init_path: str
    # Ordered (fragment_path, t_start_ns, t_end_ns), already anchor-offset.
    fragments: List[Tuple[str, int, int]]
    _tmpdir: tempfile.TemporaryDirectory

    def cleanup(self) -> None:
        """Remove the temp directory once the bytes have been ingested."""
        self._tmpdir.cleanup()


def fragment_video(
    src: str,
    *,
    anchor_ns: int,
    frag_seconds: float = 2.0,
    scale_height: Optional[int] = None,
    preview_fps: float = DEFAULT_PREVIEW_FPS,
    crf: int = 23,
    encoder: str = "libx264",
) -> FragmentedVideo:
    """Fragment ``src`` into CMAF, anchored at ``anchor_ns``.

    ``scale_height`` selects the mode. ``None`` is the archival pass: a
    lossless ``-c copy`` remux keeping the source's own keyframes. A height is
    the playback pass: re-encode with the GOP pinned to ``preview_fps *
    frag_seconds`` so every fragment opens on a keyframe (MSE requires
    independently decodable segments), and every source property that could
    reach the init segment normalized away.
    """
    _require("ffmpeg")
    if not (1.0 <= frag_seconds <= 30.0):
        # spec/0007 §5.4 bounds fragment duration; outside it the
        # time-bucketing assumptions in the storage layout stop holding.
        raise FfmpegError("fragment duration must be between 1 and 30 seconds")

    tmpdir = tempfile.TemporaryDirectory(prefix="dreamlake-cmaf-")
    d = tmpdir.name
    playlist = os.path.join(d, "index.m3u8")

    args: List[str] = ["ffmpeg", "-y", "-i", src]
    if scale_height is None:
        # Archival: keep the bytes exactly, keep every stream, cut only on
        # the keyframes the source already has.
        args += ["-c", "copy"]
    else:
        # GOP derives from the OUTPUT rate so it lands on fragment boundaries
        # after resampling.
        gop = max(1, round(preview_fps * frag_seconds))
        args += [
            # Exactly one video stream, no audio. See the header note.
            "-map", "0:v:0",
            "-an",
            # Strip container metadata and pin the handler name, both of
            # which otherwise carry the source's identity into the init.
            "-map_metadata", "-1",
            "-metadata:s:v:0", "handler_name=DreamLake",
            "-fflags", "+bitexact",
            "-flags", "+bitexact",
            # `-2` keeps the width even (H.264 chroma subsampling needs it).
            # setsar/setparams normalize the two frame properties that reach
            # the init segment.
            "-vf",
            f"scale=-2:{int(scale_height)},setsar=1,"
            "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv",
            "-r", str(preview_fps),
            "-pix_fmt", "yuv420p",
            "-c:v", encoder,
            "-preset", "veryfast",
            "-crf", str(crf),
            "-profile:v", "high",
            "-level", "4.0",
            "-x264-params", f"keyint={gop}:min-keyint={gop}:scenecut=0",
        ]

    args += [
        "-f", "hls",
        "-hls_segment_type", "fmp4",
        "-hls_time", str(frag_seconds),
        "-hls_list_size", "0",
        # Every segment must stand alone for MSE to append them independently.
        "-hls_flags", "independent_segments",
        "-hls_fmp4_init_filename", "init.mp4",
        "-hls_segment_filename", os.path.join(d, "seg_%05d.m4s"),
        playlist,
    ]

    proc = _run(args)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-12:])
        tmpdir.cleanup()
        raise FfmpegError(f"ffmpeg failed on {src}:\n{tail}")

    try:
        fragments = _parse_playlist(playlist, d, anchor_ns)
    except Exception:
        tmpdir.cleanup()
        raise

    return FragmentedVideo(
        init_path=os.path.join(d, "init.mp4"),
        fragments=fragments,
        _tmpdir=tmpdir,
    )


def _parse_playlist(playlist: str, d: str, anchor_ns: int) -> List[Tuple[str, int, int]]:
    """HLS playlist → anchored fragment spans.

    Timing comes from the playlist's own ``#EXTINF`` durations accumulated in
    order, not from filenames — segments are not uniformly long (the last one
    never is, and ``-c copy`` snaps to existing keyframes), so
    ``index * frag_seconds`` would drift from real presentation time.
    """
    out: List[Tuple[str, int, int]] = []
    cum = 0.0
    pending: Optional[float] = None

    with open(playlist) as f:
        for raw in f:
            line = raw.strip()
            if line.startswith("#EXTINF:"):
                try:
                    pending = float(line[len("#EXTINF:"):].split(",")[0])
                except ValueError:
                    pending = None
                continue
            if not line or line.startswith("#"):
                continue
            if pending is None:
                continue

            t_start = anchor_ns + round(cum * _NS)
            cum += pending
            t_end = anchor_ns + round(cum * _NS)
            # A zero-length EXTINF would make the span empty, which sorts
            # ambiguously against its neighbour. Widen by 1 ns rather than
            # dropping the fragment, which would lose its bytes.
            if t_end <= t_start:
                t_end = t_start + 1
            out.append((os.path.join(d, line), t_start, t_end))
            pending = None

    if not out:
        raise FfmpegError(f"no fragments produced — {playlist} listed none")
    return out
