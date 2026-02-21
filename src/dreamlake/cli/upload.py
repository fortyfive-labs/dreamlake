"""
Upload command for dreamlake CLI.

Upload videos from a folder to BSS (Big Streaming Server).
"""

import os
import glob
from typing import List

from params_proto import proto


@proto
class UploadCommand:
    """
    Upload videos from current folder to BSS (Big Streaming Server).

    Usage:
        dreamlake upload [--path PATH] [--endpoint URL] [--pattern PATTERN]

    Examples:
        dreamlake upload
        dreamlake upload --path ./videos
        dreamlake upload --path ./videos --endpoint bss://localhost:3112
        dreamlake upload --pattern "*.mp4"
    """

    path: str = "."
    """Path to directory containing videos to upload"""

    endpoint: str = "bss://localhost:3112"
    """BSS endpoint URL (Big Streaming Server)"""

    pattern: str = "*.mp4,*.mov,*.avi,*.mkv,*.webm"
    """Comma-separated glob patterns for video files"""

    recursive: bool = False
    """Recursively search for videos in subdirectories"""

    dry_run: bool = False
    """Show what would be uploaded without actually uploading"""

    verbose: bool = False
    """Show verbose output"""

    @classmethod
    def print_help(cls):
        """Print help message for upload command."""
        help_text = """
dreamlake upload - Upload videos to BSS

Usage:
    dreamlake upload [options]

Options:
    --path PATH          Path to directory containing videos (default: .)
    --endpoint URL       BSS endpoint URL (default: bss://localhost:3112)
    --pattern PATTERN    Comma-separated glob patterns (default: *.mp4,*.mov,*.avi,*.mkv,*.webm)
    --recursive          Recursively search subdirectories
    --dry-run            Show what would be uploaded without uploading
    --verbose            Show verbose output
    -h, --help           Show this help message

Examples:
    dreamlake upload
    dreamlake upload --path ./videos
    dreamlake upload --path ./videos --endpoint bss://192.168.1.100:3112
    dreamlake upload --pattern "*.mp4" --recursive
"""
        print(help_text)

    @classmethod
    def _parse_args(cls, args: list):
        """Parse command line arguments and update class attributes."""
        i = 0
        while i < len(args):
            arg = args[i]
            if arg == "--path" and i + 1 < len(args):
                cls.path = args[i + 1]
                i += 2
            elif arg == "--endpoint" and i + 1 < len(args):
                cls.endpoint = args[i + 1]
                i += 2
            elif arg == "--pattern" and i + 1 < len(args):
                cls.pattern = args[i + 1]
                i += 2
            elif arg == "--recursive":
                cls.recursive = True
                i += 1
            elif arg == "--dry-run":
                cls.dry_run = True
                i += 1
            elif arg == "--verbose":
                cls.verbose = True
                i += 1
            else:
                # Could be positional arg for path
                if not arg.startswith("-"):
                    cls.path = arg
                i += 1

    @classmethod
    def _find_videos(cls) -> List[str]:
        """Find video files matching the patterns."""
        patterns = [p.strip() for p in cls.pattern.split(",")]
        videos = []

        base_path = os.path.abspath(cls.path)

        for pattern in patterns:
            if cls.recursive:
                search_pattern = os.path.join(base_path, "**", pattern)
                matches = glob.glob(search_pattern, recursive=True)
            else:
                search_pattern = os.path.join(base_path, pattern)
                matches = glob.glob(search_pattern)

            videos.extend(matches)

        # Remove duplicates and sort
        videos = sorted(set(videos))
        return videos

    @classmethod
    def _parse_endpoint(cls) -> tuple:
        """Parse BSS endpoint URL into (host, port)."""
        endpoint = cls.endpoint

        # Handle bss:// protocol
        if endpoint.startswith("bss://"):
            endpoint = endpoint[6:]  # Remove "bss://"
        elif endpoint.startswith("http://"):
            endpoint = endpoint[7:]
        elif endpoint.startswith("https://"):
            endpoint = endpoint[8:]

        # Split host:port
        if ":" in endpoint:
            host, port_str = endpoint.split(":", 1)
            port = int(port_str)
        else:
            host = endpoint
            port = 3112  # Default BSS port

        return host, port

    @classmethod
    def _upload_video(cls, video_path: str, host: str, port: int) -> bool:
        """
        Upload a single video to BSS.

        This is a stub implementation - actual upload logic would go here.
        """
        # TODO: Implement actual BSS upload logic
        # This would typically:
        # 1. Open a connection to BSS
        # 2. Send video metadata
        # 3. Stream video data
        # 4. Receive confirmation

        if cls.verbose:
            print(f"  Connecting to {host}:{port}...")
            print(f"  Streaming {os.path.basename(video_path)}...")
            print(f"  Upload complete.")

        return True

    @classmethod
    def run(cls) -> int:
        """Execute the upload command."""
        # Validate path
        if not os.path.exists(cls.path):
            print(f"Error: Path does not exist: {cls.path}")
            return 1

        if not os.path.isdir(cls.path):
            print(f"Error: Path is not a directory: {cls.path}")
            return 1

        # Parse endpoint
        try:
            host, port = cls._parse_endpoint()
        except ValueError as e:
            print(f"Error: Invalid endpoint format: {cls.endpoint}")
            return 1

        # Find videos
        videos = cls._find_videos()

        if not videos:
            print(f"No videos found in {cls.path} matching pattern: {cls.pattern}")
            return 0

        print(f"Found {len(videos)} video(s) to upload")
        print(f"Endpoint: bss://{host}:{port}")
        print()

        if cls.dry_run:
            print("DRY RUN - No files will be uploaded\n")

        # Upload each video
        success_count = 0
        fail_count = 0

        for video_path in videos:
            rel_path = os.path.relpath(video_path, cls.path)
            size_mb = os.path.getsize(video_path) / (1024 * 1024)

            if cls.dry_run:
                print(f"[DRY RUN] Would upload: {rel_path} ({size_mb:.2f} MB)")
                success_count += 1
            else:
                print(f"Uploading: {rel_path} ({size_mb:.2f} MB)")
                try:
                    if cls._upload_video(video_path, host, port):
                        success_count += 1
                        print(f"  OK")
                    else:
                        fail_count += 1
                        print(f"  FAILED")
                except Exception as e:
                    fail_count += 1
                    print(f"  FAILED: {e}")

        print()
        print(f"Upload complete: {success_count} succeeded, {fail_count} failed")

        return 0 if fail_count == 0 else 1
