"""Offline tooling for DreamLake Asset Libraries (``dreamlake.assets/v1``).

The authoring model is split: users keep a clean SOURCE dir (asset
files + one optional ``dreamlake.yml``); ALL generated artifacts --
the wire manifest, thumbnails, vectors -- are produced at push time by
the dreamlake CLI into a cache dir and uploaded under the reserved
``files/.dreamlake/`` prefix. The CLI shells out to the entry points
in this package for the python-side steps (thumbnail rendering, CLIP
embeddings).

Layout:

* :mod:`~dreamlake.assets_tools.manifest` -- the ``dreamlake.assets/v1``
  WIRE schema as dataclasses, strict validation, deterministic
  read/write. The CLI builds this at push time; importers no longer
  write it into source dirs.
* :mod:`~dreamlake.assets_tools.dreamlake_yml` -- deterministic
  ``dreamlake.yml`` emission (tiny built-in YAML writer; pyyaml is not
  a base dependency).
* :mod:`~dreamlake.assets_tools.thumbnails` -- offscreen MJCF thumbnail
  rendering (auto-framed, transparent background, supersampled 2x and
  downscaled to 640px lossy WebP via the shared ``save_thumbnail``).
  Rendering needs ``mujoco`` (``pip install 'dreamlake[compose]'``);
  degrades to a warning + skip without it.
* :mod:`~dreamlake.assets_tools.render_thumbnails` -- the CLI's batch
  entry point: ``python -m dreamlake.assets_tools.render_thumbnails
  --jobs jobs.json --out-dir DIR``; one JSON report line on stdout.
* :mod:`~dreamlake.assets_tools.importers` -- one module per upstream
  repo shape (``menagerie``, ``mujoco_scanned_objects``), each writing
  source files + ``dreamlake.yml`` (nothing generated) via
  ``build_library(src, out, ...)`` plus an argparse ``main``.
* ``import_menagerie`` / ``import_mujoco_scanned_objects`` -- the
  ``python -m dreamlake.assets_tools.import_*`` entry points.
* :mod:`~dreamlake.assets_tools.embed` -- the CLIP embeddings sidecar
  (``dreamlake.assets.vectors/v1``). Manifest mode (the CLI step)
  writes ``vectors.json`` + ``vectors.f32`` into ``--out-dir``; legacy
  in-dir mode writes ``assets.vectors.*`` next to ``assets.json``.
  Needs ``torch``/``open_clip`` (``pip install 'dreamlake[embed]'``),
  imported lazily on first cache miss.
"""

from .dreamlake_yml import dump_yaml, write_dreamlake_yml
from .manifest import (
    SCHEMA,
    Asset,
    AssetFile,
    LibraryInfo,
    LibraryManifest,
    ManifestError,
    file_entry,
    hash_file,
    load_manifest,
    validate,
    write_manifest,
)
from .thumbnails import mujoco_available, render_thumbnail, save_thumbnail

__all__ = [
    "SCHEMA",
    "Asset",
    "AssetFile",
    "LibraryInfo",
    "LibraryManifest",
    "ManifestError",
    "dump_yaml",
    "file_entry",
    "hash_file",
    "load_manifest",
    "mujoco_available",
    "render_thumbnail",
    "save_thumbnail",
    "validate",
    "write_dreamlake_yml",
    "write_manifest",
]
