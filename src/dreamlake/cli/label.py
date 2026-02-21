"""
Label command for dreamlake CLI.

Generate text tracks with frame descriptions from video files.

Usage:
    dreamlake label video.mp4 --output labels.tsv
    dreamlake label video.mp4 --interval 1.0 --format jsonl
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

from params_proto import proto


@proto
class LabelCommand:
    """
    Generate text track labels from video files.

    Creates a text file with start/end timestamps and frame descriptions.
    Output format: start_ms<TAB>end_ms<TAB>text

    Reference format for linking: track:<track_id>:line:<line_num>
    """

    video: str = "."
    """Path to video file or directory containing videos"""

    output: Optional[str] = None
    """Output file path (default: <video>.labels.<format>)"""

    format: str = "tsv"
    """Output format: tsv, jsonl, srt, vtt"""

    interval: float = 1.0
    """Interval between samples in seconds"""

    frame_step: Optional[int] = None
    """Sample every N frames (alternative to interval)"""

    include_keyframes: bool = True
    """Include keyframe markers in output"""

    endpoint: str = "bss://localhost:3112"
    """BSS endpoint for upload (if --upload specified)"""

    upload: bool = False
    """Upload track to BSS after generation"""

    track_name: str = "auto-frames"
    """Name for the text track"""

    source: str = "dreamlake-cli"
    """Source identifier for the track"""

    dry_run: bool = False
    """Show what would be done without writing files"""

    verbose: bool = False
    """Show verbose output"""

    @classmethod
    def print_help(cls):
        """Print help message for label command."""
        help_text = """
dreamlake label - Generate text track labels from video

Usage:
    dreamlake label <video> [options]

Arguments:
    video              Path to video file

Options:
    --output FILE      Output file path (default: <video>.labels.<format>)
    --format FORMAT    Output format: tsv, jsonl, srt, vtt (default: tsv)
    --interval SECS    Sample interval in seconds (default: 1.0)
    --frame-step N     Sample every N frames (alternative to interval)
    --include-keyframes  Include keyframe markers (default: true)
    --upload           Upload track to BSS after generation
    --endpoint URL     BSS endpoint (default: bss://localhost:3112)
    --track-name NAME  Track name (default: auto-frames)
    --source SOURCE    Source identifier (default: dreamlake-cli)
    --dry-run          Show what would be done
    --verbose          Show verbose output
    -h, --help         Show this help message

Output Format (TSV):
    start_ms    end_ms    line_num    text
    0           1000      0           frame 0 @ 0.000s
    1000        2000      1           frame 30 @ 1.000s
    ...

Reference Format:
    track:<track_id>:line:<line_num>

Examples:
    dreamlake label video.mp4
    dreamlake label video.mp4 --interval 0.5 --format jsonl
    dreamlake label video.mp4 --upload --endpoint bss://api.example.com
"""
        print(help_text)

    @classmethod
    def _parse_args(cls, args: list):
        """Parse command line arguments."""
        i = 0
        while i < len(args):
            arg = args[i]
            if arg.startswith("--"):
                if arg == "--output" and i + 1 < len(args):
                    cls.output = args[i + 1]
                    i += 2
                elif arg == "--format" and i + 1 < len(args):
                    cls.format = args[i + 1]
                    i += 2
                elif arg == "--interval" and i + 1 < len(args):
                    cls.interval = float(args[i + 1])
                    i += 2
                elif arg == "--frame-step" and i + 1 < len(args):
                    cls.frame_step = int(args[i + 1])
                    i += 2
                elif arg == "--endpoint" and i + 1 < len(args):
                    cls.endpoint = args[i + 1]
                    i += 2
                elif arg == "--track-name" and i + 1 < len(args):
                    cls.track_name = args[i + 1]
                    i += 2
                elif arg == "--source" and i + 1 < len(args):
                    cls.source = args[i + 1]
                    i += 2
                elif arg == "--include-keyframes":
                    cls.include_keyframes = True
                    i += 1
                elif arg == "--no-keyframes":
                    cls.include_keyframes = False
                    i += 1
                elif arg == "--upload":
                    cls.upload = True
                    i += 1
                elif arg == "--dry-run":
                    cls.dry_run = True
                    i += 1
                elif arg == "--verbose":
                    cls.verbose = True
                    i += 1
                else:
                    i += 1
            elif not arg.startswith("-") and cls.video == ".":
                cls.video = arg
                i += 1
            else:
                i += 1

    @classmethod
    def _get_video_info(cls, video_path: Path) -> dict:
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

    @classmethod
    def _extract_video_stream(cls, info: dict) -> dict:
        """Extract video stream info."""
        for stream in info.get("streams", []):
            if stream.get("codec_type") == "video":
                return stream
        return {}

    @classmethod
    def _generate_segments(cls, video_path: Path) -> list:
        """Generate text segments for the video."""
        info = cls._get_video_info(video_path)
        video_stream = cls._extract_video_stream(info)

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

        if cls.verbose:
            print(f"Video: {video_path.name}")
            print(f"  Duration: {duration_ms}ms ({duration_ms/1000:.2f}s)")
            print(f"  FPS: {fps:.2f}")
            print(f"  Resolution: {width}x{height}")

        segments = []
        line_num = 0

        if cls.frame_step:
            # Sample every N frames
            frame_interval_ms = int((cls.frame_step / fps) * 1000)
        else:
            # Sample by time interval
            frame_interval_ms = int(cls.interval * 1000)

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

    @classmethod
    def _format_tsv(cls, segments: list) -> str:
        """Format segments as TSV."""
        lines = ["start_ms\tend_ms\tline_num\ttext"]
        for seg in segments:
            lines.append(f"{seg['start_ms']}\t{seg['end_ms']}\t{seg['line_num']}\t{seg['text']}")
        return "\n".join(lines)

    @classmethod
    def _format_jsonl(cls, segments: list) -> str:
        """Format segments as JSONL."""
        lines = []
        for seg in segments:
            lines.append(json.dumps(seg))
        return "\n".join(lines)

    @classmethod
    def _format_srt(cls, segments: list) -> str:
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

    @classmethod
    def _format_vtt(cls, segments: list) -> str:
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

    @classmethod
    def run(cls) -> int:
        """Execute the label command."""
        video_path = Path(cls.video)

        if not video_path.exists():
            print(f"Error: Video not found: {video_path}")
            return 1

        if video_path.is_dir():
            print(f"Error: Directory not supported yet, specify a video file")
            return 1

        # Determine output path
        if cls.output:
            output_path = Path(cls.output)
        else:
            ext = {"tsv": "tsv", "jsonl": "jsonl", "srt": "srt", "vtt": "vtt"}.get(cls.format, "tsv")
            output_path = video_path.with_suffix(f".labels.{ext}")

        if cls.verbose:
            print(f"Generating labels for: {video_path}")
            print(f"Output: {output_path}")
            print(f"Format: {cls.format}")
            print(f"Interval: {cls.interval}s")

        # Generate segments
        segments = cls._generate_segments(video_path)

        if cls.verbose:
            print(f"Generated {len(segments)} segments")

        # Format output
        formatters = {
            "tsv": cls._format_tsv,
            "jsonl": cls._format_jsonl,
            "srt": cls._format_srt,
            "vtt": cls._format_vtt,
        }
        formatter = formatters.get(cls.format, cls._format_tsv)
        output_content = formatter(segments)

        if cls.dry_run:
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

        # Upload if requested
        if cls.upload:
            print(f"TODO: Upload to {cls.endpoint}")
            print(f"  Track name: {cls.track_name}")
            print(f"  Source: {cls.source}")

        return 0
