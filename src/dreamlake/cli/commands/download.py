"""
Download command - Download files from a DreamLake session.

Usage:
    dreamlake download --file-id abc123 --workspace my-ws --session exp-001
"""

import sys
from textwrap import dedent

from params_proto import proto

# ANSI color codes
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
CYAN = "\033[36m"


@proto(prefix="download")
class DownloadConfig:
    """Configuration for the download command."""

    file_id: str = None
    output: str = None
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


def cmd_download(config: DownloadConfig) -> int:
    """Execute download command."""
    if not config.file_id:
        print(f"{RED}Error:{RESET} --file-id is required", file=sys.stderr)
        return 1

    if not config.workspace or not config.session:
        print(f"{RED}Error:{RESET} --workspace and --session are required", file=sys.stderr)
        return 1

    try:
        with _get_session(config) as session:
            downloaded_path = session.file(
                file_id=config.file_id,
                dest_path=config.output,
            ).download()
            print(f"{GREEN}Downloaded:{RESET} {downloaded_path}")
            return 0
    except Exception as e:
        print(f"{RED}Error:{RESET} {e}", file=sys.stderr)
        return 1


def print_help():
    """Print download command help."""
    print(dedent(f"""
        {BOLD}DreamLake Download{RESET} - Download files from a session

        {BOLD}Usage:{RESET}
            dreamlake download --file-id <id> --workspace <name> --session <name> [options]

        {BOLD}Options:{RESET}
            --file-id       ID of the file to download (required)
            --workspace     Workspace name (required)
            --session       Session/experiment name (required)
            --output        Output path (default: current directory)
            --remote        Remote API URL
            --api-key       API key for remote mode
            --local-path    Local storage path

        {BOLD}Examples:{RESET}
            {DIM}# Download to current directory{RESET}
            dreamlake download --file-id abc123 --workspace my-ws --session exp-001

            {DIM}# Download to specific path{RESET}
            dreamlake download --file-id abc123 --workspace my-ws --session exp-001 \\
                --output ./downloaded_model.pt
    """).strip())


def main(args: list) -> int:
    """Main entry point for download command."""
    if not args or args[0] in ("-h", "--help", "help"):
        print_help()
        return 0 if args else 1

    DownloadConfig._update(args)
    return cmd_download(DownloadConfig)
