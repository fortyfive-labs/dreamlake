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
            {CYAN}login{RESET}       Authenticate with DreamLake (device auth flow)
            {CYAN}logout{RESET}      Remove stored credentials
            {CYAN}profile{RESET}     Show current user
            {CYAN}upload{RESET}      Upload a file (type auto-detected)
            {CYAN}download{RESET}    Download a file
            {CYAN}list{RESET}        List assets or dreamlets
            {CYAN}create{RESET}      Create a dreamlet
            {CYAN}delete{RESET}      Delete a dreamlet
            {CYAN}update{RESET}      Update a dreamlet (add/remove episodes)
            {CYAN}vectorize{RESET}   Run CLIP + LLaVA on video chunks for semantic search
            {CYAN}video{RESET}       Video commands (upload/download/list via BSS)

        {BOLD}Target syntax:{RESET}
            --episode space[@namespace][:episode]
            --space   space[@namespace]

        {BOLD}Examples:{RESET}
            {DIM}# Upload (type auto-detected from extension){RESET}
            dreamlake upload ./mic.wav --episode robotics@alice:2026/q1/run-042 --to /microphone/front

            {DIM}# Download{RESET}
            dreamlake download --episode robotics@alice:2026/q1/run-042 --from /microphone/front -o ./mic.wav

            {DIM}# List assets{RESET}
            dreamlake list --episode robotics@alice:2026/q1/run-042
            dreamlake list --episode robotics@alice:2026/q1/run-042 --type audio

            {DIM}# List dreamlets{RESET}
            dreamlake list dreamlet --space robotics@alice

            {DIM}# Video commands{RESET}
            dreamlake video upload ./video.mp4 --user alice --project robotics
            dreamlake video download <id> --output ./video.mp4
            dreamlake video list --user alice --project robotics

        {BOLD}Environment Variables:{RESET}
            {YELLOW}DREAMLAKE_REMOTE{RESET}      Default server URL
            {YELLOW}DREAMLAKE_API_KEY{RESET}     Default API token
            {YELLOW}DREAMLAKE_BSS_URL{RESET}     Default BSS server URL

        Use '{CYAN}dreamlake <command> --help{RESET}' for more information.
    """).strip())


def main():
    """Main CLI entry point."""
    # Strip --debug early (before params-proto sees argv) and apply globally
    if "--debug" in sys.argv:
        sys.argv = [a for a in sys.argv if a != "--debug"]
        from dreamlake.cli._config import ServerConfig
        ServerConfig.debug = True
        ServerConfig.remote = "http://localhost:10334"
        ServerConfig.bss_url = "http://localhost:10234"

    if len(sys.argv) < 2:
        print_help()
        return 1

    command = sys.argv[1]

    if command in ("-h", "--help", "help"):
        print_help()
        return 0

    # Auth commands
    if command == "login":
        from dreamlake.cli_commands.login import cmd_login
        import argparse as ap
        p = ap.ArgumentParser(prog="dreamlake login")
        p.add_argument("--url", type=str)
        p.add_argument("--no-browser", action="store_true")
        return cmd_login(p.parse_args(sys.argv[2:]))

    elif command == "logout":
        from dreamlake.cli_commands.logout import cmd_logout
        return cmd_logout(None)

    elif command == "profile":
        from dreamlake.cli_commands.profile import cmd_profile
        import argparse as ap
        p = ap.ArgumentParser(prog="dreamlake profile")
        p.add_argument("--url", type=str)
        return cmd_profile(p.parse_args(sys.argv[2:]))

    # Video subcommands
    elif command == "video":
        from .commands import video
        return video.main(sys.argv[2:])

    # Asset commands
    elif command == "upload":
        from .commands import upload
        return upload.main(sys.argv[2:])

    elif command == "download":
        from .commands import download
        return download.main(sys.argv[2:])

    elif command == "list":
        from .commands import list as list_mod
        return list_mod.main(sys.argv[2:])

    elif command == "create":
        from .commands import create
        return create.main(sys.argv[2:])

    elif command == "delete":
        from .commands import delete
        return delete.main(sys.argv[2:])

    elif command == "update":
        from .commands import update
        return update.main(sys.argv[2:])

    elif command == "vectorize":
        from .commands import vectorize
        return vectorize.main(sys.argv[2:])

    else:
        print(f"{RED}Unknown command:{RESET} {command}", file=sys.stderr)
        print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
