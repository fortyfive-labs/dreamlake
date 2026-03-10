"""Logout command for dreamlake CLI."""

from dreamlake.auth.token_storage import get_token_storage

TOKEN_KEY = "dreamlake-token"


def add_parser(subparsers):
    subparsers.add_parser("logout", help="Log out and remove stored credentials")


def cmd_logout(args) -> int:
    try:
        from rich.console import Console
        console = Console()
        _print = console.print
    except ImportError:
        _print = print
    try:
        storage = get_token_storage()
        storage.delete(TOKEN_KEY)
        _print("[green]✓ Logged out successfully.[/green]")
        return 0
    except Exception as e:
        _print(f"[red]✗ Logout failed:[/red] {e}")
        return 1
