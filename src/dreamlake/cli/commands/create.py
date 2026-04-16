"""
Create command.

Usage:
    dreamlake create dreamlet <name> --space space[@namespace] [--description <desc>] [--tags <tags>]
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
{BOLD}dreamlake create{RESET} - Create resources

{BOLD}Usage:{RESET}
    dreamlake create dreamlet <name> --space space[@namespace] [--description <desc>] [--tags <tags>]

{BOLD}Options:{RESET}
    --space        Space target: space[@namespace]
    --description  Description text
    --tags         Comma-separated tags

{BOLD}Examples:{RESET}
    dreamlake create dreamlet "front-camera" --space robotics@alice
    dreamlake create dreamlet "training-set" --space robotics --description "Q1 training" --tags robotics,training
""".strip())


def cmd_create_dreamlet(name: str, args: dict) -> int:
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

    description = args.get("description")
    tags_str = args.get("tags", "")
    tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []

    import httpx
    remote = ServerConfig.remote
    headers = {"Authorization": f"Bearer {token}"}

    body = {"name": name}
    if description:
        body["description"] = description
    if tags:
        body["tags"] = tags

    try:
        with httpx.Client(timeout=30, headers=headers) as client:
            r = client.post(
                f"{remote}/namespaces/{s.namespace}/spaces/{s.space}/dreamlets",
                json=body,
            )
            if r.status_code == 409:
                print(f"{RED}error:{RESET} dreamlet '{name}' already exists in {format_space(s)}", file=sys.stderr)
                return 1
            if r.status_code == 404:
                print(f"{RED}error:{RESET} space '{format_space(s)}' not found", file=sys.stderr)
                return 1
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as e:
        print(f"{RED}error:{RESET} {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"{RED}error:{RESET} {e}", file=sys.stderr)
        return 1

    print(f"{GREEN}✓ Created dreamlet:{RESET} {CYAN}{name}{RESET} in {BOLD}{format_space(s)}{RESET}")
    if description:
        print(f"  {DIM}description:{RESET} {description}")
    if tags:
        print(f"  {DIM}tags:{RESET}        {', '.join(tags)}")

    return 0


def main(args: list) -> int:
    if not args or args[0] in ("-h", "--help", "help"):
        print_help()
        return 0 if args else 1

    subcommand = args[0]

    if subcommand == "dreamlet":
        # Extract positional name (first non-flag arg after "dreamlet")
        remaining = args[1:]
        name = None
        flags = []
        for i, arg in enumerate(remaining):
            if not arg.startswith("-") and name is None:
                name = arg
            else:
                flags.append(arg)

        if not name:
            print(f"{RED}error:{RESET} dreamlet name is required", file=sys.stderr)
            print_help()
            return 1

        parsed = args_to_dict(flags)
        return cmd_create_dreamlet(name, parsed)

    else:
        print(f"{RED}error:{RESET} unknown resource type '{subcommand}'. supported: dreamlet", file=sys.stderr)
        return 1
