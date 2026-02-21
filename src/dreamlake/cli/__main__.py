"""
Entry point for running dreamlake CLI as a module.

Usage:
    python -m dreamlake.cli upload --path ./videos
    python -m dreamlake.cli list
    python -m dreamlake.cli status
"""

import sys
from . import main

if __name__ == "__main__":
    sys.exit(main())
