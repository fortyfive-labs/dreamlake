"""Importers: one module per upstream asset-repo shape.

Each importer exposes ``build_library(src, out, ...)`` writing a clean
source dir (asset files + ``dreamlake.yml``, nothing generated) and
returning the yml document as a dict, plus an argparse ``main(argv)``
wired to a ``python -m dreamlake.assets_tools.import_<name>`` entry
point one level up.
"""
