"""
Download command.

Usage:
    dreamlake download --episode [namespace@]project[:episode] --from <path> [-o <output>]
"""

import sys
from pathlib import Path

from params_proto import proto

from dreamlake.cli._args import args_to_dict
from dreamlake.cli._config import ServerConfig
from dreamlake.cli._target import parse_target, format_target

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
CYAN = "\033[36m"


@proto.prefix
class DownloadConfig:
    sess: str | None = None       # [namespace@]project[:episode]
    from_path: str | None = None  # source path within episode (CLI flag: --from)


def print_help():
    print(f"""
{BOLD}dreamlake download{RESET} - Download a file from DreamLake

{BOLD}Usage:{RESET}
    dreamlake download --episode [namespace@]project[:episode] --from <path>

{BOLD}Options:{RESET}
    --episode    Episode scope: [namespace@]project[:episode]
    --from    Source path (including filename); output name derived from it

{BOLD}Examples:{RESET}
    dreamlake download --episode alice@robotics:2026/q1/run-042 --from /microphone/front/mic.wav
    dreamlake download --episode robotics:experiments/run-042 --from /camera/front/recording.mp4
    dreamlake download --episode alice@robotics --from /audio/ambient/ambient.wav
""".strip())


def cmd_download() -> int:
    if not DownloadConfig.sess:
        print(f"{RED}error:{RESET} --episode is required", file=sys.stderr)
        return 1

    if not DownloadConfig.from_path:
        print(f"{RED}error:{RESET} --from is required", file=sys.stderr)
        return 1

    try:
        t = parse_target(DownloadConfig.sess)
    except ValueError as e:
        print(f"{RED}error:{RESET} {e}", file=sys.stderr)
        return 1

    path = DownloadConfig.from_path.lstrip("/")

    if not t.namespace:
        t.namespace = ServerConfig.resolve_namespace()
        if not t.namespace:
            print(
                f"{RED}error:{RESET} namespace not specified and no authenticated user found. run 'dreamlake login'",
                file=sys.stderr,
            )
            return 1

    token = ServerConfig.resolve_token()
    if not token:
        print(f"{RED}error:{RESET} not authenticated. run 'dreamlake login' first", file=sys.stderr)
        return 1

    output = Path(path).name

    print(f"Downloading {CYAN}/{path}{RESET}")
    print(f"  {DIM}episode:{RESET} {format_target(t)}")
    print(f"  {DIM}output:{RESET}  {output}")

    try:
        return _download_asset(t, path, output, token)
    except Exception as e:
        print(f"{RED}error:{RESET} {e}", file=sys.stderr)
        return 1


EXTENSION_TO_CATEGORY = {
    ".wav": "audio", ".mp3": "audio", ".aac": "audio", ".opus": "audio",
    ".mp4": "video", ".mkv": "video",
    ".npy": "track", ".msgpack": "track", ".csv": "track",
    ".srt": "text-track", ".vtt": "text-track",
    ".jsonl": "label-track",
}


def _download_asset(t, path: str, output: str, token: str) -> int:
    """Download asset through dreamlake-server."""
    import httpx

    # Detect category from output filename extension; default to video if no extension
    ext = Path(output).suffix.lower()
    category = EXTENSION_TO_CATEGORY.get(ext, "video") if ext else "video"

    if category != "video":
        print(f"\n(download for '{category}' not yet implemented)")
        return 0

    remote = ServerConfig.remote
    headers = {"Authorization": f"Bearer {token}"}

    params: dict = {"namespace": t.namespace, "project": t.project, "path": f"/{path}"}
    if t.episode:
        params["episode"] = t.episode

    # Resolve presigned download URL
    with httpx.Client(timeout=30, headers=headers) as client:
        r = client.get(f"{remote}/assets/video/download", params=params)
        if r.status_code == 404:
            print(f"{RED}error:{RESET} asset not found at /{path}", file=sys.stderr)
            return 1
        r.raise_for_status()
        presigned_url = r.json()["url"]

    # Stream-download from S3 with progress
    with httpx.Client(timeout=300) as s3:
        with s3.stream("GET", presigned_url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(output, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded / total * 100
                        print(
                            f"\r  {DIM}{downloaded / 1024 / 1024:.1f}/{total / 1024 / 1024:.1f} MB ({pct:.0f}%){RESET}",
                            end="",
                            flush=True,
                        )
    print()
    print(f"{GREEN}✓ Downloaded:{RESET} {output}  ({downloaded / 1024 / 1024:.1f} MB)")
    return 0


def main(args: list) -> int:
    if not args or args[0] in ("-h", "--help", "help"):
        print_help()
        return 0 if args else 1

    # Remap --from (Python reserved word) to --from-path for params-proto
    remapped = ["--from-path" if a == "--from" else a for a in args]

    DownloadConfig._update(args_to_dict(remapped))
    return cmd_download()
