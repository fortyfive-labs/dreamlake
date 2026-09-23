"""``python -m dreamlake.assets_tools.import_menagerie <src> <out>``.

Builds a pushable DreamLake asset library from a mujoco_menagerie
checkout; see :mod:`dreamlake.assets_tools.importers.menagerie`.
Options: ``--subset name1,name2``, ``--thumbnails``, ``--copy``/
``--link`` (default copy), ``--size``.
"""

from __future__ import annotations

import sys

from .importers.menagerie import main

if __name__ == "__main__":
    sys.exit(main())
