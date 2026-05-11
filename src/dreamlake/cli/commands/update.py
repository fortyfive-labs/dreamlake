"""
Update command.

Usage:
    dreamlake update bindr <name> --project space[@namespace] --add <glob>
    dreamlake update bindr <name> --project space[@namespace] --remove <glob>
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
    dreamlake update bindr <name> --project space[@namespace] --add <glob>  [episodes by node path]
    dreamlake update bindr <name> --project space[@namespace] --remove <glob>
    dreamlake update dataset <name> --project space[@namespace] --add <glob>   [bindrs by name]
    dreamlake update dataset <name> --project space[@namespace] --remove <glob>

{BOLD}Options:{RESET}
    --project        Space target: space[@namespace]
    --add          Glob pattern to add members
    --remove       Glob pattern to remove members
    --description  Update description
    --tags         Update tags (comma-separated)

{BOLD}Examples:{RESET}
    dreamlake update bindr "my-set" --project robotics@alice --add "2026/04/*"
    dreamlake update bindr "my-set" --project robotics@alice --remove "run-001"
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


def cmd_update_bindr(name: str, args: dict) -> int:
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
    base = f"{remote}/namespaces/{s.namespace}/projects/{s.project}/bindrs/{name}"

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
                    print(f"{RED}error:{RESET} bindr '{name}' not found in {format_project(s)}", file=sys.stderr)
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
                    print(f"{RED}error:{RESET} bindr '{name}' not found in {format_project(s)}", file=sys.stderr)
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
                    print(f"{RED}error:{RESET} bindr '{name}' not found in {format_project(s)}", file=sys.stderr)
                    return 1
                r.raise_for_status()
                print(f"{GREEN}✓ Updated bindr:{RESET} {CYAN}{name}{RESET}")

    except Exception as e:
        print(f"{RED}error:{RESET} {e}", file=sys.stderr)
        return 1

    return 0


def _fetch_all_bindrs(client, remote: str, namespace: str, space: str) -> list[dict]:
    """Fetch all bindrs in a project (paginated)."""
    bindrs = []
    page = 1
    while True:
        r = client.get(
            f"{remote}/namespaces/{namespace}/projects/{space}/bindrs",
            params={"page": str(page), "pageSize": "200"},
        )
        r.raise_for_status()
        data = r.json()
        bindrs.extend(data.get("bindrs", []))
        if page >= data.get("totalPages", 1):
            break
        page += 1
    return bindrs


def _match_bindrs(bindrs: list[dict], pattern: str) -> list[dict]:
    """Match bindrs by glob pattern against their names."""
    matched = []
    for d in bindrs:
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
            all_bindrs = None
            if add_pattern or remove_pattern:
                all_bindrs = _fetch_all_bindrs(client, remote, s.namespace, s.project)

            # ── Add bindrs ────────────────────────────────────────
            if add_pattern:
                to_add = _match_bindrs(all_bindrs, add_pattern)
                if not to_add:
                    print(f"{RED}error:{RESET} no bindrs match '{add_pattern}'", file=sys.stderr)
                    return 1

                print(f"Adding {BOLD}{len(to_add)}{RESET} bindr(s) matching '{add_pattern}':")
                for d in to_add:
                    print(f"  {CYAN}{d['name']}{RESET}")

                r = client.post(f"{base}/bindrs", json={"add": [d["name"] for d in to_add]})
                if r.status_code == 404:
                    print(f"{RED}error:{RESET} dataset '{name}' not found in {format_project(s)}", file=sys.stderr)
                    return 1
                r.raise_for_status()
                result = r.json()
                print(f"{GREEN}✓ Added {result.get('added', len(to_add))}{RESET} → {result.get('total', '?')} total bindrs")

            # ── Remove bindrs ─────────────────────────────────────
            if remove_pattern:
                to_remove = _match_bindrs(all_bindrs, remove_pattern)
                if not to_remove:
                    print(f"{RED}error:{RESET} no bindrs match '{remove_pattern}'", file=sys.stderr)
                    return 1

                print(f"Removing {BOLD}{len(to_remove)}{RESET} bindr(s) matching '{remove_pattern}':")
                for d in to_remove:
                    print(f"  {CYAN}{d['name']}{RESET}")

                r = client.request("DELETE", f"{base}/bindrs", json={"remove": [d["name"] for d in to_remove]})
                if r.status_code == 404:
                    print(f"{RED}error:{RESET} dataset '{name}' not found in {format_project(s)}", file=sys.stderr)
                    return 1
                r.raise_for_status()
                result = r.json()
                print(f"{GREEN}✓ Removed {result.get('removed', len(to_remove))}{RESET} → {result.get('total', '?')} total bindrs")

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

    if subcommand in ("bindr", "dataset"):
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
        if subcommand == "bindr":
            return cmd_update_bindr(name, parsed)
        return cmd_update_dataset(name, parsed)

    else:
        print(f"{RED}error:{RESET} unknown resource type '{subcommand}'. supported: bindr, dataset", file=sys.stderr)
        return 1
