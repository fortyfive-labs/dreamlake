"""
List command.

Usage:
    dreamlake list --episode [namespace@]space[:episode] [--prefix <path>] [--type <category>]

Omit --type to list all categories. --prefix filters by path prefix.
"""

import sys

from params_proto import proto

from dreamlake.cli._args import args_to_dict
from dreamlake.cli._config import ServerConfig
from dreamlake.cli._target import parse_target, format_target

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
CYAN = "\033[36m"

CATEGORIES = {"audio", "video", "track", "text-track", "label-track"}


@proto.prefix
class ListConfig:
    sess: str | None = None     # [namespace@]space[:episode]
    prefix: str | None = None   # path prefix filter
    type: str | None = None     # category filter (omit for all)


def print_help():
    print(f"""
{BOLD}dreamlake list{RESET} - List assets in DreamLake

{BOLD}Usage:{RESET}
    dreamlake list --episode [namespace@]space[:episode] [--prefix <path>] [--type <category>]

{BOLD}Options:{RESET}
    --episode      Episode scope: [namespace@]space[:episode]
    --prefix    Filter by path prefix (optional)
    --type      Filter by category: audio, video, track, text-track, label-track (optional)

{BOLD}Examples:{RESET}
    dreamlake list --episode alice@robotics:2026/q1/run-042
    dreamlake list --episode alice@robotics:experiments/run-042 --type audio
    dreamlake list --episode alice@robotics --type track
    dreamlake list --episode robotics:experiments/run-042 --prefix /microphone
""".strip())


def cmd_list() -> int:
    if not ListConfig.sess:
        print(f"{RED}error:{RESET} --episode is required", file=sys.stderr)
        return 1

    if ListConfig.type and ListConfig.type not in CATEGORIES:
        print(
            f"{RED}error:{RESET} unknown type '{ListConfig.type}'. valid: {', '.join(sorted(CATEGORIES))}",
            file=sys.stderr,
        )
        return 1

    try:
        t = parse_target(ListConfig.sess)
    except ValueError as e:
        print(f"{RED}error:{RESET} {e}", file=sys.stderr)
        return 1

    if not t.namespace:
        t.namespace = ServerConfig.resolve_namespace()
        if not t.namespace:
            print(
                f"{RED}error:{RESET} namespace not specified and no authenticated user found. run 'dreamlake login'",
                file=sys.stderr,
            )
            return 1

    token = ServerConfig.resolve_token()
    if not token:
        print(f"{RED}error:{RESET} not authenticated. run 'dreamlake login' first", file=sys.stderr)
        return 1

    scope = format_target(t)
    type_label = ListConfig.type or "all"
    print(f"Listing {CYAN}{type_label}{RESET} assets in {BOLD}{scope}{RESET}")

    try:
        return _list_assets(t, token)
    except Exception as e:
        print(f"{RED}error:{RESET} {e}", file=sys.stderr)
        return 1


def _list_assets(t, token: str) -> int:
    """List assets from dreamlake-server."""
    import httpx

    remote = ServerConfig.remote
    headers = {"Authorization": f"Bearer {token}"}

    params = {"namespace": t.namespace, "space": t.space}
    if t.episode:
        params["episode"] = t.episode
    if ListConfig.prefix:
        params["prefix"] = ListConfig.prefix

    # Determine which types to query
    requested_type = ListConfig.type
    implemented = {"video"}
    types_to_query = [requested_type] if requested_type else list(implemented)

    all_assets: list[tuple[str, dict]] = []

    with httpx.Client(timeout=30, headers=headers) as client:
        for asset_type in types_to_query:
            if asset_type not in implemented:
                print(f"  {DIM}(listing '{asset_type}' not yet implemented){RESET}")
                continue
            r = client.get(f"{remote}/assets/{asset_type}", params=params)
            if r.status_code == 404:
                # namespace or space not found — not an error, just nothing there
                continue
            r.raise_for_status()
            for item in r.json():
                all_assets.append((asset_type, item))

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


def main(args: list) -> int:
    if not args or args[0] in ("-h", "--help", "help"):
        print_help()
        return 0 if args else 1

    ListConfig._update(args_to_dict(args))
    return cmd_list()
