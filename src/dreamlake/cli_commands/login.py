"""Login command for dreamlake CLI."""

import webbrowser

from dreamlake.auth.device_flow import DeviceFlowClient
from dreamlake.auth.device_secret import get_or_create_device_secret
from dreamlake.auth.exceptions import (
    AuthorizationDeniedError,
    DeviceCodeExpiredError,
    TokenExchangeError,
)
from dreamlake.auth.token_storage import get_token_storage
from dreamlake.config import config

TOKEN_KEY = "dreamlake-token"


def add_parser(subparsers):
    parser = subparsers.add_parser(
        "login",
        help="Authenticate with DreamLake using device authorization flow",
    )
    parser.add_argument(
        "--url",
        type=str,
        help="DreamLake server URL (e.g., http://localhost:3000)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't automatically open browser for authorization",
    )


def _generate_qr_code_ascii(url: str) -> str:
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        lines = []
        for row in qr.get_matrix():
            lines.append("".join("██" if cell else "  " for cell in row))
        return "\n".join(lines)
    except ImportError:
        return "[QR code unavailable — install qrcode: pip install qrcode]"
    except Exception:
        return "[QR code generation failed]"


def cmd_login(args) -> int:
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.progress import Progress, SpinnerColumn, TextColumn
    except ImportError:
        print("Error: 'rich' is required for login. Run with: uv run --extra auth dreamlake login")
        return 1

    console = Console()

    remote_url = getattr(args, "url", None) or config.remote_url
    if not remote_url:
        console.print(
            "[red]Error: No server URL configured.[/red]\n\n"
            "Specify with --url:\n"
            "  dreamlake login --url http://localhost:3000"
        )
        return 1

    try:
        console.print("[bold]Initializing device authorization...[/bold]\n")

        device_secret = get_or_create_device_secret(config)
        client = DeviceFlowClient(
            device_secret=device_secret,
            dreamlake_server_url=remote_url,
        )

        flow = client.start_device_flow()
        qr_code = _generate_qr_code_ascii(flow.verification_uri_complete)

        panel_content = (
            f"[bold cyan]1. Visit this URL:[/bold cyan]\n\n"
            f"   {flow.verification_uri}\n\n"
            f"[bold cyan]2. Enter this code:[/bold cyan]\n\n"
            f"   [bold green]{flow.user_code}[/bold green]\n\n"
        )
        if "unavailable" not in qr_code and "failed" not in qr_code:
            panel_content += f"[bold cyan]Or scan QR code:[/bold cyan]\n\n{qr_code}\n\n"
        panel_content += f"[dim]Code expires in {flow.expires_in // 60} minutes[/dim]"

        console.print(
            Panel(
                panel_content,
                title="[bold blue]DEVICE AUTHORIZATION REQUIRED[/bold blue]",
                border_style="blue",
                expand=False,
            )
        )
        console.print()

        if not getattr(args, "no_browser", False):
            try:
                webbrowser.open(flow.verification_uri_complete)
                console.print("[dim]✓ Opened browser automatically[/dim]\n")
            except Exception:
                pass

        console.print("[bold]Waiting for authorization...[/bold]")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("Polling", total=None)

            def update_progress(elapsed: int):
                progress.update(task, description=f"Waiting ({elapsed}s)")

            try:
                vuer_auth_token = client.poll_for_token(
                    max_attempts=120, progress_callback=update_progress
                )
            except DeviceCodeExpiredError:
                console.print(
                    "\n[red]✗ Device code expired[/red]\n\n"
                    "Please run 'dreamlake login' again."
                )
                return 1
            except AuthorizationDeniedError:
                console.print(
                    "\n[red]✗ Authorization denied[/red]\n\n"
                    "Please run 'dreamlake login' again."
                )
                return 1
            except TimeoutError:
                console.print(
                    "\n[red]✗ Authorization timed out[/red]\n\n"
                    "Please run 'dreamlake login' again."
                )
                return 1

        console.print("[green]✓ Authorization successful![/green]\n")
        console.print("[bold]Exchanging token with DreamLake server...[/bold]")

        try:
            dreamlake_token = client.exchange_token(vuer_auth_token)
        except TokenExchangeError as e:
            console.print(f"\n[red]✗ Token exchange failed:[/red] {e}\n")
            return 1

        storage = get_token_storage()
        storage.store(TOKEN_KEY, dreamlake_token)

        console.print("[green]✓ Token exchanged successfully![/green]\n")
        console.print(
            "[bold green]✓ Logged in successfully![/bold green]\n\n"
            "Your authentication token has been securely stored.\n"
            "You can now use dreamlake commands without --api-key."
        )
        return 0

    except KeyboardInterrupt:
        console.print("\n\n[yellow]Login cancelled.[/yellow]")
        return 1
    except Exception as e:
        console.print(f"\n[red]✗ Unexpected error:[/red] {e}")
        return 1
