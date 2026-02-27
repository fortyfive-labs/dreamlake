"""
List command - List files in a DreamLake session.

Usage:
    dreamlake list --workspace my-ws --session exp-001
"""

import sys
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


@proto(prefix="list")
class ListConfig:
    """Configuration for the list command."""

    path: str = None
    tags: List[str] = []
    json_output: bool = False
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


def cmd_list(config: ListConfig) -> int:
    """Execute list command."""
    if not config.workspace or not config.session:
        print(f"{RED}Error:{RESET} --workspace and --session are required", file=sys.stderr)
        return 1

    try:
        with _get_session(config) as session:
            files = session.files().list(
                path=config.path,
                tags=config.tags if config.tags else None,
            )

            if config.json_output:
                import json
                print(json.dumps(files, indent=2, default=str))
            else:
                if not files:
                    print(f"{DIM}No files found.{RESET}")
                else:
                    print(f"Found {BOLD}{len(files)}{RESET} file(s):\n")
                    for f in files:
                        fid = f.get('id', 'N/A')[:20]
                        path = f.get('path', '/')
                        name = f.get('filename', 'N/A')
                        print(f"  {CYAN}{fid}{RESET}  {DIM}{path:<15}{RESET}  {name}")
            return 0
    except Exception as e:
        print(f"{RED}Error:{RESET} {e}", file=sys.stderr)
        return 1


def print_help():
    """Print list command help."""
    print(dedent(f"""
        {BOLD}DreamLake List{RESET} - List files in a session

        {BOLD}Usage:{RESET}
            dreamlake list --workspace <name> --session <name> [options]

        {BOLD}Options:{RESET}
            --workspace     Workspace name (required)
            --session       Session/experiment name (required)
            --path          Filter by path prefix
            --tags          Filter by tags (comma-separated)
            --json-output   Output as JSON
            --remote        Remote API URL
            --api-key       API key for remote mode
            --local-path    Local storage path

        {BOLD}Examples:{RESET}
            {DIM}# List all files{RESET}
            dreamlake list --workspace my-ws --session exp-001

            {DIM}# Filter by path{RESET}
            dreamlake list --workspace my-ws --session exp-001 --path /models

            {DIM}# JSON output{RESET}
            dreamlake list --workspace my-ws --session exp-001 --json-output
    """).strip())


def main(args: list) -> int:
    """Main entry point for list command."""
    if not args or args[0] in ("-h", "--help", "help"):
        print_help()
        return 0 if args else 1

    ListConfig._update(args)
    return cmd_list(ListConfig)
