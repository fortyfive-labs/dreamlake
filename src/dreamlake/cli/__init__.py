"""
Dreamlake CLI - Command-line interface for dreamlake SDK.

Commands:
    dreamlake upload  - Upload videos from current folder to BSS
    dreamlake list    - List uploaded videos
    dreamlake status  - Check connection status
    dreamlake label   - Generate text track labels from video
"""

import sys
from typing import Optional

from .upload import UploadCommand
from .list import ListCommand
from .status import StatusCommand
from .label import LabelCommand


def main(args: Optional[list] = None) -> int:
    """
    Main CLI entry point.

    Usage:
        dreamlake upload [--path PATH] [--endpoint URL]
        dreamlake list [--endpoint URL] [--format FORMAT]
        dreamlake status [--endpoint URL]
    """
    if args is None:
        args = sys.argv[1:]

    if not args:
        print_help()
        return 0

    command = args[0]
    command_args = args[1:]

    if command in ("-h", "--help", "help"):
        print_help()
        return 0

    if command == "upload":
        return run_upload(command_args)
    elif command == "list":
        return run_list(command_args)
    elif command == "status":
        return run_status(command_args)
    elif command == "label":
        return run_label(command_args)
    else:
        print(f"Unknown command: {command}")
        print_help()
        return 1


def print_help():
    """Print CLI help message."""
    help_text = """
dreamlake - CLI for dreamlake SDK

Usage:
    dreamlake <command> [options]

Commands:
    upload    Upload videos from current folder to BSS (Big Streaming Server)
    list      List uploaded videos
    status    Check connection status to BSS
    label     Generate text track labels from video files

Options:
    -h, --help    Show this help message

Examples:
    dreamlake upload --path ./videos --endpoint bss://localhost:3112
    dreamlake list --endpoint bss://localhost:3112
    dreamlake status --endpoint bss://localhost:3112
    dreamlake label video.mp4 --interval 1.0 --format tsv

For command-specific help:
    dreamlake <command> --help
"""
    print(help_text)


def run_upload(args: list) -> int:
    """Run the upload command."""
    if "--help" in args or "-h" in args:
        UploadCommand.print_help()
        return 0

    # Parse command line args into class attributes
    UploadCommand._parse_args(args)
    return UploadCommand.run()


def run_list(args: list) -> int:
    """Run the list command."""
    if "--help" in args or "-h" in args:
        ListCommand.print_help()
        return 0

    ListCommand._parse_args(args)
    return ListCommand.run()


def run_status(args: list) -> int:
    """Run the status command."""
    if "--help" in args or "-h" in args:
        StatusCommand.print_help()
        return 0

    StatusCommand._parse_args(args)
    return StatusCommand.run()


def run_label(args: list) -> int:
    """Run the label command."""
    if "--help" in args or "-h" in args:
        LabelCommand.print_help()
        return 0

    LabelCommand._parse_args(args)
    return LabelCommand.run()


__all__ = [
    "main",
    "UploadCommand",
    "ListCommand",
    "StatusCommand",
    "LabelCommand",
]
