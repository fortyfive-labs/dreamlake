"""Offline tooling for DreamLake Asset Libraries (``dreamlake.assets/v1``).

An asset library is a directory of asset files plus an ``assets.json``
manifest at its root, pushed to DreamLake verbatim by the CLI. This
package GENERATES such directories from open-source asset repos: it
walks model directories, computes per-file sha256, renders thumbnails,
and writes the manifest.

Layout:

* :mod:`~dreamlake.assets_tools.manifest` -- the ``dreamlake.assets/v1``
  schema as dataclasses, strict validation, deterministic read/write.
* :mod:`~dreamlake.assets_tools.thumbnails` -- offscreen MJCF thumbnail
  rendering (auto-framed, transparent background). Needs ``mujoco``
  (``pip install 'dreamlake[compose]'``); degrades to a warning + skip
  without it.
* :mod:`~dreamlake.assets_tools.importers` -- one module per upstream
  repo shape (``menagerie``, ``mujoco_scanned_objects``), each exposing
  ``build_library(src, out, ...)`` plus an argparse ``main``.
* ``import_menagerie`` / ``import_mujoco_scanned_objects`` -- the
  ``python -m dreamlake.assets_tools.import_*`` entry points.
* :mod:`~dreamlake.assets_tools.embed` -- the CLIP embeddings sidecar
  (``assets.vectors.json`` + ``assets.vectors.f32``) generated from the
  manifest's thumbnails and text metadata. Needs ``torch``/``open_clip``
  (``pip install 'dreamlake[embed]'``), imported lazily on first cache
  miss; ``python -m dreamlake.assets_tools.embed <lib-dir>``.
"""

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
from .thumbnails import mujoco_available, render_thumbnail

__all__ = [
    "SCHEMA",
    "Asset",
    "AssetFile",
    "LibraryInfo",
    "LibraryManifest",
    "ManifestError",
    "file_entry",
    "hash_file",
    "load_manifest",
    "mujoco_available",
    "render_thumbnail",
    "validate",
    "write_manifest",
]
