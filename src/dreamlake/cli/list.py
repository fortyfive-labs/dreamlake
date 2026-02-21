"""
List command for dreamlake CLI.

List uploaded videos on BSS (Big Streaming Server).
"""

from typing import List, Dict, Any, Optional

from params_proto import proto


@proto
class ListCommand:
    """
    List uploaded videos on BSS (Big Streaming Server).

    Usage:
        dreamlake list [--endpoint URL] [--format FORMAT]

    Examples:
        dreamlake list
        dreamlake list --endpoint bss://localhost:3112
        dreamlake list --format json
    """

    endpoint: str = "bss://localhost:3112"
    """BSS endpoint URL (Big Streaming Server)"""

    format: str = "table"
    """Output format: table, json, or csv"""

    limit: int = 100
    """Maximum number of videos to list"""

    offset: int = 0
    """Offset for pagination"""

    sort_by: str = "created_at"
    """Sort by: created_at, name, size"""

    descending: bool = True
    """Sort in descending order"""

    verbose: bool = False
    """Show verbose output including metadata"""

    @classmethod
    def print_help(cls):
        """Print help message for list command."""
        help_text = """
dreamlake list - List uploaded videos on BSS

Usage:
    dreamlake list [options]

Options:
    --endpoint URL       BSS endpoint URL (default: bss://localhost:3112)
    --format FORMAT      Output format: table, json, csv (default: table)
    --limit N            Maximum number of videos to list (default: 100)
    --offset N           Offset for pagination (default: 0)
    --sort-by FIELD      Sort by: created_at, name, size (default: created_at)
    --descending         Sort in descending order (default: true)
    --verbose            Show verbose output including metadata
    -h, --help           Show this help message

Examples:
    dreamlake list
    dreamlake list --endpoint bss://192.168.1.100:3112
    dreamlake list --format json
    dreamlake list --limit 50 --offset 0
"""
        print(help_text)

    @classmethod
    def _parse_args(cls, args: list):
        """Parse command line arguments and update class attributes."""
        i = 0
        while i < len(args):
            arg = args[i]
            if arg == "--endpoint" and i + 1 < len(args):
                cls.endpoint = args[i + 1]
                i += 2
            elif arg == "--format" and i + 1 < len(args):
                cls.format = args[i + 1]
                i += 2
            elif arg == "--limit" and i + 1 < len(args):
                cls.limit = int(args[i + 1])
                i += 2
            elif arg == "--offset" and i + 1 < len(args):
                cls.offset = int(args[i + 1])
                i += 2
            elif arg == "--sort-by" and i + 1 < len(args):
                cls.sort_by = args[i + 1]
                i += 2
            elif arg == "--descending":
                cls.descending = True
                i += 1
            elif arg == "--ascending":
                cls.descending = False
                i += 1
            elif arg == "--verbose":
                cls.verbose = True
                i += 1
            else:
                i += 1

    @classmethod
    def _parse_endpoint(cls) -> tuple:
        """Parse BSS endpoint URL into (host, port)."""
        endpoint = cls.endpoint

        # Handle bss:// protocol
        if endpoint.startswith("bss://"):
            endpoint = endpoint[6:]
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
            port = 3112

        return host, port

    @classmethod
    def _fetch_videos(cls, host: str, port: int) -> List[Dict[str, Any]]:
        """
        Fetch list of videos from BSS.

        This is a stub implementation - actual fetch logic would go here.
        """
        # TODO: Implement actual BSS API call
        # This would typically:
        # 1. Connect to BSS at host:port
        # 2. Send list request with pagination params
        # 3. Parse and return response

        # Return stub data for now
        return [
            {
                "id": "video_001",
                "name": "sample_video_1.mp4",
                "size": 104857600,  # 100 MB
                "duration": 120.5,
                "created_at": "2024-01-15T10:30:00Z",
                "status": "ready",
            },
            {
                "id": "video_002",
                "name": "sample_video_2.mov",
                "size": 52428800,  # 50 MB
                "duration": 60.0,
                "created_at": "2024-01-14T15:45:00Z",
                "status": "ready",
            },
            {
                "id": "video_003",
                "name": "processing_video.mp4",
                "size": 209715200,  # 200 MB
                "duration": None,
                "created_at": "2024-01-16T08:00:00Z",
                "status": "processing",
            },
        ]

    @classmethod
    def _format_size(cls, size_bytes: int) -> str:
        """Format size in human-readable format."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

    @classmethod
    def _format_duration(cls, duration: Optional[float]) -> str:
        """Format duration in human-readable format."""
        if duration is None:
            return "-"
        minutes = int(duration // 60)
        seconds = int(duration % 60)
        return f"{minutes}:{seconds:02d}"

    @classmethod
    def _print_table(cls, videos: List[Dict[str, Any]]):
        """Print videos in table format."""
        if not videos:
            print("No videos found.")
            return

        # Print header
        if cls.verbose:
            print(f"{'ID':<12} {'Name':<30} {'Size':>10} {'Duration':>10} {'Status':<12} {'Created At'}")
            print("-" * 90)
        else:
            print(f"{'Name':<40} {'Size':>10} {'Duration':>10} {'Status':<12}")
            print("-" * 75)

        # Print rows
        for video in videos:
            size_str = cls._format_size(video["size"])
            duration_str = cls._format_duration(video.get("duration"))

            if cls.verbose:
                print(
                    f"{video['id']:<12} "
                    f"{video['name']:<30} "
                    f"{size_str:>10} "
                    f"{duration_str:>10} "
                    f"{video['status']:<12} "
                    f"{video['created_at']}"
                )
            else:
                print(
                    f"{video['name']:<40} "
                    f"{size_str:>10} "
                    f"{duration_str:>10} "
                    f"{video['status']:<12}"
                )

        print()
        print(f"Total: {len(videos)} video(s)")

    @classmethod
    def _print_json(cls, videos: List[Dict[str, Any]]):
        """Print videos in JSON format."""
        import json
        print(json.dumps(videos, indent=2))

    @classmethod
    def _print_csv(cls, videos: List[Dict[str, Any]]):
        """Print videos in CSV format."""
        if not videos:
            return

        # Print header
        headers = ["id", "name", "size", "duration", "status", "created_at"]
        print(",".join(headers))

        # Print rows
        for video in videos:
            row = [
                video.get("id", ""),
                video.get("name", ""),
                str(video.get("size", "")),
                str(video.get("duration", "")),
                video.get("status", ""),
                video.get("created_at", ""),
            ]
            print(",".join(row))

    @classmethod
    def run(cls) -> int:
        """Execute the list command."""
        # Parse endpoint
        try:
            host, port = cls._parse_endpoint()
        except ValueError as e:
            print(f"Error: Invalid endpoint format: {cls.endpoint}")
            return 1

        # Validate format
        if cls.format not in ("table", "json", "csv"):
            print(f"Error: Invalid format: {cls.format}. Use: table, json, or csv")
            return 1

        if cls.format == "table":
            print(f"Listing videos from bss://{host}:{port}")
            print()

        # Fetch videos
        try:
            videos = cls._fetch_videos(host, port)
        except Exception as e:
            print(f"Error: Failed to fetch videos: {e}")
            return 1

        # Print in requested format
        if cls.format == "table":
            cls._print_table(videos)
        elif cls.format == "json":
            cls._print_json(videos)
        elif cls.format == "csv":
            cls._print_csv(videos)

        return 0
