"""``python -m dreamlake.assets_tools.import_menagerie <src> <out>``.

Builds a clean DreamLake asset source dir (asset files +
``dreamlake.yml``, nothing generated) from a mujoco_menagerie
checkout; see :mod:`dreamlake.assets_tools.importers.menagerie`.
Options: ``--subset name1,name2``, ``--copy``/``--link`` (default
copy).
"""

from __future__ import annotations

import sys

from .importers.menagerie import main

if __name__ == "__main__":
    sys.exit(main())
