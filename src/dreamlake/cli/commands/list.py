"""
List command.

Usage:
    dreamlake list --episode space[@namespace][:episode] [--prefix <path>] [--type <category>]
    dreamlake list collection --project space[@namespace]
    dreamlake list dataset --project space[@namespace]
    dreamlake list episode --project space[@namespace]

"""

import sys

from params_proto import proto

from dreamlake.cli._args import args_to_dict
from dreamlake.cli._config import ServerConfig
from dreamlake.cli._target import parse_target, parse_project, format_target, format_project

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
CYAN = "\033[36m"

CATEGORIES = {"audio", "video", "track", "text-track", "label-track"}


PAGE_SIZE = 20


@proto.prefix
class ListConfig:
    episode: str | None = None   # space[@namespace][:episode]
    space: str | None = None     # space[@namespace] (for collection listing)
    prefix: str | None = None    # path prefix filter
    type: str | None = None      # category filter (omit for all)


def print_help():
    print(f"""
{BOLD}dreamlake list{RESET} - List assets or collections

{BOLD}Usage:{RESET}
    dreamlake list --episode space[@namespace][:episode] [--prefix <path>] [--type <category>]
    dreamlake list collection --project space[@namespace]
    dreamlake list dataset --project space[@namespace]
    dreamlake list episode --project space[@namespace]

{BOLD}Options:{RESET}
    --episode   Target: space[@namespace][:episode]
    --project     Space target: space[@namespace] (for collection/dataset/episode listing)
    --prefix    Filter by path prefix (optional)
    --type      Filter by category: audio, video, track, text-track, label-track (optional)

{BOLD}Examples:{RESET}
    dreamlake list --episode robotics@alice:run-042
    dreamlake list --episode robotics@alice:run-042 --type video
    dreamlake list collection --project robotics@alice
    dreamlake list dataset --project robotics@alice
    dreamlake list episode --project robotics@alice
""".strip())


