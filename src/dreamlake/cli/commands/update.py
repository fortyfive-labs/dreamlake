"""
Update command.

Usage:
    dreamlake update collection <name> --project space[@namespace] --add <glob>
    dreamlake update collection <name> --project space[@namespace] --remove <glob>
    dreamlake update dataset <name> --project space[@namespace] --add <glob>
    dreamlake update dataset <name> --project space[@namespace] --remove <glob>
"""

import sys
from fnmatch import fnmatch

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
{BOLD}dreamlake update{RESET} - Update resources

{BOLD}Usage:{RESET}
    dreamlake update collection <name> --project space[@namespace] --add <glob>  [episodes by node path]
    dreamlake update collection <name> --project space[@namespace] --remove <glob>
    dreamlake update dataset <name> --project space[@namespace] --add <glob>   [collections by name]
    dreamlake update dataset <name> --project space[@namespace] --remove <glob>

{BOLD}Options:{RESET}
    --project        Space target: space[@namespace]
    --add          Glob pattern to add members
    --remove       Glob pattern to remove members
    --description  Update description
    --tags         Update tags (comma-separated)

{BOLD}Examples:{RESET}
    dreamlake update collection "my-set" --project robotics@alice --add "2026/04/*"
    dreamlake update collection "my-set" --project robotics@alice --remove "run-001"
    dreamlake update dataset "training-v1" --project robotics@alice --add "front-*"
    dreamlake update dataset "training-v1" --project robotics@alice --remove "old-*"
