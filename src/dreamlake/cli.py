"""
DreamLake CLI - Command line interface for experiment data management.

This module re-exports from dreamlake.cli for backwards compatibility.
The actual implementation is in dreamlake/cli/__init__.py and dreamlake/cli/commands/.
"""

# Re-export main entry point
from .cli import main

# Re-export config classes for programmatic use
from .cli.commands.video import VideoUploadConfig, VideoDownloadConfig, VideoListConfig

__all__ = [
    "main",
    "VideoUploadConfig",
    "VideoDownloadConfig",
    "VideoListConfig",
]

if __name__ == "__main__":
    import sys
    sys.exit(main())
