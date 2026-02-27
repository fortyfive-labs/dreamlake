"""
Video commands for BSS - upload, download, list.

Usage:
    dreamlake video upload ./video.mp4 --user alice --project robotics
    dreamlake video download abc123 --output ./video.mp4
    dreamlake video list --user alice
"""

import os
import sys
import hashlib
from pathlib import Path
from textwrap import dedent
from typing import List

from params_proto import proto

# ANSI color codes
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"


def parse_args_to_dict(args: list) -> dict:
    """Parse CLI-style args list into a dictionary.

    Converts ['--foo', 'bar', '--baz', '123'] into {'foo': 'bar', 'baz': '123'}
    """
    result = {}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("--"):
            key = arg[2:].replace("-", "_")
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                value = args[i + 1]
                # Handle boolean-like values
                if value.lower() in ("true", "yes", "1"):
                    result[key] = True
                elif value.lower() in ("false", "no", "0"):
                    result[key] = False
                else:
                    # Try to convert to int
                    try:
                        result[key] = int(value)
                    except ValueError:
                        result[key] = value
                i += 2
            else:
                # Flag without value (boolean True)
                result[key] = True
                i += 1
        else:
            i += 1
    return result


# ============================================================
# Configuration Classes (params-proto)
# ============================================================

@proto(prefix="upload")
class VideoUploadConfig:
    """Configuration for video upload command."""

    file: str = None
    name: str = None
    user: str = None
    project: str = None
    tags: List[str] = []
    description: str = None
    bss_url: str = None
    token: str = None


@proto(prefix="download")
class VideoDownloadConfig:
    """Configuration for video download command."""

    video_id: str = None
    output: str = None
    bss_url: str = None
    token: str = None


@proto(prefix="list")
class VideoListConfig:
    """Configuration for video list command."""

    user: str = None
    project: str = None
    tags: List[str] = []
    limit: int = 50
    offset: int = 0
    json_output: bool = False
    bss_url: str = None
    token: str = None


# ============================================================
# Command Implementations
# ============================================================

