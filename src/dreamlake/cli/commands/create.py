"""
Create command.

Usage:
    dreamlake create collection <name> --project space[@namespace] [--episode <glob>] [--description <desc>] [--tags <tags>]
    dreamlake create dataset <name> --project space[@namespace] [--description <desc>] [--tags <tags>]
"""

import sys

from dreamlake.cli._args import args_to_dict
from dreamlake.cli._config import ServerConfig
from dreamlake.cli._target import parse_project, format_project

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
    dreamlake create collection <name> --project space[@namespace] [--episode <glob>] [--description <desc>] [--tags <tags>]
    dreamlake create dataset <name> --project space[@namespace] [--description <desc>] [--tags <tags>]

{BOLD}Options:{RESET}
    --project        Space target: space[@namespace]
    --episode      Glob pattern to match episodes by node path (collection only)
    --description  Description text
    --tags         Comma-separated tags

{BOLD}Examples:{RESET}
    dreamlake create collection "front-camera" --project robotics@alice --episode "camera/front/*"
    dreamlake create collection "april-runs" --project robotics@alice --episode "2026/04/*"
    dreamlake create dataset "training-v1" --project robotics@alice --description "Q1 training" --tags robotics,training
""".strip())


def _fetch_all_episodes(client, remote: str, namespace: str, space: str) -> list[dict]:
    """Fetch all episodes in a space (paginated)."""
    episodes = []
    page = 1
    while True:
        r = client.get(
            f"{remote}/namespaces/{namespace}/projects/{space}/episodes",
            params={"page": str(page), "pageSize": "200"},
        )
        r.raise_for_status()
        data = r.json()
        episodes.extend(data.get("episodes", []))
        if page >= data.get("totalPages", 1):
            break
        page += 1
    return episodes


def _match_episodes(episodes: list[dict], pattern: str) -> list[dict]:
    """Match episodes by glob pattern against their node paths."""
    from fnmatch import fnmatch

    # Normalize pattern: strip leading /
    pat = pattern.lstrip("/")

    matched = []
    for ep in episodes:
        node_path = ep.get("nodePath") or ""
        # Strip leading / for matching
        path = node_path.lstrip("/")
        if fnmatch(path, pat):
            matched.append(ep)
    return matched


def cmd_create_collection(name: str, args: dict) -> int:
    project_str = args.get("project")
    if not project_str:
        print(f"{RED}error:{RESET} --project is required", file=sys.stderr)
        return 1

    try:
        s = parse_project(project_str)
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
    episode_pattern = args.get("episode")

    import httpx
    remote = ServerConfig.remote
    headers = {"Authorization": f"Bearer {token}"}

    members = []

    try:
        with httpx.Client(timeout=30, headers=headers) as client:
            # Resolve episode glob → member IDs
            if episode_pattern:
                all_episodes = _fetch_all_episodes(client, remote, s.namespace, s.project)
                matched = _match_episodes(all_episodes, episode_pattern)

                if not matched:
                    print(f"{RED}error:{RESET} no episodes match '{episode_pattern}'", file=sys.stderr)
                    return 1

                print(f"Matched {BOLD}{len(matched)}{RESET} episode(s):")
                for ep in matched:
                    path = ep.get("nodePath", ep.get("name", ""))
                    print(f"  {CYAN}{path}{RESET}")

                confirm = input(f"\nCreate collection '{name}' with {len(matched)} episodes? [y/N] ").strip().lower()
                if confirm not in ("y", "yes"):
                    print("Cancelled.")
                    return 0

                members = [ep["id"] for ep in matched]

            body = {"name": name, "members": members}
            if description:
                body["description"] = description
            if tags:
                body["tags"] = tags

            r = client.post(
                f"{remote}/namespaces/{s.namespace}/projects/{s.project}/collections",
                json=body,
            )
            if r.status_code == 409:
                print(f"{RED}error:{RESET} collection '{name}' already exists in {format_project(s)}", file=sys.stderr)
                return 1
            if r.status_code == 404:
                print(f"{RED}error:{RESET} space '{format_project(s)}' not found", file=sys.stderr)
                return 1
            r.raise_for_status()
    except httpx.HTTPStatusError as e:
        print(f"{RED}error:{RESET} {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"{RED}error:{RESET} {e}", file=sys.stderr)
        return 1

    print(f"{GREEN}✓ Created collection:{RESET} {CYAN}{name}{RESET} in {BOLD}{format_project(s)}{RESET}")
    if members:
        print(f"  {DIM}episodes:{RESET}    {len(members)}")
    if description:
        print(f"  {DIM}description:{RESET} {description}")
    if tags:
        print(f"  {DIM}tags:{RESET}        {', '.join(tags)}")

    return 0


def cmd_create_dataset(name: str, args: dict) -> int:
    project_str = args.get("project")
    if not project_str:
        print(f"{RED}error:{RESET} --project is required", file=sys.stderr)
        return 1

    try:
        s = parse_project(project_str)
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
                f"{remote}/namespaces/{s.namespace}/projects/{s.project}/datasets",
                json=body,
            )
            if r.status_code == 409:
                print(f"{RED}error:{RESET} dataset '{name}' already exists in {format_project(s)}", file=sys.stderr)
                return 1
            if r.status_code == 404:
                print(f"{RED}error:{RESET} space '{format_project(s)}' not found", file=sys.stderr)
                return 1
            r.raise_for_status()
    except httpx.HTTPStatusError as e:
        print(f"{RED}error:{RESET} {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"{RED}error:{RESET} {e}", file=sys.stderr)
        return 1

    print(f"{GREEN}✓ Created dataset:{RESET} {CYAN}{name}{RESET} in {BOLD}{format_project(s)}{RESET}")
    if description:
        print(f"  {DIM}description:{RESET} {description}")
    if tags:
        print(f"  {DIM}tags:{RESET}        {', '.join(tags)}")

    return 0


def _extract_name_and_flags(args: list) -> tuple:
    """Extract positional name and remaining flags from args."""
    name = None
    flags = []
    for arg in args:
        if not arg.startswith("-") and name is None:
            name = arg
        else:
            flags.append(arg)
    return name, flags


def main(args: list) -> int:
    if not args or args[0] in ("-h", "--help", "help"):
        print_help()
        return 0 if args else 1

    subcommand = args[0]

    if subcommand in ("collection", "dataset"):
        name, flags = _extract_name_and_flags(args[1:])

        if not name:
            print(f"{RED}error:{RESET} {subcommand} name is required", file=sys.stderr)
            print_help()
            return 1

        parsed = args_to_dict(flags)
        if subcommand == "collection":
            return cmd_create_collection(name, parsed)
        return cmd_create_dataset(name, parsed)

    else:
        print(f"{RED}error:{RESET} unknown resource type '{subcommand}'. supported: collection, dataset", file=sys.stderr)
        return 1