""".strip())


def _fetch_all_episodes(client, remote: str, namespace: str, space: str) -> list[dict]:
    """Fetch all episodes in a project (paginated)."""
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
    pat = pattern.lstrip("/")
    matched = []
    for ep in episodes:
        node_path = ep.get("nodePath") or ""
        path = node_path.lstrip("/")
        if fnmatch(path, pat):
            matched.append(ep)
    return matched


def cmd_update_collection(name: str, args: dict) -> int:
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

    add_pattern = args.get("add")
    remove_pattern = args.get("remove")
    description = args.get("description")
    tags_str = args.get("tags")

    if not any([add_pattern, remove_pattern, description, tags_str]):
        print(f"{RED}error:{RESET} nothing to update. use --add, --remove, --description, or --tags", file=sys.stderr)
        return 1

    import httpx
    remote = ServerConfig.remote
    headers = {"Authorization": f"Bearer {token}"}
    base = f"{remote}/namespaces/{s.namespace}/projects/{s.project}/collections/{name}"

    try:
        with httpx.Client(timeout=30, headers=headers) as client:
            # Resolve globs if needed
            all_episodes = None
            if add_pattern or remove_pattern:
                all_episodes = _fetch_all_episodes(client, remote, s.namespace, s.project)

            # ── Add episodes ─────────────────────────────────────────
            if add_pattern:
                to_add = _match_episodes(all_episodes, add_pattern)
                if not to_add:
                    print(f"{RED}error:{RESET} no episodes match '{add_pattern}'", file=sys.stderr)
                    return 1

                print(f"Adding {BOLD}{len(to_add)}{RESET} episode(s) matching '{add_pattern}':")
                for ep in to_add:
                    print(f"  {CYAN}{ep.get('nodePath', ep.get('name', ''))}{RESET}")

                r = client.post(f"{base}/members", json={"add": [ep["id"] for ep in to_add]})
                if r.status_code == 404:
                    print(f"{RED}error:{RESET} collection '{name}' not found in {format_project(s)}", file=sys.stderr)
                    return 1
                r.raise_for_status()
                result = r.json()
                print(f"{GREEN}✓ Added {len(to_add)}{RESET} → {result.get('total', '?')} total members")

            # ── Remove episodes ──────────────────────────────────────
            if remove_pattern:
                to_remove = _match_episodes(all_episodes, remove_pattern)
                if not to_remove:
                    print(f"{RED}error:{RESET} no episodes match '{remove_pattern}'", file=sys.stderr)
                    return 1

                print(f"Removing {BOLD}{len(to_remove)}{RESET} episode(s) matching '{remove_pattern}':")
                for ep in to_remove:
                    print(f"  {CYAN}{ep.get('nodePath', ep.get('name', ''))}{RESET}")

                r = client.request("DELETE", f"{base}/members", json={"remove": [ep["id"] for ep in to_remove]})
                if r.status_code == 404:
                    print(f"{RED}error:{RESET} collection '{name}' not found in {format_project(s)}", file=sys.stderr)
                    return 1
                r.raise_for_status()
                result = r.json()
                print(f"{GREEN}✓ Removed {len(to_remove)}{RESET} → {result.get('total', '?')} total members")

            # ── Update metadata ──────────────────────────────────────
            if description is not None or tags_str is not None:
                body = {}
                if description is not None:
                    body["description"] = description
                if tags_str is not None:
                    body["tags"] = [t.strip() for t in tags_str.split(",") if t.strip()]

                r = client.patch(base, json=body)
                if r.status_code == 404:
                    print(f"{RED}error:{RESET} collection '{name}' not found in {format_project(s)}", file=sys.stderr)
                    return 1
                r.raise_for_status()
                print(f"{GREEN}✓ Updated collection:{RESET} {CYAN}{name}{RESET}")

    except Exception as e:
        print(f"{RED}error:{RESET} {e}", file=sys.stderr)
        return 1

    return 0


def _fetch_all_collections(client, remote: str, namespace: str, space: str) -> list[dict]:
    """Fetch all collections in a project (paginated)."""
    collections = []
    page = 1
    while True:
        r = client.get(
            f"{remote}/namespaces/{namespace}/projects/{space}/collections",
            params={"page": str(page), "pageSize": "200"},
        )
        r.raise_for_status()
        data = r.json()
        collections.extend(data.get("collections", []))
        if page >= data.get("totalPages", 1):
            break
        page += 1
    return collections


def _match_collections(collections: list[dict], pattern: str) -> list[dict]:
    """Match collections by glob pattern against their names."""
    matched = []
    for d in collections:
        if fnmatch(d.get("name", ""), pattern):
            matched.append(d)
    return matched


def cmd_update_dataset(name: str, args: dict) -> int:
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

    add_pattern = args.get("add")
    remove_pattern = args.get("remove")
    description = args.get("description")
    tags_str = args.get("tags")

    if not any([add_pattern, remove_pattern, description, tags_str]):
        print(f"{RED}error:{RESET} nothing to update. use --add, --remove, --description, or --tags", file=sys.stderr)
        return 1

    import httpx
    remote = ServerConfig.remote
    headers = {"Authorization": f"Bearer {token}"}
    base = f"{remote}/namespaces/{s.namespace}/projects/{s.project}/datasets/{name}"

    try:
        with httpx.Client(timeout=30, headers=headers) as client:
            all_collections = None
            if add_pattern or remove_pattern:
                all_collections = _fetch_all_collections(client, remote, s.namespace, s.project)

            # ── Add collections ────────────────────────────────────────
            if add_pattern:
                to_add = _match_collections(all_collections, add_pattern)
                if not to_add:
                    print(f"{RED}error:{RESET} no collections match '{add_pattern}'", file=sys.stderr)
                    return 1

                print(f"Adding {BOLD}{len(to_add)}{RESET} collection(s) matching '{add_pattern}':")
                for d in to_add:
                    print(f"  {CYAN}{d['name']}{RESET}")

                r = client.post(f"{base}/collections", json={"add": [d["name"] for d in to_add]})
                if r.status_code == 404:
                    print(f"{RED}error:{RESET} dataset '{name}' not found in {format_project(s)}", file=sys.stderr)
                    return 1
                r.raise_for_status()
                result = r.json()
                print(f"{GREEN}✓ Added {result.get('added', len(to_add))}{RESET} → {result.get('total', '?')} total collections")

            # ── Remove collections ─────────────────────────────────────
            if remove_pattern:
                to_remove = _match_collections(all_collections, remove_pattern)
                if not to_remove:
                    print(f"{RED}error:{RESET} no collections match '{remove_pattern}'", file=sys.stderr)
                    return 1

                print(f"Removing {BOLD}{len(to_remove)}{RESET} collection(s) matching '{remove_pattern}':")
                for d in to_remove:
                    print(f"  {CYAN}{d['name']}{RESET}")

                r = client.request("DELETE", f"{base}/collections", json={"remove": [d["name"] for d in to_remove]})
                if r.status_code == 404:
                    print(f"{RED}error:{RESET} dataset '{name}' not found in {format_project(s)}", file=sys.stderr)
                    return 1
                r.raise_for_status()
                result = r.json()
                print(f"{GREEN}✓ Removed {result.get('removed', len(to_remove))}{RESET} → {result.get('total', '?')} total collections")

            # ── Update metadata ──────────────────────────────────────
            if description is not None or tags_str is not None:
                body = {}
                if description is not None:
                    body["description"] = description
                if tags_str is not None:
                    body["tags"] = [t.strip() for t in tags_str.split(",") if t.strip()]

                r = client.patch(base, json=body)
                if r.status_code == 404:
                    print(f"{RED}error:{RESET} dataset '{name}' not found in {format_project(s)}", file=sys.stderr)
                    return 1
                r.raise_for_status()
                print(f"{GREEN}✓ Updated dataset:{RESET} {CYAN}{name}{RESET}")

    except Exception as e:
        print(f"{RED}error:{RESET} {e}", file=sys.stderr)
        return 1

    return 0


def main(args: list) -> int:
    if not args or args[0] in ("-h", "--help", "help"):
        print_help()
        return 0 if args else 1

    subcommand = args[0]

    if subcommand in ("collection", "dataset"):
        remaining = args[1:]
        name = None
        flags = []
        for arg in remaining:
            if not arg.startswith("-") and name is None:
                name = arg
            else:
                flags.append(arg)

        if not name:
            print(f"{RED}error:{RESET} {subcommand} name is required", file=sys.stderr)
            print_help()
            return 1

        parsed = args_to_dict(flags)
        if subcommand == "collection":
            return cmd_update_collection(name, parsed)
        return cmd_update_dataset(name, parsed)

    else:
        print(f"{RED}error:{RESET} unknown resource type '{subcommand}'. supported: collection, dataset", file=sys.stderr)
        return 1
