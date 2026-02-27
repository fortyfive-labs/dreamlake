"""
DreamLake CLI - Command line interface for experiment data management.

Uses params-proto for configuration and argument parsing.
"""

import sys
from textwrap import dedent

# ANSI color codes
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"


def print_help():
    """Print CLI help."""
    print(dedent(f"""
        {BOLD}DreamLake CLI{RESET} - Experiment data management

        {BOLD}Usage:{RESET}
            dreamlake <command> [options]

        {BOLD}Commands:{RESET}
            {CYAN}upload{RESET}      Upload a file to a session
            {CYAN}download{RESET}    Download a file from a session
            {CYAN}list{RESET}        List files in a session
            {CYAN}video{RESET}       Video commands (upload/download/list from BSS)

        {BOLD}Examples:{RESET}
            {DIM}# Upload a file (local mode){RESET}
            dreamlake upload --file ./model.pt --workspace my-ws --session exp-001

            {DIM}# Download a file from session{RESET}
            dreamlake download --file-id abc123 --workspace my-ws --session exp-001

            {DIM}# Video commands{RESET}
            dreamlake video upload ./video.mp4 --user alice --project robotics
            dreamlake video download abc123 --output ./video.mp4
            dreamlake video list --user alice

        {BOLD}Environment Variables:{RESET}
            {YELLOW}DREAMLAKE_REMOTE{RESET}       Default remote API URL
            {YELLOW}DREAMLAKE_API_KEY{RESET}      Default API key
            {YELLOW}DREAMLAKE_LOCAL_PATH{RESET}   Default local storage path
            {YELLOW}DREAMLAKE_BSS_URL{RESET}      Default BSS server URL (for video commands)
            {YELLOW}DREAMLAKE_BSS_TOKEN{RESET}    Default BSS JWT token
            {YELLOW}DREAMLAKE_USER{RESET}         Default user for video uploads
            {YELLOW}DREAMLAKE_PROJECT{RESET}      Default project for video uploads

        Use '{CYAN}dreamlake <command> --help{RESET}' for more information.
    """).strip())


def main():
    """Main CLI entry point."""
    if len(sys.argv) < 2:
        print_help()
        return 1

    command = sys.argv[1]

    if command in ("-h", "--help", "help"):
        print_help()
        return 0

    # Video subcommands
    if command == "video":
        from .commands import video
        return video.main(sys.argv[2:])

    # Session file commands
    elif command == "upload":
        from .commands import upload
        return upload.main(sys.argv[2:])

    elif command == "download":
        from .commands import download
        return download.main(sys.argv[2:])

    elif command == "list":
        from .commands import list_cmd
        return list_cmd.main(sys.argv[2:])

    else:
        print(f"{RED}Unknown command:{RESET} {command}", file=sys.stderr)
        print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
