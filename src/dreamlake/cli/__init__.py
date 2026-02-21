"""
Dreamlake CLI - Command-line interface for dreamlake SDK.

Commands:
    dreamlake upload   - Upload videos from current folder to BSS
    dreamlake list     - List uploaded videos
    dreamlake status   - Check connection status
    dreamlake pipeline - Run video processing pipelines
"""

import sys
from typing import Optional

from .upload import UploadCommand
from .list import ListCommand
from .status import StatusCommand
from .pipeline import PipelineCommand


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
    elif command == "pipeline":
        return run_pipeline(command_args)
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
    pipeline  Run video processing pipelines

Options:
    -h, --help    Show this help message

Examples:
    dreamlake upload --path ./videos --endpoint bss://localhost:3112
    dreamlake list --endpoint bss://localhost:3112
    dreamlake status --endpoint bss://localhost:3112
    dreamlake pipeline list
    dreamlake pipeline run --id pipeline:example-timestamp video.mp4

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


def run_pipeline(args: list) -> int:
    """Run the pipeline command."""
    if "--help" in args or "-h" in args:
        PipelineCommand.print_help()
        return 0

    PipelineCommand._parse_args(args)
    return PipelineCommand.run()


__all__ = [
    "main",
    "UploadCommand",
    "ListCommand",
    "StatusCommand",
    "PipelineCommand",
]
