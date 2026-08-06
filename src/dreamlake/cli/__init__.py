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
            {CYAN}video{RESET}       Video commands (upload/download/list via BSS)
            {CYAN}artifact{RESET}    Upload/list renderable artifacts (HTML/React/Markdown/SVG/code)
            {CYAN}workflow{RESET}    Push/list workflow specs (WorkflowSpec v1 JSON)
            {CYAN}source{RESET}      Managed DreamDB video sources (create/collection/push)

        {BOLD}Moved:{RESET}
            {YELLOW}upload, download, list, create, delete, update and vectorize now live in{RESET}
            {YELLOW}the standalone `dreamlake` CLI:{RESET}
                curl -fsSL https://dl.dreamlake.ai/install.sh | bash

        {BOLD}Examples:{RESET}
            {DIM}# Video commands{RESET}
            dreamlake video upload ./video.mp4 --user alice --project robotics
            dreamlake video download <id> --output ./video.mp4
            dreamlake video list --user alice --project robotics

            {DIM}# The canonical DreamDB writers (spawned by dreamlake-server){RESET}
            {DIM}# — read the payload on stdin, print one JSON line on stdout{RESET}
            python -m dreamlake.cli workflow append-local --backend <url> --name <workflow>
            python -m dreamlake.cli artifact append-local --backend <url> --id <artifactId>

        {BOLD}Environment Variables:{RESET}
            {YELLOW}DREAMLAKE_REMOTE{RESET}      Default server URL
            {YELLOW}DREAMLAKE_API_KEY{RESET}     Default API token
            {YELLOW}DREAMLAKE_BSS_URL{RESET}     Default BSS server URL

        Use '{CYAN}dreamlake <command> --help{RESET}' for more information.
    """).strip())


def main():
    """Main CLI entry point."""
    # Deprecation pointer: the CLI moved to the standalone DreamLake CLI
    # (curl -fsSL https://dl.dreamlake.ai/install.sh | bash). argv[0]-gated
    # — `python -m dreamlake.cli` and the internal append-local writers
    # stay silent.
    from dreamlake.cli._notice import migration_notice

    notice = migration_notice(
        sys.argv[0],
        sys.argv[1:],
    )
    if notice:
        print(notice, file=sys.stderr)

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

    # Asset commands (upload / download / list / create / delete / update /
    # vectorize) were removed — they live in the standalone DreamLake CLI now.
    # `_notice._KNOWN_COMMANDS` still names them on purpose, so a stale
    # `dreamlake` bin left by an old install prints the right pointer.
    elif command in ("upload", "download", "list", "create", "delete", "update", "vectorize"):
        print(
            f"{RED}removed:{RESET} `dreamlake {command}` now lives in the standalone "
            f"DreamLake CLI.\n"
            f"    curl -fsSL https://dl.dreamlake.ai/install.sh | bash\n"
            f"then run:  dreamlake {command} ...",
            file=sys.stderr,
        )
        return 1

    elif command == "artifact":
        from .commands import artifact
        return artifact.main(sys.argv[2:])

    elif command == "workflow":
        from .commands import workflow
        return workflow.main(sys.argv[2:])

    elif command == "source":
        from .commands import source
        return source.main(sys.argv[2:])

    else:
        print(f"{RED}Unknown command:{RESET} {command}", file=sys.stderr)
        print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
