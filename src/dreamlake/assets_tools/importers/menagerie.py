"""mujoco_menagerie checkout -> clean DreamLake asset SOURCE dir.

Importers emit source + ``dreamlake.yml``, nothing generated: the
output is the asset files tree plus one ``dreamlake.yml`` at its root.
All generated artifacts (wire manifest, thumbnails, vectors) are
produced at push time by the dreamlake CLI into a cache dir -- nothing
of that lands in the tree. Menagerie's shipped preview PNGs stay where
they live inside each model dir (they are source files) but are no
longer copied or re-encoded into a ``thumbnails/`` dir; thumbnails are
render-time now (``python -m dreamlake.assets_tools.render_thumbnails``).

One asset per model directory (a top-level dir containing ``*.xml``):

* ``id`` = the directory name; ``title`` from the README H1 (else the
  prettified dir name); ``license`` sniffed from the dir's LICENSE;
* ``entryPoints`` = every top-level compilable-looking XML stem, kind
  ``scene`` when the stem starts with ``scene`` else ``robot``;
  ``entry`` = the first scene entry point, else the first XML;
* every file in the dir (recursive) is copied (or hardlinked) to the
  same relative path under the output root;
* ``category`` comes from the checkout's ``catalog.py`` (the gallery's
  MODEL_MAP) when present, hoisted into ``defaults`` when uniform.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

from ..dreamlake_yml import hoist_defaults, prune, write_dreamlake_yml
from ._common import (
    git_head,
    iter_asset_files,
    looks_compilable,
    place_file,
    prettify,
    readme_title,
    sanitize_id,
    sniff_license,
)

REPO_URL = "https://github.com/google-deepmind/mujoco_menagerie"

LIBRARY = {
    "title": "MuJoCo Menagerie",
    "description": (
        "High-quality MJCF robot models curated by Google DeepMind. "
        "Licenses vary per model; see each asset's license field."
    ),
    "provider": "Google DeepMind",
    "homepage": REPO_URL,
    "tags": ["robots", "mujoco"],
}


def _load_catalog(src: Path) -> dict[str, str]:
    """'<dir>/<stem>' -> category string, from ``<src>/catalog.py``.

    The Menagerie checkout ships ``catalog.py`` with ``MODEL_MAP``
    (robot -> ModelType enum); category strings are the enum names
    lowercased (``END_EFFECTOR`` -> ``end_effector``). Missing or
    unloadable catalog degrades to ``{}``.
    """
    catalog_py = src / "catalog.py"
    if not catalog_py.exists():
        return {}
    try:
        spec = importlib.util.spec_from_file_location(
            "_menagerie_catalog", catalog_py)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return {
            robot: model_type.name.lower()
            for robot, model_type in module.MODEL_MAP.items()
        }
    except Exception:
        return {}


def build_library(
    src: str | Path,
    out: str | Path,
    *,
    subset: list[str] | None = None,
    link: bool = False,
) -> dict:
    """Build the source dir at ``out`` from a Menagerie checkout.

    Returns the ``dreamlake.yml`` document (also written to
    ``<out>/dreamlake.yml``).
    """
    src = Path(src)
    out = Path(out)
    if not src.is_dir():
        raise FileNotFoundError(f"not a directory: {src}")

    model_dirs = [
        d for d in sorted(src.iterdir())
        if d.is_dir() and not d.name.startswith(".")
        and any(d.glob("*.xml"))  # non-model dirs have no top-level XML
    ]
    if subset is not None:
        known = {d.name for d in model_dirs}
        missing = sorted(set(subset) - known)
        if missing:
            raise FileNotFoundError(
                f"--subset names not found in {src}: {', '.join(missing)}")
        model_dirs = [d for d in model_dirs if d.name in subset]

    categories = _load_catalog(src)
    assets: dict[str, dict] = {}
    for model_dir in model_dirs:
        raw_name = model_dir.name
        model_id = sanitize_id(raw_name)
        if model_id is None or model_id in assets:
            print(f"skip {raw_name}: unusable or duplicate id",
                  file=sys.stderr)
            continue
        for rel in iter_asset_files(model_dir):
            place_file(model_dir / rel, out / model_id / rel, link=link)

        top_xmls = sorted(model_dir.glob("*.xml"))
        entry_points: dict[str, dict] = {}
        for xml in top_xmls:
            if not looks_compilable(xml):
                continue
            kind = "scene" if xml.stem.startswith("scene") else "robot"
            entry_points[xml.stem] = {
                "kind": kind, "file": f"{model_id}/{xml.name}"}
        entry = next(
            (ep["file"] for ep in entry_points.values()
             if ep["kind"] == "scene"),
            next(iter(entry_points.values()))["file"] if entry_points
            else f"{model_id}/{top_xmls[0].name}",
        )

        category = next(
            (categories[f"{model_id}/{stem}"] for stem in entry_points
             if f"{model_id}/{stem}" in categories),
            None,
        )

        assets[model_id] = prune({
            "title": readme_title(model_dir) or prettify(raw_name),
            "category": category,
            "license": sniff_license(model_dir),
            "entry": entry,
            "entryPoints": entry_points or None,
            "upstream": {"id": raw_name} if raw_name != model_id else None,
        })

    upstream = {"repo": REPO_URL}
    commit = git_head(src)
    if commit:
        upstream["commit"] = commit
    library = prune({**LIBRARY, "upstream": upstream})
    defaults = hoist_defaults(assets)
    doc = {"library": library}
    if defaults:
        doc["defaults"] = defaults
    doc["assets"] = assets
    write_dreamlake_yml(doc, out)
    return doc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m dreamlake.assets_tools.import_menagerie",
        description=(
            "Build a clean DreamLake asset source dir (asset files + "
            "dreamlake.yml, nothing generated) from a mujoco_menagerie "
            "checkout. One asset per model directory."
        ),
    )
    parser.add_argument("src", type=Path, help="mujoco_menagerie checkout")
    parser.add_argument("out", type=Path, help="output source directory")
    parser.add_argument(
        "--subset", metavar="NAME[,NAME...]",
        help="only these model directories (comma-separated)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--copy", dest="link", action="store_false", default=False,
        help="copy asset files into the output (default)")
    mode.add_argument(
        "--link", dest="link", action="store_true",
        help="hardlink asset files instead of copying")
    args = parser.parse_args(argv)

    subset = None
    if args.subset:
        subset = [s.strip() for s in args.subset.split(",") if s.strip()]
    try:
        doc = build_library(
            args.src, args.out, subset=subset, link=args.link)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    assets = doc.get("assets", {})
    print(f"wrote {args.out / 'dreamlake.yml'}: {len(assets)} assets")
    return 0 if assets else 1


if __name__ == "__main__":
    sys.exit(main())
