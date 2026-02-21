"""
Example Timestamp Pipeline

Generates timestamp labels for video frames at regular intervals.
Output format: start_ms<TAB>end_ms<TAB>line_num<TAB>text

Usage via CLI:
    dreamlake pipeline run --id pipeline:example-timestamp video.mp4
"""

import json
import subprocess
from pathlib import Path
from typing import Optional

DESCRIPTION = "Generate timestamp labels for video frames"
OPTIONS = {
    "interval": "Sample interval in seconds (default: 1.0)",
    "format": "Output format: tsv, jsonl, srt, vtt (default: tsv)",
    "output": "Output file path (default: <video>.labels.<format>)",
}


def _get_video_info(video_path: Path) -> dict:
    """Get video metadata using ffprobe."""
    try:
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(video_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"Warning: ffprobe failed: {result.stderr}")
            return {}
        return json.loads(result.stdout)
    except FileNotFoundError:
        print("Warning: ffprobe not found, using defaults")
        return {}
    except json.JSONDecodeError:
        return {}


def _extract_video_stream(info: dict) -> dict:
    """Extract video stream info."""
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            return stream
    return {}


def _generate_segments(video_path: Path, interval: float, verbose: bool = False) -> list:
    """Generate text segments for the video."""
    info = _get_video_info(video_path)
    video_stream = _extract_video_stream(info)

    # Get video properties
    duration_str = info.get("format", {}).get("duration", "0")
    duration_ms = int(float(duration_str) * 1000)

    # Parse frame rate
    fps_str = video_stream.get("r_frame_rate", "30/1")
    if "/" in fps_str:
        num, den = fps_str.split("/")
        fps = float(num) / float(den) if float(den) != 0 else 30.0
    else:
        fps = float(fps_str) if fps_str else 30.0

    width = video_stream.get("width", 0)
    height = video_stream.get("height", 0)

    if verbose:
        print(f"Video: {video_path.name}")
        print(f"  Duration: {duration_ms}ms ({duration_ms/1000:.2f}s)")
        print(f"  FPS: {fps:.2f}")
        print(f"  Resolution: {width}x{height}")

    segments = []
    line_num = 0
    frame_interval_ms = int(interval * 1000)

    current_ms = 0
    frame_num = 0

    while current_ms < duration_ms:
        end_ms = min(current_ms + frame_interval_ms, duration_ms)
        time_sec = current_ms / 1000.0

        # Generate frame description
        text = f"frame {frame_num} @ {time_sec:.3f}s"

        segment = {
            "line_num": line_num,
            "start_ms": current_ms,
            "end_ms": end_ms,
            "start_frame": frame_num,
            "end_frame": int(end_ms * fps / 1000),
            "text": text,
        }
        segments.append(segment)

        current_ms = end_ms
        frame_num = int(current_ms * fps / 1000)
        line_num += 1

    return segments


def _format_tsv(segments: list) -> str:
    """Format segments as TSV."""
    lines = ["start_ms\tend_ms\tline_num\ttext"]
    for seg in segments:
        lines.append(f"{seg['start_ms']}\t{seg['end_ms']}\t{seg['line_num']}\t{seg['text']}")
    return "\n".join(lines)


def _format_jsonl(segments: list) -> str:
    """Format segments as JSONL."""
    lines = []
    for seg in segments:
        lines.append(json.dumps(seg))
    return "\n".join(lines)


def _format_srt(segments: list) -> str:
    """Format segments as SRT subtitles."""
    def ms_to_srt_time(ms: int) -> str:
        hours = ms // 3600000
        minutes = (ms % 3600000) // 60000
        seconds = (ms % 60000) // 1000
        millis = ms % 1000
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

    lines = []
    for i, seg in enumerate(segments, 1):
        lines.append(str(i))
        lines.append(f"{ms_to_srt_time(seg['start_ms'])} --> {ms_to_srt_time(seg['end_ms'])}")
        lines.append(seg['text'])
        lines.append("")
    return "\n".join(lines)


def _format_vtt(segments: list) -> str:
    """Format segments as WebVTT."""
    def ms_to_vtt_time(ms: int) -> str:
        hours = ms // 3600000
        minutes = (ms % 3600000) // 60000
        seconds = (ms % 60000) // 1000
        millis = ms % 1000
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"

    lines = ["WEBVTT", ""]
    for seg in segments:
        lines.append(f"{ms_to_vtt_time(seg['start_ms'])} --> {ms_to_vtt_time(seg['end_ms'])}")
        lines.append(seg['text'])
        lines.append("")
    return "\n".join(lines)


def run(
    video: str,
    output: Optional[str] = None,
    format: str = "tsv",
    interval: float = 1.0,
    dry_run: bool = False,
    verbose: bool = False,
    **kwargs,
) -> int:
    """
    Run the example-timestamp pipeline.

    Args:
        video: Path to video file
        output: Output file path (default: <video>.labels.<format>)
        format: Output format (tsv, jsonl, srt, vtt)
        interval: Sample interval in seconds
        dry_run: Show what would be done without writing
        verbose: Show verbose output

    Returns:
        Exit code (0 for success)
    """
    video_path = Path(video)

    if not video_path.exists():
        print(f"Error: Video not found: {video_path}")
        return 1

    if video_path.is_dir():
        print(f"Error: Directory not supported, specify a video file")
        return 1

    # Determine output path
    if output:
        output_path = Path(output)
    else:
        ext = {"tsv": "tsv", "jsonl": "jsonl", "srt": "srt", "vtt": "vtt"}.get(format, "tsv")
        output_path = video_path.with_suffix(f".labels.{ext}")

    if verbose:
        print(f"Pipeline: example-timestamp")
        print(f"Input: {video_path}")
        print(f"Output: {output_path}")
        print(f"Format: {format}")
        print(f"Interval: {interval}s")

    # Generate segments
    segments = _generate_segments(video_path, interval, verbose)

    if verbose:
        print(f"Generated {len(segments)} segments")

    # Format output
    formatters = {
        "tsv": _format_tsv,
        "jsonl": _format_jsonl,
        "srt": _format_srt,
        "vtt": _format_vtt,
    }
    formatter = formatters.get(format, _format_tsv)
    output_content = formatter(segments)

    if dry_run:
        print(f"\n[DRY RUN] Would write to: {output_path}")
        print(f"[DRY RUN] Content preview ({len(segments)} segments):")
        preview_lines = output_content.split("\n")[:10]
        for line in preview_lines:
            print(f"  {line}")
        if len(output_content.split("\n")) > 10:
            print(f"  ... ({len(segments)} total segments)")
        return 0

    # Write output
    output_path.write_text(output_content)
    print(f"Wrote {len(segments)} segments to: {output_path}")

    return 0