def _pager_prompt(page: int, total_pages: int) -> str | None:
    """Show pagination prompt. Returns 'n', 'p', or None to quit."""
    if total_pages <= 1:
        return None
    hints = []
    if page < total_pages:
        hints.append("[n]ext")
    if page > 1:
        hints.append("[p]rev")
    hints.append("[q]uit")
    try:
        choice = input(f"\n  Page {page}/{total_pages}  {' '.join(hints)}: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if choice in ("n", "next") and page < total_pages:
        return "n"
    if choice in ("p", "prev") and page > 1:
        return "p"
    return None


def _resolve_namespace(namespace: str | None) -> str | None:
    if namespace:
        return namespace
    ns = ServerConfig.resolve_namespace()
    if not ns:
        print(f"{RED}error:{RESET} namespace not specified and no authenticated user found. run 'dreamlake login'", file=sys.stderr)
    return ns


def _resolve_token() -> str | None:
    token = ServerConfig.resolve_token()
    if not token:
        print(f"{RED}error:{RESET} not authenticated. run 'dreamlake login' first", file=sys.stderr)
    return token


# ── List assets ───────────────────────────────────────────────────────────────

def cmd_list_assets() -> int:
    if not ListConfig.episode:
        print(f"{RED}error:{RESET} --episode is required", file=sys.stderr)
        return 1

    if ListConfig.type and ListConfig.type not in CATEGORIES:
        print(f"{RED}error:{RESET} unknown type '{ListConfig.type}'. valid: {', '.join(sorted(CATEGORIES))}", file=sys.stderr)
        return 1

    try:
        t = parse_target(ListConfig.episode)
    except ValueError as e:
        print(f"{RED}error:{RESET} {e}", file=sys.stderr)
        return 1

    t.namespace = _resolve_namespace(t.namespace)
    if not t.namespace:
        return 1

    token = _resolve_token()
    if not token:
        return 1

    scope = format_target(t)
    type_label = ListConfig.type or "all"
    print(f"Listing {CYAN}{type_label}{RESET} assets in {BOLD}{scope}{RESET}")

    import httpx
    remote = ServerConfig.remote
    headers = {"Authorization": f"Bearer {token}"}

    params = {"namespace": t.namespace, "project": t.project}
    if t.episode:
        params["episode"] = t.episode
    if ListConfig.prefix:
        params["prefix"] = ListConfig.prefix

    requested_type = ListConfig.type
    implemented = {"video"}
    types_to_query = [requested_type] if requested_type else list(implemented)

    all_assets: list[tuple[str, dict]] = []

    try:
        with httpx.Client(timeout=30, headers=headers) as client:
            for asset_type in types_to_query:
                if asset_type not in implemented:
                    print(f"  {DIM}(listing '{asset_type}' not yet implemented){RESET}")
                    continue
                r = client.get(f"{remote}/assets/{asset_type}", params=params)
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                for item in r.json():
                    all_assets.append((asset_type, item))
    except Exception as e:
        print(f"{RED}error:{RESET} {e}", file=sys.stderr)
        return 1

    if not all_assets:
        print(f"\n  {DIM}(no assets found){RESET}")
        return 0

    print()
    for asset_type, item in all_assets:
        name = item.get("name") or "(unnamed)"
        created = (item.get("createdAt") or "")[:10]
        print(f"  {CYAN}{name}{RESET}  {DIM}[{asset_type}]  {created}{RESET}")

    print(f"\n  {len(all_assets)} asset(s)")
    return 0


# ── List collections ────────────────────────────────────────────────────────────

def _render_collections(collections: list[dict], total: int, page: int, total_pages: int):
    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(show_edge=False, pad_edge=False)
    table.add_column("Name", style="cyan")
    table.add_column("Files", justify="right")
    table.add_column("Tags")
    table.add_column("Description", style="dim")
    table.add_column("Created", style="dim")

    for d in collections:
        members = d.get("members", [])
        member_count = str(len(members)) if isinstance(members, list) else "0"
        tags = ", ".join(d.get("tags", []))
        desc = (d.get("description") or "")[:40]
        created = (d.get("createdAt") or "")[:10]
        table.add_row(d.get("name", ""), member_count, tags, desc, created)

    console.print(table)
    console.print(f"\n  {total} collection(s)")


def cmd_list_collections() -> int:
    if not ListConfig.project:
        print(f"{RED}error:{RESET} --project is required for listing collections", file=sys.stderr)
        return 1

    try:
        s = parse_project(ListConfig.project)
    except ValueError as e:
        print(f"{RED}error:{RESET} {e}", file=sys.stderr)
        return 1

    s.namespace = _resolve_namespace(s.namespace)
    if not s.namespace:
        return 1

    token = _resolve_token()
    if not token:
        return 1

    scope = format_project(s)
    print(f"Listing collections in {BOLD}{scope}{RESET}")

    import httpx
    remote = ServerConfig.remote
    headers = {"Authorization": f"Bearer {token}"}
    page = 1

    try:
        with httpx.Client(timeout=30, headers=headers) as client:
            while True:
                r = client.get(
                    f"{remote}/namespaces/{s.namespace}/projects/{s.project}/collections",
                    params={"page": str(page), "pageSize": str(PAGE_SIZE)},
                )
                if r.status_code == 404:
                    print(f"\n  {DIM}(space not found){RESET}")
                    return 0
                r.raise_for_status()
                data = r.json()
                collections = data.get("collections", [])
                total = data.get("total", 0)
                total_pages = data.get("totalPages", 1)

                if not collections:
                    print(f"\n  {DIM}(no collections found){RESET}")
                    return 0

                _render_collections(collections, total, page, total_pages)
                action = _pager_prompt(page, total_pages)
                if action == "n":
                    page += 1
                elif action == "p":
                    page -= 1
                else:
                    break
    except Exception as e:
        print(f"{RED}error:{RESET} {e}", file=sys.stderr)
        return 1

    return 0


# ── List datasets ────────────────────────────────────────────────────────────

def _render_datasets(datasets: list[dict], total: int):
    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(show_edge=False, pad_edge=False)
    table.add_column("Name", style="cyan")
    table.add_column("Collections", justify="right")
    table.add_column("Tags")
    table.add_column("Description", style="dim")
    table.add_column("Created", style="dim")

    for d in datasets:
        collections = d.get("collections", [])
        collection_count = str(len(collections)) if isinstance(collections, list) else "0"
        tags = ", ".join(d.get("tags", []))
        desc = (d.get("description") or "")[:40]
        created = (d.get("createdAt") or "")[:10]
        table.add_row(d.get("name", ""), collection_count, tags, desc, created)

    console.print(table)
    console.print(f"\n  {total} dataset(s)")


def cmd_list_datasets() -> int:
    if not ListConfig.project:
        print(f"{RED}error:{RESET} --project is required for listing datasets", file=sys.stderr)
        return 1

    try:
        s = parse_project(ListConfig.project)
    except ValueError as e:
        print(f"{RED}error:{RESET} {e}", file=sys.stderr)
        return 1

    s.namespace = _resolve_namespace(s.namespace)
    if not s.namespace:
        return 1

    token = _resolve_token()
    if not token:
        return 1

    scope = format_project(s)
    print(f"Listing datasets in {BOLD}{scope}{RESET}")

    import httpx
    remote = ServerConfig.remote
    headers = {"Authorization": f"Bearer {token}"}
    page = 1

    try:
        with httpx.Client(timeout=30, headers=headers) as client:
            while True:
                r = client.get(
                    f"{remote}/namespaces/{s.namespace}/projects/{s.project}/datasets",
                    params={"page": str(page), "pageSize": str(PAGE_SIZE)},
                )
                if r.status_code == 404:
                    print(f"\n  {DIM}(space not found){RESET}")
                    return 0
                r.raise_for_status()
                data = r.json()
                datasets = data.get("datasets", [])
                total = data.get("total", 0)
                total_pages = data.get("totalPages", 1)

                if not datasets:
                    print(f"\n  {DIM}(no datasets found){RESET}")
                    return 0

                _render_datasets(datasets, total)
                action = _pager_prompt(page, total_pages)
                if action == "n":
                    page += 1
                elif action == "p":
                    page -= 1
                else:
                    break
    except Exception as e:
        print(f"{RED}error:{RESET} {e}", file=sys.stderr)
        return 1

    return 0


# ── List episodes ────────────────────────────────────────────────────────────

def _render_episodes(episodes: list[dict], total: int):
    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(show_edge=False, pad_edge=False)
    table.add_column("Name", style="cyan")
    table.add_column("Path", style="dim")
    table.add_column("Status")
    table.add_column("Tags")
    table.add_column("Description", style="dim")
    table.add_column("Created", style="dim")

    for ep in episodes:
        node_path = ep.get("nodePath") or ""
        status = ep.get("status", "")
        tags = ", ".join(ep.get("tags", []))
        desc = (ep.get("description") or "")[:40]
        created = (ep.get("createdAt") or "")[:10]
        table.add_row(ep.get("name", ""), node_path, status, tags, desc, created)

    console.print(table)
    console.print(f"\n  {total} episode(s)")


def cmd_list_episodes() -> int:
    if not ListConfig.project:
        print(f"{RED}error:{RESET} --project is required for listing episodes", file=sys.stderr)
        return 1

    try:
        s = parse_project(ListConfig.project)
    except ValueError as e:
        print(f"{RED}error:{RESET} {e}", file=sys.stderr)
        return 1

    s.namespace = _resolve_namespace(s.namespace)
    if not s.namespace:
        return 1

    token = _resolve_token()
    if not token:
        return 1

    scope = format_project(s)
    print(f"Listing episodes in {BOLD}{scope}{RESET}")

    import httpx
    remote = ServerConfig.remote
    headers = {"Authorization": f"Bearer {token}"}
    page = 1

    try:
        with httpx.Client(timeout=30, headers=headers) as client:
            while True:
                r = client.get(
                    f"{remote}/namespaces/{s.namespace}/projects/{s.project}/episodes",
                    params={"page": str(page), "pageSize": str(PAGE_SIZE)},
                )
                if r.status_code == 404:
                    print(f"\n  {DIM}(space not found){RESET}")
                    return 0
                r.raise_for_status()
                data = r.json()
                episodes = data.get("episodes", [])
                total = data.get("total", 0)
                total_pages = data.get("totalPages", 1)

                if not episodes:
                    print(f"\n  {DIM}(no episodes found){RESET}")
                    return 0

                _render_episodes(episodes, total)
                action = _pager_prompt(page, total_pages)
                if action == "n":
                    page += 1
                elif action == "p":
                    page -= 1
                else:
                    break
    except Exception as e:
        print(f"{RED}error:{RESET} {e}", file=sys.stderr)
        return 1

    return 0


# ── Entry point ───────────────────────────────────────────────────────────────

def main(args: list) -> int:
    if not args or args[0] in ("-h", "--help", "help"):
        print_help()
        return 0 if args else 1

    # Check for subcommand: dreamlake list collection/dataset/episode --project ...
    if args[0] == "collection":
        ListConfig._update(args_to_dict(args[1:]))
        return cmd_list_collections()

    if args[0] == "dataset":
        ListConfig._update(args_to_dict(args[1:]))
        return cmd_list_datasets()

    if args[0] == "episode":
        ListConfig._update(args_to_dict(args[1:]))
        return cmd_list_episodes()

    # Default: list assets
    ListConfig._update(args_to_dict(args))
    return cmd_list_assets()
