"""
Pipeline command for dreamlake CLI.

Run registered pipelines for video processing.

Usage:
    dreamlake pipeline list
    dreamlake pipeline run --id pipeline:example-timestamp video.mp4
"""

import importlib
import sys
from pathlib import Path
from typing import Optional

from params_proto import proto


# Registry of available pipelines
PIPELINES = {
    "pipeline:example-timestamp": "dreamlake.pipelines.example_timestamp",
}


@proto
class PipelineCommand:
    """
    Run video processing pipelines.

    Pipelines are modular processing units that can be chained together.
    Each pipeline is identified by a unique ID in the format pipeline:<name>.
    """

    action: str = "list"
    """Action: list, run, info"""

    id: Optional[str] = None
    """Pipeline ID (e.g., pipeline:example-timestamp)"""

    video: Optional[str] = None
    """Path to video file (for run action)"""

    output: Optional[str] = None
    """Output file path"""

    format: str = "tsv"
    """Output format: tsv, jsonl, srt, vtt"""

    interval: float = 1.0
    """Interval between samples in seconds"""

    dry_run: bool = False
    """Show what would be done without executing"""

    verbose: bool = False
    """Show verbose output"""

    @classmethod
    def print_help(cls):
        """Print help message for pipeline command."""
        help_text = """
dreamlake pipeline - Run video processing pipelines

Usage:
    dreamlake pipeline list
    dreamlake pipeline run --id <pipeline-id> <video> [options]
    dreamlake pipeline info --id <pipeline-id>

Actions:
    list               List available pipelines
    run                Run a pipeline on a video
    info               Show pipeline details

Options:
    --id PIPELINE_ID   Pipeline identifier (e.g., pipeline:example-timestamp)
    --output FILE      Output file path
    --format FORMAT    Output format: tsv, jsonl, srt, vtt (default: tsv)
    --interval SECS    Sample interval in seconds (default: 1.0)
    --dry-run          Show what would be done
    --verbose          Show verbose output
    -h, --help         Show this help message

Available Pipelines:
    pipeline:example-timestamp   Generate timestamp labels for video frames

Examples:
    dreamlake pipeline list
    dreamlake pipeline run --id pipeline:example-timestamp video.mp4
    dreamlake pipeline run --id pipeline:example-timestamp video.mp4 --interval 0.5
"""
        print(help_text)

    @classmethod
    def _parse_args(cls, args: list):
        """Parse command line arguments."""
        i = 0

        # First non-flag argument is the action
        if args and not args[0].startswith("-"):
            cls.action = args[0]
            i = 1

        while i < len(args):
            arg = args[i]
            if arg.startswith("--"):
                if arg == "--id" and i + 1 < len(args):
                    cls.id = args[i + 1]
                    i += 2
                elif arg == "--output" and i + 1 < len(args):
                    cls.output = args[i + 1]
                    i += 2
                elif arg == "--format" and i + 1 < len(args):
                    cls.format = args[i + 1]
                    i += 2
                elif arg == "--interval" and i + 1 < len(args):
                    cls.interval = float(args[i + 1])
                    i += 2
                elif arg == "--dry-run":
                    cls.dry_run = True
                    i += 1
                elif arg == "--verbose":
                    cls.verbose = True
                    i += 1
                else:
                    i += 1
            elif not arg.startswith("-") and cls.video is None and cls.action == "run":
                cls.video = arg
                i += 1
            else:
                i += 1

    @classmethod
    def _list_pipelines(cls) -> int:
        """List available pipelines."""
        print("Available pipelines:\n")
        for pipeline_id, module_path in PIPELINES.items():
            print(f"  {pipeline_id}")
            try:
                module = importlib.import_module(module_path)
                if hasattr(module, "DESCRIPTION"):
                    print(f"    {module.DESCRIPTION}")
            except ImportError:
                print(f"    (module not found: {module_path})")
            print()
        return 0

    @classmethod
    def _run_pipeline(cls) -> int:
        """Run a pipeline."""
        if not cls.id:
            print("Error: --id required for run action")
            return 1

        if cls.id not in PIPELINES:
            print(f"Error: Unknown pipeline: {cls.id}")
            print(f"Available: {', '.join(PIPELINES.keys())}")
            return 1

        if not cls.video:
            print("Error: video path required for run action")
            return 1

        video_path = Path(cls.video)
        if not video_path.exists():
            print(f"Error: Video not found: {video_path}")
            return 1

        module_path = PIPELINES[cls.id]

        try:
            module = importlib.import_module(module_path)
        except ImportError as e:
            print(f"Error: Could not load pipeline module: {e}")
            return 1

        if not hasattr(module, "run"):
            print(f"Error: Pipeline module missing 'run' function")
            return 1

        # Pass options to pipeline
        options = {
            "video": str(video_path),
            "output": cls.output,
            "format": cls.format,
            "interval": cls.interval,
            "dry_run": cls.dry_run,
            "verbose": cls.verbose,
        }

        if cls.verbose:
            print(f"Running pipeline: {cls.id}")
            print(f"  Video: {video_path}")
            print(f"  Options: {options}")

        return module.run(**options)

    @classmethod
    def _info_pipeline(cls) -> int:
        """Show pipeline info."""
        if not cls.id:
            print("Error: --id required for info action")
            return 1

        if cls.id not in PIPELINES:
            print(f"Error: Unknown pipeline: {cls.id}")
            return 1

        module_path = PIPELINES[cls.id]
        print(f"Pipeline: {cls.id}")
        print(f"Module: {module_path}")

        try:
            module = importlib.import_module(module_path)
            if hasattr(module, "DESCRIPTION"):
                print(f"Description: {module.DESCRIPTION}")
            if hasattr(module, "OPTIONS"):
                print(f"Options: {module.OPTIONS}")
        except ImportError as e:
            print(f"Error loading module: {e}")

        return 0

    @classmethod
    def run(cls) -> int:
        """Execute the pipeline command."""
        if cls.action == "list":
            return cls._list_pipelines()
        elif cls.action == "run":
            return cls._run_pipeline()
        elif cls.action == "info":
            return cls._info_pipeline()
        else:
            print(f"Unknown action: {cls.action}")
            cls.print_help()
            return 1
