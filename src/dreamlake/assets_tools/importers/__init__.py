"""Importers: one module per upstream asset-repo shape.

Each importer exposes ``build_library(src, out, ...)`` returning the
written :class:`~dreamlake.assets_tools.manifest.LibraryManifest`, plus
an argparse ``main(argv)`` wired to a ``python -m
dreamlake.assets_tools.import_<name>`` entry point one level up.
"""
