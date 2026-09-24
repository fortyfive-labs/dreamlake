"""``python -m dreamlake.assets_tools.import_mujoco_scanned_objects``.

Builds a clean DreamLake asset source dir (asset files +
``dreamlake.yml``, nothing generated) from a mujoco_scanned_objects
checkout; see
:mod:`dreamlake.assets_tools.importers.mujoco_scanned_objects`.
Options: ``--subset name1,name2``, ``--copy``/``--link`` (default
copy).
"""

from __future__ import annotations

import sys

from .importers.mujoco_scanned_objects import main

if __name__ == "__main__":
    sys.exit(main())