def cmd_upload(config) -> int:
    """Upload a video to BSS."""
    import httpx

    if not config.file:
        print(f"{RED}Error:{RESET} video file path is required", file=sys.stderr)
        print(f"Usage: dreamlake video upload <file> [options]", file=sys.stderr)
        return 1

    video_path = Path(config.file)
    if not video_path.exists():
        print(f"{RED}Error:{RESET} Video file not found: {config.file}", file=sys.stderr)
        return 1

    bss_url = config.bss_url or os.getenv("DREAMLAKE_BSS_URL", "http://localhost:4000")
    token = config.token or os.getenv("DREAMLAKE_BSS_TOKEN")
    user = config.user or os.getenv("DREAMLAKE_USER", "default")
    project = config.project or os.getenv("DREAMLAKE_PROJECT", "default")

    if not token:
        print(f"{RED}Error:{RESET} --token or DREAMLAKE_BSS_TOKEN is required", file=sys.stderr)
        return 1

    # Read video content and compute hash
    print(f"Reading video file: {CYAN}{video_path}{RESET}")
    content = video_path.read_bytes()
    raw_hash = hashlib.sha256(content).hexdigest()[:16]
    size_mb = len(content) / (1024 * 1024)
    print(f"  {DIM}Size:{RESET} {size_mb:.2f} MB")
    print(f"  {DIM}Hash:{RESET} {raw_hash}")

    headers = {"Authorization": f"Bearer {token}"}

    try:
        with httpx.Client(timeout=30.0, headers=headers) as client:
            print(f"Requesting presigned URL from {bss_url}...")
            presign_resp = client.post(
                f"{bss_url}/video/upload/presigned",
                json={
                    "user": user,
                    "project": project,
                    "hash": raw_hash,
                    "contentType": "video/mp4",
                }
            )
            presign_resp.raise_for_status()
            presign_data = presign_resp.json()

        print(f"Uploading to S3...")
        with httpx.Client(timeout=300.0) as client:
            upload_resp = client.put(
                presign_data["url"],
                content=content,
                headers={"Content-Type": "video/mp4"},
            )
            upload_resp.raise_for_status()

        video_name = config.name or video_path.stem
        tags = config.tags if config.tags else []

        print(f"Creating video entry...")
        with httpx.Client(timeout=30.0, headers=headers) as client:
            video_resp = client.post(
                f"{bss_url}/video",
                json={
                    "name": video_name,
                    "user": user,
                    "project": project,
                    "rawHash": raw_hash,
                    "duration": 0,
                    "tags": tags,
                    "metadata": {
                        "source": "dreamlake-cli",
                        "originalFile": video_path.name,
                        "description": config.description,
                    }
                }
            )
            video_resp.raise_for_status()
            video_data = video_resp.json()

        video_id = video_data.get("id", "N/A")
        print(f"\n{GREEN}Uploaded:{RESET} {BOLD}{video_name}{RESET}")
        print(f"  {DIM}ID:{RESET}      {video_id}")
        print(f"  {DIM}User:{RESET}    {user}")
        print(f"  {DIM}Project:{RESET} {project}")
        print(f"  {DIM}Hash:{RESET}    {raw_hash}")
        return 0

    except httpx.HTTPError as e:
        print(f"{RED}Error:{RESET} HTTP request failed: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"{RED}Error:{RESET} {e}", file=sys.stderr)
        return 1


def cmd_download(config) -> int:
    """Download a video from BSS."""
    import httpx

    if not config.video_id:
        print(f"{RED}Error:{RESET} video ID is required", file=sys.stderr)
        print(f"Usage: dreamlake video download <video_id> [options]", file=sys.stderr)
        return 1

    video_id = config.video_id
    bss_url = config.bss_url or os.getenv("DREAMLAKE_BSS_URL", "http://localhost:4000")
    token = config.token or os.getenv("DREAMLAKE_BSS_TOKEN")

    headers = {"Authorization": f"Bearer {token}"} if token else {}

    print(f"Fetching video metadata from {bss_url}...")

    try:
        with httpx.Client(timeout=30.0, headers=headers) as client:
            meta_resp = client.get(f"{bss_url}/video/{video_id}")
            if meta_resp.status_code == 404:
                print(f"{RED}Error:{RESET} Video not found: {video_id}", file=sys.stderr)
                return 1
            meta_resp.raise_for_status()
            meta = meta_resp.json()

        video_name = meta.get("name", video_id)
        user = meta.get("user", "unknown")
        project = meta.get("project", "unknown")
        raw_hash = meta.get("rawHash")

        if not raw_hash:
            print(f"{RED}Error:{RESET} Video has no raw file hash", file=sys.stderr)
            return 1

        output_path = Path(config.output) if config.output else Path(f"{video_name}.mp4")

        print(f"\n{BOLD}Video:{RESET} {video_name}")
        print(f"  {DIM}User:{RESET}    {user}")
        print(f"  {DIM}Project:{RESET} {project}")
        print(f"  {DIM}Hash:{RESET}    {raw_hash[:16]}...")
        print(f"  {DIM}Output:{RESET}  {output_path}")

        print(f"\nDownloading...")
        with httpx.Client(timeout=300.0, headers=headers) as client:
            raw_url = f"{bss_url}/video/{video_id}/raw"
            download_resp = client.get(raw_url, follow_redirects=True)

            if download_resp.status_code != 200:
                print(f"{RED}Error:{RESET} Failed to download (HTTP {download_resp.status_code})", file=sys.stderr)
                return 1

            output_path.write_bytes(download_resp.content)
            size_mb = len(download_resp.content) / (1024 * 1024)
            print(f"{GREEN}Downloaded:{RESET} {output_path} ({size_mb:.2f} MB)")

        return 0

    except httpx.HTTPError as e:
        print(f"{RED}Error:{RESET} HTTP request failed: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"{RED}Error:{RESET} {e}", file=sys.stderr)
        return 1


def cmd_list(config) -> int:
    """List videos from BSS."""
    import httpx

    bss_url = config.bss_url or os.getenv("DREAMLAKE_BSS_URL", "http://localhost:4000")
    token = config.token or os.getenv("DREAMLAKE_BSS_TOKEN")

    headers = {"Authorization": f"Bearer {token}"} if token else {}

    try:
        params = {"limit": config.limit, "offset": config.offset}
        if config.user:
            params["user"] = config.user
        if config.project:
            params["project"] = config.project
        if config.tags:
            params["tags"] = ",".join(config.tags)

        with httpx.Client(timeout=30.0, headers=headers) as client:
            resp = client.get(f"{bss_url}/video", params=params)
            resp.raise_for_status()
            data = resp.json()

        videos = data.get("videos", data) if isinstance(data, dict) else data

        if config.json_output:
            import json
            print(json.dumps(videos, indent=2, default=str))
        else:
            if not videos:
                print(f"{DIM}No videos found.{RESET}")
            else:
                print(f"Found {BOLD}{len(videos)}{RESET} video(s):\n")
                for v in videos:
                    vid = v.get("id", "N/A")[:8]
                    name = v.get("name", "N/A")
                    user = v.get("user", "N/A")
                    project = v.get("project", "N/A")
                    print(f"  {CYAN}{vid}...{RESET}  {DIM}{user}/{project}{RESET}  {name}")

        return 0

    except httpx.HTTPError as e:
        print(f"{RED}Error:{RESET} HTTP request failed: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"{RED}Error:{RESET} {e}", file=sys.stderr)
        return 1


# ============================================================
# Help and Main Entry
# ============================================================

def print_help():
    """Print video subcommand help."""
    print(dedent(f"""
        {BOLD}DreamLake Video Commands{RESET} - BSS video management

        {BOLD}Usage:{RESET}
            dreamlake video <command> [options]

        {BOLD}Commands:{RESET}
            {CYAN}upload{RESET}      Upload a video to BSS
            {CYAN}download{RESET}    Download a video from BSS
            {CYAN}list{RESET}        List videos in BSS

        {BOLD}Environment Variables:{RESET}
            DREAMLAKE_BSS_URL       BSS server URL (default: http://localhost:4000)
            DREAMLAKE_BSS_TOKEN     JWT authentication token
            DREAMLAKE_USER          Default user name
            DREAMLAKE_PROJECT       Default project name

        {BOLD}Upload:{RESET}
            dreamlake video upload <file> [options]

            {DIM}Options:{RESET}
                --name          Video name (default: filename)
                --user          User/owner name
                --project       Project name
                --tags          Comma-separated tags
                --description   Video description
                --bss-url       BSS server URL
                --token         JWT authentication token

            {DIM}Examples:{RESET}
                dreamlake video upload ./demo.mp4 --user alice --project robotics
                dreamlake video upload ./test.mp4 --name "Test Video" --tags demo,test

        {BOLD}Download:{RESET}
            dreamlake video download <video_id> [options]

            {DIM}Options:{RESET}
                --output, -o    Output file path (default: <name>.mp4)
                --bss-url       BSS server URL
                --token         JWT authentication token

            {DIM}Examples:{RESET}
                dreamlake video download abc123 --output ./downloaded.mp4
                dreamlake video download abc123 --token $DREAMLAKE_BSS_TOKEN

        {BOLD}List:{RESET}
            dreamlake video list [options]

            {DIM}Options:{RESET}
                --user          Filter by user
                --project       Filter by project
                --tags          Filter by tags (comma-separated)
                --limit         Max results (default: 50)
                --offset        Offset for pagination
                --json-output   Output as JSON
                --bss-url       BSS server URL
                --token         JWT authentication token

            {DIM}Examples:{RESET}
                dreamlake video list --user alice --project robotics
                dreamlake video list --json-output --token $DREAMLAKE_BSS_TOKEN
    """).strip())


def main(args: list) -> int:
    """Main entry point for video commands."""
    if not args:
        print_help()
        return 1

    subcommand = args[0]

    if subcommand in ("-h", "--help", "help"):
        print_help()
        return 0

    if subcommand == "upload":
        # Handle positional file argument
        sub_args = args[1:]
        positional = None
        if sub_args and not sub_args[0].startswith("--"):
            positional = sub_args[0]
            sub_args = sub_args[1:]
        kwargs = parse_args_to_dict(sub_args)
        if positional:
            kwargs["file"] = positional
        VideoUploadConfig._update(kwargs)
        return cmd_upload(VideoUploadConfig)

    elif subcommand == "download":
        # Handle positional video_id argument
        sub_args = args[1:]
        positional = None
        if sub_args and not sub_args[0].startswith("--"):
            positional = sub_args[0]
            sub_args = sub_args[1:]
        kwargs = parse_args_to_dict(sub_args)
        if positional:
            kwargs["video_id"] = positional
        VideoDownloadConfig._update(kwargs)
        return cmd_download(VideoDownloadConfig)

    elif subcommand == "list":
        kwargs = parse_args_to_dict(args[1:])
        VideoListConfig._update(kwargs)
        return cmd_list(VideoListConfig)

    else:
        print(f"{RED}Unknown video subcommand:{RESET} {subcommand}", file=sys.stderr)
        print_help()
        return 1
