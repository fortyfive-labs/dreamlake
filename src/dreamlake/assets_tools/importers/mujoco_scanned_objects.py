"""mujoco_scanned_objects checkout -> pushable DreamLake asset library.

The repo shape: ``<repo>/models/<Object_Name>/`` (1030 dirs), each with
``model.xml`` + ``model.obj`` + ``texture.png`` + ``model_collision_*.obj``.
One asset per dir: ``id`` = dir name, ``title`` = dir name with
underscores as spaces, ``entry`` = ``<dir>/model.xml``, category
``object``. The whole dataset is CC-BY-4.0, declared once at the
library level. No shipped previews exist, so thumbnails only appear
with ``--thumbnails`` (rendered offscreen to
``thumbnails/<id>.webp``; needs mujoco).

Scale: ~36k files stream through hashing without ever holding file
contents in memory.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..manifest import (
    Asset,
    LibraryInfo,
    LibraryManifest,
    file_entry,
    write_manifest,
)
from ..thumbnails import THUMBNAIL_MAX_DIM, render_thumbnail, view_angles
from ._common import git_head, iter_asset_files, place_file, sanitize_id

REPO_URL = "https://github.com/kevinzakka/mujoco_scanned_objects"

LIBRARY = {
    "name": "mujoco-scanned-objects",
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

#: escape hatch mirroring the menagerie importer: asset id -> extra
#: render_thumbnail kwargs when auto-framing misfires
PREVIEW_OVERRIDES: dict[str, dict] = {}


def build_library(
    src: str | Path,
    out: str | Path,
    *,
    subset: list[str] | None = None,
    thumbnails: bool = False,
    link: bool = False,
    size: int = THUMBNAIL_MAX_DIM,
) -> LibraryManifest:
    """Build the library directory at ``out`` from a GSO checkout.

    ``src`` may be the repo root (containing ``models/``) or the
    ``models/`` directory itself.
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

    azimuth, elevation = view_angles("object")
    assets: list[Asset] = []
    seen_ids: set[str] = set()
    skipped = 0
    for model_dir in model_dirs:
        raw_name = model_dir.name
        # ids must be ASCII-safe (two GSO dirs are named Pokémon_*);
        # the library-relative paths use the sanitized id too, and the
        # original name is preserved in upstream.id
        model_id = sanitize_id(raw_name)
        if model_id is None or model_id in seen_ids:
            print(f"skip {raw_name}: unusable or duplicate id",
                  file=sys.stderr)
            skipped += 1
            continue
        if not (model_dir / "model.xml").exists():
            print(f"skip {raw_name}: no model.xml", file=sys.stderr)
            skipped += 1
            continue
        seen_ids.add(model_id)
        rel_files = list(iter_asset_files(model_dir))
        for rel in rel_files:
            place_file(model_dir / rel, out / model_id / rel, link=link)
        files = [
            file_entry(out, Path(model_id) / rel) for rel in rel_files
        ]

        entry = f"{model_id}/model.xml"
        thumb_rel = f"thumbnails/{model_id}.webp"
        have_thumb = False
        if thumbnails:
            kwargs = {"azimuth": azimuth, "elevation": elevation}
            kwargs.update(PREVIEW_OVERRIDES.get(model_id, {}))
            have_thumb = render_thumbnail(
                out / entry, out / thumb_rel, size=size, **kwargs)
        if have_thumb:
            files.append(file_entry(out, thumb_rel))

        assets.append(Asset(
            id=model_id,
            title=raw_name.replace("_", " "),
            category="object",
            kind="mjcf",
            format="mjcf",
            upstream={"id": raw_name} if raw_name != model_id else None,
            entry=entry,
            files=files,
            thumbnail=thumb_rel if have_thumb else None,
        ))

    upstream = {"repo": REPO_URL}
    commit = git_head(src)
    if commit:
        upstream["commit"] = commit
    manifest = LibraryManifest(
        library=LibraryInfo(upstream=upstream, **LIBRARY),
        assets=assets,
    )
    write_manifest(manifest, out)
    if skipped:
        print(f"skipped {skipped} dirs without model.xml", file=sys.stderr)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=(
            "python -m dreamlake.assets_tools.import_mujoco_scanned_objects"
        ),
        description=(
            "Build a pushable DreamLake asset library (assets.json + "
            "asset files + thumbnails/) from a mujoco_scanned_objects "
            "checkout. One asset per models/<dir>."
        ),
    )
    parser.add_argument(
        "src", type=Path,
        help="mujoco_scanned_objects checkout (or its models/ dir)")
    parser.add_argument("out", type=Path, help="output library directory")
    parser.add_argument(
        "--subset", metavar="NAME[,NAME...]",
        help="only these model directories (comma-separated)")
    parser.add_argument(
        "--thumbnails", action="store_true",
        help="render thumbnails (needs mujoco; GSO ships no previews)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--copy", dest="link", action="store_false", default=False,
        help="copy asset files into the library (default)")
    mode.add_argument(
        "--link", dest="link", action="store_true",
        help="hardlink asset files instead of copying")
    parser.add_argument(
        "--size", type=int, default=THUMBNAIL_MAX_DIM,
        help="thumbnail max dimension in pixels (WebP output, rendered "
             f"at 2x and downscaled; default: {THUMBNAIL_MAX_DIM})")
    args = parser.parse_args(argv)

    subset = None
    if args.subset:
        subset = [s.strip() for s in args.subset.split(",") if s.strip()]
    try:
        manifest = build_library(
            args.src, args.out, subset=subset,
            thumbnails=args.thumbnails, link=args.link, size=args.size,
        )
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    n_files = sum(len(a.files) for a in manifest.assets)
    n_thumbs = sum(1 for a in manifest.assets if a.thumbnail)
    print(
        f"wrote {args.out / 'assets.json'}: {len(manifest.assets)} assets, "
        f"{n_files} file entries, {n_thumbs} thumbnails")
    return 0 if manifest.assets else 1


if __name__ == "__main__":
    sys.exit(main())
