"""Profile command for dreamlake CLI — shows current authenticated user."""

import time

from dreamlake.auth.token_storage import get_token_storage, decode_jwt_payload
from dreamlake.config import config

TOKEN_KEY = "dreamlake-token"


def add_parser(subparsers):
    parser = subparsers.add_parser("profile", help="Show current authenticated user")
    parser.add_argument("--url", type=str, help="DreamLake server URL")


def _fetch_server_profile(token: str, server_url: str) -> dict | None:
    """Fetch live user info from GET /auth/me. Returns None on failure."""
    try:
        import httpx
        resp = httpx.get(
            f"{server_url.rstrip('/')}/auth/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5.0,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def _print_profile(data: dict, console=None) -> None:
    """Render profile data."""
    fields = ("id", "sub", "username", "name", "email", "email_verified", "picture")

    if console:
        from rich.table import Table
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Field", style="bold cyan")
        table.add_column("Value")
        for field in fields:
            value = data.get(field)
            if value is not None:
                table.add_row(field, str(value))
        ns = data.get("namespace")
        if ns:
            table.add_row("namespace", f"{ns['slug']}  [dim](id: {ns['id']})[/dim]")
        created = data.get("createdAt")
        if created:
            table.add_row("member since", str(created)[:10])
        console.print("\n[bold]Current DreamLake user[/bold]")
        console.print(table)
        console.print()
    else:
        for field in fields:
            value = data.get(field)
            if value is not None:
                print(f"{field}: {value}")
        ns = data.get("namespace")
        if ns:
            print(f"namespace: {ns['slug']} (id: {ns['id']})")
        created = data.get("createdAt")
        if created:
            print(f"member since: {str(created)[:10]}")


def cmd_profile(args) -> int:
    try:
        from rich.console import Console
        console = Console()
    except ImportError:
        console = None

    storage = get_token_storage()
    token = storage.load(TOKEN_KEY)

    if not token:
        print("Not logged in. Run: dreamlake login")
        return 1

    # Determine server URL: --url flag > config file
    server_url = getattr(args, "url", None) or config.remote_url

    if server_url:
        server_data = _fetch_server_profile(token, server_url)
        if server_data:
            _print_profile(server_data, console)
            return 0
        # Fall through to local JWT decode if server unreachable
        if console:
            console.print("[yellow]Warning: could not reach server, showing local token info.[/yellow]")
        else:
            print("Warning: could not reach server, showing local token info.")

    # Fallback: decode JWT locally
    payload = decode_jwt_payload(token)
    if not payload:
        print("Failed to decode token. Please log in again.")
        return 1

    exp = payload.get("exp")
    expired = exp and exp < time.time()

    if console:
        from rich.table import Table
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Field", style="bold cyan")
        table.add_column("Value")
        for field in ("sub", "username", "name", "email", "email_verified", "picture"):
            value = payload.get(field)
            if value is not None:
                table.add_row(field, str(value))
        if exp:
            from datetime import datetime
            exp_str = datetime.fromtimestamp(exp).strftime("%Y-%m-%d %H:%M:%S")
            label = "[red]expires[/red]" if expired else "expires"
            table.add_row(label, exp_str + (" [red](EXPIRED)[/red]" if expired else ""))
        else:
            table.add_row("expires", "never (long-lived token)")
        console.print("\n[bold]Current DreamLake user[/bold]")
        console.print(table)
        console.print()
    else:
        for field in ("sub", "username", "name", "email", "email_verified", "picture"):
            value = payload.get(field)
            if value is not None:
                print(f"{field}: {value}")
        if exp:
            from datetime import datetime
            exp_str = datetime.fromtimestamp(exp).strftime("%Y-%m-%d %H:%M:%S")
            print(f"expires: {exp_str}" + (" (EXPIRED)" if expired else ""))
        else:
            print("expires: never (long-lived token)")

    return 0
