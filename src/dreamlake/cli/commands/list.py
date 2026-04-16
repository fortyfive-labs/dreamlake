"""
List command.

Usage:
    dreamlake list --episode space[@namespace][:episode] [--prefix <path>] [--type <category>]
    dreamlake list dreamlet --space space[@namespace]

"""

import sys

from params_proto import proto

from dreamlake.cli._args import args_to_dict
from dreamlake.cli._config import ServerConfig
from dreamlake.cli._target import parse_target, parse_space, format_target, format_space

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
CYAN = "\033[36m"

CATEGORIES = {"audio", "video", "track", "text-track", "label-track"}


@proto.prefix
class ListConfig:
    episode: str | None = None   # space[@namespace][:episode]
    space: str | None = None     # space[@namespace] (for dreamlet listing)
    prefix: str | None = None    # path prefix filter
    type: str | None = None      # category filter (omit for all)


def print_help():
    print(f"""
{BOLD}dreamlake list{RESET} - List assets or dreamlets

{BOLD}Usage:{RESET}
    dreamlake list --episode space[@namespace][:episode] [--prefix <path>] [--type <category>]
    dreamlake list dreamlet --space space[@namespace]

{BOLD}Options:{RESET}
    --episode   Target: space[@namespace][:episode]
    --space     Space target: space[@namespace] (for dreamlet listing)
    --prefix    Filter by path prefix (optional)
    --type      Filter by category: audio, video, track, text-track, label-track (optional)

{BOLD}Examples:{RESET}
    dreamlake list --episode robotics@alice:run-042
    dreamlake list --episode robotics@alice:run-042 --type video
    dreamlake list dreamlet --space robotics@alice
    dreamlake list dreamlet --space robotics
""".strip())


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

    params = {"namespace": t.namespace, "space": t.space}
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


# ── List dreamlets ────────────────────────────────────────────────────────────

def cmd_list_dreamlets() -> int:
    if not ListConfig.space:
        print(f"{RED}error:{RESET} --space is required for listing dreamlets", file=sys.stderr)
        return 1

    try:
        s = parse_space(ListConfig.space)
    except ValueError as e:
        print(f"{RED}error:{RESET} {e}", file=sys.stderr)
        return 1

    s.namespace = _resolve_namespace(s.namespace)
    if not s.namespace:
        return 1

    token = _resolve_token()
    if not token:
        return 1

    scope = format_space(s)
    print(f"Listing dreamlets in {BOLD}{scope}{RESET}")

    import httpx
    remote = ServerConfig.remote
    headers = {"Authorization": f"Bearer {token}"}

    try:
        with httpx.Client(timeout=30, headers=headers) as client:
            r = client.get(
                f"{remote}/namespaces/{s.namespace}/spaces/{s.space}/dreamlets",
                params={"pageSize": "200"},
            )
            if r.status_code == 404:
                print(f"\n  {DIM}(space not found){RESET}")
                return 0
            r.raise_for_status()
            data = r.json()
            dreamlets = data.get("dreamlets", [])
    except Exception as e:
        print(f"{RED}error:{RESET} {e}", file=sys.stderr)
        return 1

    if not dreamlets:
        print(f"\n  {DIM}(no dreamlets found){RESET}")
        return 0

    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(show_edge=False, pad_edge=False)
    table.add_column("Name", style="cyan")
    table.add_column("Files", justify="right")
    table.add_column("Tags")
    table.add_column("Description", style="dim")
    table.add_column("Created", style="dim")
    table.add_column("ID", style="dim")

    for d in dreamlets:
        members = d.get("members", [])
        member_count = str(len(members)) if isinstance(members, list) else "0"
        tags = ", ".join(d.get("tags", []))
        desc = (d.get("description") or "")[:40]
        created = (d.get("createdAt") or "")[:10]
        did = (d.get("id") or "")[:12]
        table.add_row(d.get("name", ""), member_count, tags, desc, created, did)

    console.print(table)
    console.print(f"\n  {len(dreamlets)} dreamlet(s)")
    return 0


# ── Entry point ───────────────────────────────────────────────────────────────

def main(args: list) -> int:
    if not args or args[0] in ("-h", "--help", "help"):
        print_help()
        return 0 if args else 1

    # Check for subcommand: dreamlake list dreamlet --space ...
    if args[0] == "dreamlet":
        ListConfig._update(args_to_dict(args[1:]))
        return cmd_list_dreamlets()

    # Default: list assets
    ListConfig._update(args_to_dict(args))
    return cmd_list_assets()
