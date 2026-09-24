"""mujoco_menagerie checkout -> pushable DreamLake asset library.

One asset per model directory (a top-level dir containing ``*.xml``):

* ``id`` = the directory name; ``title`` from the README H1 (else the
  prettified dir name); ``license`` sniffed from the dir's LICENSE;
* ``entryPoints`` = every top-level compilable-looking XML stem, kind
  ``scene`` when the stem starts with ``scene`` else ``robot``;
  ``entry`` = the first scene entry point, else the first XML;
* ``files`` = every file in the dir (recursive), copied (or hardlinked)
  to the same relative path under the output library root;
* thumbnail: the model's shipped preview PNG when one exists
  (re-encoded through the shared downscale+WebP writer -- Menagerie
  ships large PNGs), else -- with ``--thumbnails`` -- rendered
  offscreen via :mod:`~dreamlake.assets_tools.thumbnails`; either way
  it lands at ``thumbnails/<id>.webp`` and is listed in the asset's
  files with the WebP's real size/sha256.

When the checkout carries a ``catalog.py`` (the gallery's MODEL_MAP),
categories and per-category view angles come from it; otherwise both
degrade gracefully (no category, default angles).
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

from PIL import Image

from ..manifest import (
    Asset,
    LibraryInfo,
    LibraryManifest,
    file_entry,
    write_manifest,
)
from ..thumbnails import (
    THUMBNAIL_MAX_DIM,
    render_thumbnail,
    save_thumbnail,
    view_angles,
)
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
    "name": "mujoco-menagerie",
    "title": "MuJoCo Menagerie",
    "description": (
        "High-quality MJCF robot models curated by Google DeepMind. "
        "Licenses vary per model; see each asset's license field."
    ),
    "provider": "Google DeepMind",
    "homepage": REPO_URL,
    "tags": ["robots", "mujoco"],
}

# Gallery poses, ported from generate_gallery.py KEYFRAME_MAP. Keyed by
# '<model_dir>/<xml stem>'; injected as the `gallery_thumbnail`
# keyframe when rendering that XML.
# fmt: off
KEYFRAME_MAP = {
    "pal_talos/talos": (
        "0 0 1.025 0 0 0 0 0 0.15 0 0 0.3 0.4 -0.5 -1.5 0 0 0 0 -0.4 0 0 0 0 0"
        " -0.3 -0.4 0.5 -1.5 0 0 0 0 -0.4 0 0 0 0 0 0 0 -0.4 0.8 -0.4 0 0 0"
        " -0.4 0.8 -0.4 0"
    ),
    "robotis_op3/op3": (
        "0 0 0.2789 1 0 0 0 0.0 0.0 -0.0890 0.7931 -0.79 0.0874 -0.7946 0.7855"
        " -0.0015 -0.0460 -0.1626 0.2316 0.1565 -0.0230 0.0 0.0445 0.1611"
        " -0.2332 -0.1580 0.0215"
    ),
    "google_barkour_vb/barkour_vb": (
        "0 0 0.21 1 0 0 0 0 0.5 1.0 0 0.5 1.0 0 0.5 1.0 0 0.5 1.0"
    ),
    "hello_robot_stretch/stretch": (
        "0 0 0 1 0 0 0 0 0 0.1325 0.07995 0.07995 0.07605 0.0702 1.585 0 0.198"
        " 0 0 0.126 0 0 0 0"
    ),
    "google_robot/robot": (
        "-1.51699e-13 -1.16232e-12 -0.1444 2.9724 -0.146 -0.3759 1.15806e-12"
        " 0.5518 0.62275"
    ),
    "aloha/aloha": (
        "0.43988 -0.206468 1.08253 -0.443382 -1.084 -0.00397598 0.0084"
        " 0.00846495 -1.28822 -0.360594 0.717978 -0.000325086 -0.273415"
        " 6.76003e-05 0.0084 0.00839987"
    ),
    "kuka_iiwa_14/iiwa14": "0 0 0 -1.5708 0 1.5708 0",
    "flexiv_rizon4/flexiv_rizon4": "0 -0.524 0 1.833 0 0.785 0",
    "franka_emika_panda/hand": "0.04 0.04",
}
# fmt: on

#: xml stems whose authored lights survive (everyone else renders with
#: the headlight only) -- ported from the gallery's KEEP_LIGHT
KEEP_LIGHT = {"go1", "a1", "op3", "aloha", "left_hand", "stretch", "piper"}

#: per-robot (azimuth, elevation), overriding the category default;
#: keyed like KEYFRAME_MAP
VIEW_ANGLE_OVERRIDE = {
    # Default biomechanical angle catches MS-Human-700 from the side;
    # nudge to a near-frontal 3/4.
    "ms_human_700/MS-Human-700": (20, 15),
}


def _spread_aloha(spec) -> None:
    """The gallery's aloha tweak: spread the two arms apart."""
    try:
        spec.body("right/base_link").pos[0] = 0.3
        spec.body("left/base_link").pos[0] = -0.3
    except Exception:  # model variant without those bodies
        pass


