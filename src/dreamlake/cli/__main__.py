"""``python -m dreamlake.cli`` entry point.

The migration notice in ``_notice.py`` is gated on ``argv[0]``, so this
invocation stays silent — only the ``dreamlake`` console script prints
the TS-CLI pointer.
"""

import sys

from dreamlake.cli import main

if __name__ == "__main__":
    sys.exit(main())
