"""
Status command for dreamlake CLI.

Check connection status to BSS (Big Streaming Server).
"""

import socket
import time
from typing import Dict, Any, Optional

from params_proto import proto


@proto
class StatusCommand:
    """
    Check connection status to BSS (Big Streaming Server).

    Usage:
        dreamlake status [--endpoint URL]

    Examples:
        dreamlake status
        dreamlake status --endpoint bss://localhost:3112
    """

    endpoint: str = "bss://localhost:3112"
    """BSS endpoint URL (Big Streaming Server)"""

    timeout: float = 5.0
    """Connection timeout in seconds"""

    verbose: bool = False
    """Show verbose output"""

    @classmethod
    def print_help(cls):
        """Print help message for status command."""
        help_text = """
dreamlake status - Check connection status to BSS

Usage:
    dreamlake status [options]

Options:
    --endpoint URL       BSS endpoint URL (default: bss://localhost:3112)
    --timeout SECONDS    Connection timeout (default: 5.0)
    --verbose            Show verbose output
    -h, --help           Show this help message

Examples:
    dreamlake status
    dreamlake status --endpoint bss://192.168.1.100:3112
    dreamlake status --timeout 10
"""
        print(help_text)

    @classmethod
    def _parse_args(cls, args: list):
        """Parse command line arguments and update class attributes."""
        i = 0
        while i < len(args):
            arg = args[i]
            if arg == "--endpoint" and i + 1 < len(args):
                cls.endpoint = args[i + 1]
                i += 2
            elif arg == "--timeout" and i + 1 < len(args):
                cls.timeout = float(args[i + 1])
                i += 2
            elif arg == "--verbose":
                cls.verbose = True
                i += 1
            else:
                i += 1

    @classmethod
    def _parse_endpoint(cls) -> tuple:
        """Parse BSS endpoint URL into (host, port)."""
        endpoint = cls.endpoint

        # Handle bss:// protocol
        if endpoint.startswith("bss://"):
            endpoint = endpoint[6:]
        elif endpoint.startswith("http://"):
            endpoint = endpoint[7:]
        elif endpoint.startswith("https://"):
            endpoint = endpoint[8:]

        # Split host:port
        if ":" in endpoint:
            host, port_str = endpoint.split(":", 1)
            port = int(port_str)
        else:
            host = endpoint
            port = 3112

        return host, port

    @classmethod
    def _check_socket_connection(cls, host: str, port: int) -> tuple:
        """
        Check if we can establish a socket connection.

        Returns:
            (success: bool, latency_ms: float or None, error: str or None)
        """
        try:
            start_time = time.time()
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(cls.timeout)
            sock.connect((host, port))
            latency_ms = (time.time() - start_time) * 1000
            sock.close()
            return True, latency_ms, None
        except socket.timeout:
            return False, None, "Connection timed out"
        except socket.gaierror as e:
            return False, None, f"DNS resolution failed: {e}"
        except ConnectionRefusedError:
            return False, None, "Connection refused"
        except Exception as e:
            return False, None, str(e)

    @classmethod
    def _get_server_info(cls, host: str, port: int) -> Optional[Dict[str, Any]]:
        """
        Get server information from BSS.

        This is a stub implementation - actual API call would go here.
        """
        # TODO: Implement actual BSS API call to get server info
        # This would typically:
        # 1. Connect to BSS
        # 2. Send info/health request
        # 3. Parse and return response

        # Return stub data for now
        return {
            "version": "1.0.0",
            "uptime_seconds": 86400,
            "video_count": 42,
            "storage_used_gb": 150.5,
            "storage_total_gb": 500.0,
        }

    @classmethod
    def _format_uptime(cls, seconds: int) -> str:
        """Format uptime in human-readable format."""
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        minutes = (seconds % 3600) // 60

        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")

        return " ".join(parts) if parts else "< 1m"

    @classmethod
    def run(cls) -> int:
        """Execute the status command."""
        # Parse endpoint
        try:
            host, port = cls._parse_endpoint()
        except ValueError as e:
            print(f"Error: Invalid endpoint format: {cls.endpoint}")
            return 1

        print(f"Checking connection to bss://{host}:{port}...")
        print()

        # Check socket connection
        connected, latency_ms, error = cls._check_socket_connection(host, port)

        if not connected:
            print(f"Status: DISCONNECTED")
            print(f"Error: {error}")
            return 1

        print(f"Status: CONNECTED")
        print(f"Latency: {latency_ms:.1f} ms")

        if cls.verbose:
            print()
            print("Fetching server info...")

            try:
                info = cls._get_server_info(host, port)
                if info:
                    print()
                    print("Server Info:")
                    print(f"  Version: {info.get('version', 'unknown')}")
                    print(f"  Uptime: {cls._format_uptime(info.get('uptime_seconds', 0))}")
                    print(f"  Videos: {info.get('video_count', 0)}")
                    storage_used = info.get('storage_used_gb', 0)
                    storage_total = info.get('storage_total_gb', 0)
                    storage_pct = (storage_used / storage_total * 100) if storage_total > 0 else 0
                    print(f"  Storage: {storage_used:.1f} GB / {storage_total:.1f} GB ({storage_pct:.1f}%)")
            except Exception as e:
                print(f"Warning: Could not fetch server info: {e}")

        return 0
