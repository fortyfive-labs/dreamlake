"""
Upload command - Upload files to a DreamLake session.

Usage:
    dreamlake upload --file ./model.pt --workspace my-ws --session exp-001
"""

import sys
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
CYAN = "\033[36m"


@proto(prefix="upload")
class UploadConfig:
    """Configuration for the upload command."""

    file: str = None
    path: str = "/"
    description: str = None
    tags: List[str] = []
    workspace: str = None
    session: str = None
    remote: str = None
    api_key: str = None
    local_path: str = ".dreamlake"


def _get_session(config):
    """Create a session from config."""
    from dreamlake.session import Session

    kwargs = {
        "name": config.session,
        "workspace": config.workspace,
    }

    if config.remote:
        kwargs["remote"] = config.remote
        kwargs["api_key"] = config.api_key
    else:
        kwargs["local_path"] = config.local_path

    return Session(**kwargs)


def cmd_upload(config: UploadConfig) -> int:
    """Execute upload command."""
    if not config.file:
        print(f"{RED}Error:{RESET} --file is required", file=sys.stderr)
        return 1

    if not config.workspace or not config.session:
        print(f"{RED}Error:{RESET} --workspace and --session are required", file=sys.stderr)
        return 1

    file_path = Path(config.file)
    if not file_path.exists():
        print(f"{RED}Error:{RESET} File not found: {config.file}", file=sys.stderr)
        return 1

    try:
        with _get_session(config) as session:
            result = session.files().upload(
                str(file_path),
                path=config.path,
                description=config.description,
                tags=config.tags if config.tags else None,
            )
            print(f"{GREEN}Uploaded:{RESET} {BOLD}{result.get('filename', file_path.name)}{RESET}")
            print(f"  {DIM}ID:{RESET}       {result.get('id')}")
            print(f"  {DIM}Path:{RESET}     {result.get('path', config.path)}")
            print(f"  {DIM}Checksum:{RESET} {result.get('checksum', 'N/A')[:16]}...")
            return 0
    except Exception as e:
        print(f"{RED}Error:{RESET} {e}", file=sys.stderr)
        return 1


def print_help():
    """Print upload command help."""
    print(dedent(f"""
        {BOLD}DreamLake Upload{RESET} - Upload files to a session

        {BOLD}Usage:{RESET}
            dreamlake upload --file <path> --workspace <name> --session <name> [options]

        {BOLD}Options:{RESET}
            --file          Path to file to upload (required)
            --workspace     Workspace name (required)
            --session       Session/experiment name (required)
            --path          Logical path prefix (default: /)
            --description   Optional file description
            --tags          Comma-separated tags
            --remote        Remote API URL (uses local mode if not set)
            --api-key       API key for remote mode
            --local-path    Local storage path (default: .dreamlake)

        {BOLD}Examples:{RESET}
            {DIM}# Local mode{RESET}
            dreamlake upload --file ./model.pt --workspace my-ws --session exp-001

            {DIM}# With path and tags{RESET}
            dreamlake upload --file ./model.pt --workspace my-ws --session exp-001 \\
                --path /models --tags checkpoint,final

            {DIM}# Remote mode{RESET}
            dreamlake upload --file ./model.pt --workspace my-ws --session exp-001 \\
                --remote http://localhost:3000 --api-key $TOKEN
    """).strip())


def main(args: list) -> int:
    """Main entry point for upload command."""
    if not args or args[0] in ("-h", "--help", "help"):
        print_help()
        return 0 if args else 1

    UploadConfig._update(args)
    return cmd_upload(UploadConfig)
