"""mujoco_scanned_objects checkout -> clean DreamLake asset SOURCE dir.

Emits source + ``dreamlake.yml``, nothing generated (see the menagerie
importer for the model: manifests, thumbnails, and vectors are all
push-time CLI artifacts now).

The repo shape: ``<repo>/models/<Object_Name>/`` (1030 dirs), each with
``model.xml`` + ``model.obj`` + ``texture.png`` + ``model_collision_*.obj``.
One asset per dir: ``id`` = dir name, ``title`` = dir name with
underscores as spaces, ``entry`` = ``<dir>/model.xml``, category
``object`` (uniform, so it lands in ``defaults``). The whole dataset is
CC-BY-4.0, declared once at the library level.

Scale: ~36k files stream through without ever holding file contents in
memory.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..dreamlake_yml import hoist_defaults, prune, write_dreamlake_yml
from ._common import git_head, iter_asset_files, place_file, sanitize_id

REPO_URL = "https://github.com/kevinzakka/mujoco_scanned_objects"

LIBRARY = {
    "title": "MuJoCo Scanned Objects",
    "description": (
        "MJCF models for the 1030 household objects of the Google "
        "Scanned Objects dataset, converted with obj2mjcf."
    ),
    "provider": "Google Scanned Objects",
    "homepage": REPO_URL,
    "license": "CC-BY-4.0",
    "tags": ["objects", "mujoco"],
}


def build_library(
    src: str | Path,
    out: str | Path,
    *,
    subset: list[str] | None = None,
    link: bool = False,
) -> dict:
    """Build the source dir at ``out`` from a GSO checkout.

    ``src`` may be the repo root (containing ``models/``) or the
    ``models/`` directory itself. Returns the ``dreamlake.yml``
    document (also written to ``<out>/dreamlake.yml``).
    """
    src = Path(src)
    out = Path(out)
    models = src / "models" if (src / "models").is_dir() else src
    if not models.is_dir():
        raise FileNotFoundError(f"not a directory: {models}")

    model_dirs = [
        d for d in sorted(models.iterdir())
        if d.is_dir() and not d.name.startswith(".")
    ]
    if subset is not None:
        known = {d.name for d in model_dirs}
        missing = sorted(set(subset) - known)
        if missing:
            raise FileNotFoundError(
                f"--subset names not found in {models}: "
                f"{', '.join(missing)}")
        model_dirs = [d for d in model_dirs if d.name in subset]

    assets: dict[str, dict] = {}
    skipped = 0
    for model_dir in model_dirs:
        raw_name = model_dir.name
        # ids must be ASCII-safe (two GSO dirs are named Pokémon_*);
        # the output-relative paths use the sanitized id too, and the
        # original name is preserved in upstream.id
        model_id = sanitize_id(raw_name)
        if model_id is None or model_id in assets:
            print(f"skip {raw_name}: unusable or duplicate id",
                  file=sys.stderr)
            skipped += 1
            continue
        if not (model_dir / "model.xml").exists():
            print(f"skip {raw_name}: no model.xml", file=sys.stderr)
            skipped += 1
            continue
        for rel in iter_asset_files(model_dir):
            place_file(model_dir / rel, out / model_id / rel, link=link)

        assets[model_id] = prune({
            "title": raw_name.replace("_", " "),
            "category": "object",
            "entry": f"{model_id}/model.xml",
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
    if skipped:
        print(f"skipped {skipped} dirs", file=sys.stderr)
    return doc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=(
            "python -m dreamlake.assets_tools.import_mujoco_scanned_objects"
        ),
        description=(
            "Build a clean DreamLake asset source dir (asset files + "
            "dreamlake.yml, nothing generated) from a "
            "mujoco_scanned_objects checkout. One asset per "
            "models/<dir>."
        ),
    )
    parser.add_argument(
        "src", type=Path,
        help="mujoco_scanned_objects checkout (or its models/ dir)")
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