#: Per-asset escape hatch when auto-framing produces a bad thumbnail.
#: Keyed by asset id (the model dir name); values are extra kwargs for
#: :func:`~dreamlake.assets_tools.thumbnails.render_thumbnail`
#: (``azimuth``/``elevation``, an explicit ``camera`` dict, ``qpos``,
#: ``keep_lights``, ``spec_hook``) plus an optional ``xml`` naming the
#: top-level XML to render instead of the default pick.
PREVIEW_OVERRIDES: dict[str, dict] = {
    "aloha": {"spec_hook": _spread_aloha},
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


def _existing_preview(
    model_dir: Path, ep_stems: list[str]) -> Path | None:
    """The model's shipped preview PNG, if identifiable.

    Preference order: ``<dir>.png``; a PNG whose stem is a suffix of
    the dir name or vice versa (``agility_cassie`` -> ``cassie.png``);
    a PNG named after an entry-point stem; the single top-level PNG.
    """
    name = model_dir.name
    pngs = sorted(model_dir.glob("*.png"))
    if not pngs:
        return None
    for png in pngs:
        if png.stem == name:
            return png
    for png in pngs:
        if name.endswith(png.stem) or png.stem.endswith(name):
            return png
    for png in pngs:
        if png.stem in ep_stems:
            return png
    if len(pngs) == 1:
        return pngs[0]
    return None


def _thumbnail_xml(model_id: str, entry_points: dict[str, dict],
                   entry: str) -> str:
    """Library-relative XML to render: first robot entry point, else entry.

    Robot XMLs render on the white skybox alone; scene XMLs drag their
    floor plane into the AABB, so they are the last resort.
    """
    override = PREVIEW_OVERRIDES.get(model_id, {})
    if "xml" in override:
        return f"{model_id}/{override['xml']}"
    for ep in entry_points.values():
        if ep["kind"] == "robot":
            return ep["file"]
    return entry


def _render_kwargs(model_id: str, xml_rel: str, category: str | None) -> dict:
    stem = Path(xml_rel).stem
    robot = f"{model_id}/{stem}"
    azimuth, elevation = VIEW_ANGLE_OVERRIDE.get(
        robot, view_angles(category))
    kwargs: dict = {"azimuth": azimuth, "elevation": elevation}
    if robot in KEYFRAME_MAP:
        kwargs["qpos"] = KEYFRAME_MAP[robot]
    kwargs["keep_lights"] = stem in KEEP_LIGHT
    override = dict(PREVIEW_OVERRIDES.get(model_id, {}))
    override.pop("xml", None)
    kwargs.update(override)
    return kwargs


def build_library(
    src: str | Path,
    out: str | Path,
    *,
    subset: list[str] | None = None,
    thumbnails: bool = False,
    link: bool = False,
    size: int = THUMBNAIL_MAX_DIM,
) -> LibraryManifest:
    """Build the library directory at ``out`` from a Menagerie checkout."""
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
    assets: list[Asset] = []
    seen_ids: set[str] = set()
    for model_dir in model_dirs:
        raw_name = model_dir.name
        model_id = sanitize_id(raw_name)
        if model_id is None or model_id in seen_ids:
            print(f"skip {raw_name}: unusable or duplicate id",
                  file=sys.stderr)
            continue
        seen_ids.add(model_id)
        rel_files = list(iter_asset_files(model_dir))
        for rel in rel_files:
            place_file(model_dir / rel, out / model_id / rel, link=link)
        files = [
            file_entry(out, Path(model_id) / rel) for rel in rel_files
        ]

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

        thumb_rel = f"thumbnails/{model_id}.webp"
        have_thumb = False
        shipped = _existing_preview(model_dir, list(entry_points))
        if shipped is not None:
            # never copied verbatim: upstream previews are large PNGs,
            # so they go through the same downscale+WebP writer as
            # rendered thumbnails
            try:
                with Image.open(shipped) as img:
                    save_thumbnail(
                        img.convert("RGBA"), out / thumb_rel, max_dim=size)
                have_thumb = True
            except Exception as e:  # unreadable preview: fall through
                print(
                    f"bad shipped preview {shipped}: "
                    f"{type(e).__name__}: {e}", file=sys.stderr)
        if not have_thumb and thumbnails:
            xml_rel = _thumbnail_xml(model_id, entry_points, entry)
            have_thumb = render_thumbnail(
                out / xml_rel,
                out / thumb_rel,
                size=size,
                **_render_kwargs(model_id, xml_rel, category),
            )
        if have_thumb:
            files.append(file_entry(out, thumb_rel))

        assets.append(Asset(
            id=model_id,
            title=readme_title(model_dir) or prettify(raw_name),
            category=category,
            kind="mjcf",
            format="mjcf",
            license=sniff_license(model_dir),
            upstream={"id": raw_name} if raw_name != model_id else None,
            entry=entry,
            entry_points=entry_points or None,
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
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m dreamlake.assets_tools.import_menagerie",
        description=(
            "Build a pushable DreamLake asset library (assets.json + "
            "asset files + thumbnails/) from a mujoco_menagerie "
            "checkout. One asset per model directory."
        ),
    )
    parser.add_argument("src", type=Path, help="mujoco_menagerie checkout")
    parser.add_argument("out", type=Path, help="output library directory")
    parser.add_argument(
        "--subset", metavar="NAME[,NAME...]",
        help="only these model directories (comma-separated)")
    parser.add_argument(
        "--thumbnails", action="store_true",
        help="render thumbnails for models without a shipped preview "
             "(needs mujoco)")
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
