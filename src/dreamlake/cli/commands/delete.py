"""
Delete command.

Usage:
    dreamlake delete dreamlet <name> --space space[@namespace]
"""

import sys

from dreamlake.cli._args import args_to_dict
from dreamlake.cli._config import ServerConfig
from dreamlake.cli._target import parse_space, format_space

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
CYAN = "\033[36m"


def print_help():
    print(f"""
{BOLD}dreamlake delete{RESET} - Delete resources

{BOLD}Usage:{RESET}
    dreamlake delete dreamlet <name> --space space[@namespace] [--yes]

{BOLD}Options:{RESET}
    --space     Space target: space[@namespace]
    --yes       Skip confirmation prompt

{BOLD}Examples:{RESET}
    dreamlake delete dreamlet "front-camera" --space robotics@alice
    dreamlake delete dreamlet "training-set" --space robotics
""".strip())


def cmd_delete_dreamlet(name: str, args: dict) -> int:
    space_str = args.get("space")
    if not space_str:
        print(f"{RED}error:{RESET} --space is required", file=sys.stderr)
        return 1

    try:
        s = parse_space(space_str)
    except ValueError as e:
        print(f"{RED}error:{RESET} {e}", file=sys.stderr)
        return 1

    if not s.namespace:
        s.namespace = ServerConfig.resolve_namespace()
        if not s.namespace:
            print(f"{RED}error:{RESET} namespace not specified. run 'dreamlake login'", file=sys.stderr)
            return 1

    token = ServerConfig.resolve_token()
    if not token:
        print(f"{RED}error:{RESET} not authenticated. run 'dreamlake login' first", file=sys.stderr)
        return 1

    # Confirm unless --yes
    if not args.get("yes"):
        confirm = input(f"Delete dreamlet '{name}' from {format_space(s)}? [y/N] ").strip().lower()
        if confirm not in ("y", "yes"):
            print("Cancelled.")
            return 0

    import httpx
    remote = ServerConfig.remote
    headers = {"Authorization": f"Bearer {token}"}

    try:
        with httpx.Client(timeout=30, headers=headers) as client:
            r = client.delete(
                f"{remote}/namespaces/{s.namespace}/spaces/{s.space}/dreamlets/{name}",
            )
            if r.status_code == 404:
                print(f"{RED}error:{RESET} dreamlet '{name}' not found in {format_space(s)}", file=sys.stderr)
                return 1
            r.raise_for_status()
    except Exception as e:
        print(f"{RED}error:{RESET} {e}", file=sys.stderr)
        return 1

    print(f"{GREEN}✓ Deleted dreamlet:{RESET} {CYAN}{name}{RESET} from {BOLD}{format_space(s)}{RESET}")
    return 0


def main(args: list) -> int:
    if not args or args[0] in ("-h", "--help", "help"):
        print_help()
        return 0 if args else 1

    subcommand = args[0]

    if subcommand == "dreamlet":
        remaining = args[1:]
        name = None
        flags = []
        for arg in remaining:
            if not arg.startswith("-") and name is None:
                name = arg
            else:
                flags.append(arg)

        if not name:
            print(f"{RED}error:{RESET} dreamlet name is required", file=sys.stderr)
            print_help()
            return 1

        parsed = args_to_dict(flags)
        return cmd_delete_dreamlet(name, parsed)

    else:
        print(f"{RED}error:{RESET} unknown resource type '{subcommand}'. supported: dreamlet", file=sys.stderr)
        return 1
