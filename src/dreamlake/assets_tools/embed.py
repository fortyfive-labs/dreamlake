"""Embedding sidecar for asset libraries (SigLIP2 by default).

Two modes, one wire format:

* **Manifest mode (the CLI's push-time step)** --
  ``python -m dreamlake.assets_tools.embed --manifest build/assets.json
  --files-root SRC --thumbs-dir THUMBS --out-dir OUT [--device cpu]``
  reads assets from a built wire manifest (which may live anywhere) and
  writes ``<out-dir>/vectors.json`` + ``<out-dir>/vectors.f32``. The
  image source per asset: a ``thumbnail`` under the reserved
  ``.dreamlake/thumbnails/`` prefix resolves to
  ``<thumbs-dir>/<id>.webp`` (the batch renderer's cache layout);
  anything else resolves under ``--files-root`` (a user-authored image
  in the source tree). stdout carries exactly one JSON line (the stats
  dict -- the contract the CLI parses); human chatter goes to stderr.

* **Legacy in-dir mode** --
  ``python -m dreamlake.assets_tools.embed <library-dir>`` reads
  ``<library-dir>/assets.json``, resolves thumbnails inside the library
  directory, and writes ``assets.vectors.json`` + ``assets.vectors.f32``
  next to it.

Both write the layout the server parser (``dreamlake-server``
``assetLibrarySearch.ts::parseVectorsSidecar``) expects:

* the ``.json`` -- ``{"schema":
  "dreamlake.assets.vectors/v1", "model": ..., "dim": ..., "items":
  [{"id", "image", "text"}]}`` where ``image``/``text`` are row indices
  into the matrix, or null when the asset has no thumbnail / no text;
* the ``.f32`` -- the matrix: concatenated little-endian
  float32 rows, each row L2-normalized, row *i* at byte ``i * dim * 4``.

The embedding model is a COMPATIBILITY CONTRACT with the query-time
encoder (dreamlake-server runs the ONNX text tower of the SAME
checkpoint in-process). Since the single-encoder cleanup there is
exactly ONE supported space:

* **SigLIP 2 base (default)** -- open_clip ``ViT-B-16-SigLIP2``/
  ``webli`` (768-dim, Gemma tokenizer, multilingual; server side:
  ``services/siglip2Text.ts``; needs open-clip-torch >= 2.31, pinned by
  the ``embed`` extra).

``--model`` stays a generic open_clip passthrough (this is a general
tool), and the ``clip`` alias survives as an EXPLICITLY-LEGACY escape
hatch (``ViT-L-14-quickgelu``/``openai`` -- the pre-SigLIP2 space). The
server no longer runs a CLIP query tower, so a clip-embedded sidecar --
like any non-default model -- gets keyword-only search on DreamLake.

Images go through the model's own val preprocess (PIL,
``convert("RGB")``), texts through the model tokenizer (for SigLIP2
that applies big_vision canonicalization -- the server query path
mirrors it). Change either side and image/text similarity turns to
garbage. The sidecar's ``model`` field carries
``open_clip/<model>/<pretrained>``; the server only fuses a library's
vectors when that string matches its ACTIVE query encoder (both spaces
are 768-dim, so the dim check alone cannot tell them apart), otherwise
that library degrades to keyword-only search until re-pushed.

Per-asset inputs: the image vector comes from the ``thumbnail`` file;
the text vector from ``"{title}. {description}. {tags joined by ', '}"``
(empty parts dropped, all-empty skipped).

Vectors are cached under ``~/.cache/dreamlake/assets-embed/`` keyed by
content (sha256 of the thumbnail bytes / of model id + text), so
re-running after a wholesale manifest regeneration re-encodes nothing
that did not change. ``torch``/``open_clip`` are imported lazily, only
on a cache miss: ``pip install "dreamlake[embed]"``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
import warnings
from pathlib import Path

import numpy as np

from .manifest import Asset, ManifestError, load_manifest

VECTORS_SCHEMA = "dreamlake.assets.vectors/v1"
#: legacy in-dir sidecar names, written next to assets.json
VECTORS_JSON = "assets.vectors.json"
VECTORS_F32 = "assets.vectors.f32"
#: manifest-mode output names, written into --out-dir (the CLI cache)
OUT_VECTORS_JSON = "vectors.json"
OUT_VECTORS_F32 = "vectors.f32"

#: manifest thumbnails under this prefix are CLI-generated: the bytes
#: live at <thumbs-dir>/<asset id>.webp, not under the files root
DREAMLAKE_THUMBS_PREFIX = ".dreamlake/thumbnails/"

#: MUST stay in the same embedding space as the server's query-time
#: encoder (dreamlake-server services/siglip2Text.ts: the fp16 ONNX
#: text tower of this exact checkpoint). Requires open-clip-torch >=
#: 2.31 (+ timm >= 1.0.15, transformers for the Gemma tokenizer) -- all
#: pinned by the ``embed`` extra.
SIGLIP2_MODEL = "ViT-B-16-SigLIP2"
SIGLIP2_PRETRAINED = "webli"
DEFAULT_MODEL = SIGLIP2_MODEL
DEFAULT_PRETRAINED = SIGLIP2_PRETRAINED

#: LEGACY: the pre-SigLIP2 space (CLIP ViT-L-14 QuickGELU, 768-dim like
#: SigLIP2 -- the sidecar ``model`` string is what keeps the two spaces
#: apart server-side). The server's CLIP query tower is GONE, so this
#: only makes sense for non-DreamLake uses of the tool; on DreamLake a
#: clip-embedded library degrades to keyword-only search. "-quickgelu"
#: is essential when using it: the plain "ViT-L-14" config runs the
#: OpenAI weights through nn.GELU, which lands in a measurably
#: different space (cos as low as 0.5 vs the true QuickGELU on long
#: texts).
LEGACY_CLIP_MODEL = "ViT-L-14-quickgelu"
LEGACY_CLIP_PRETRAINED = "openai"

#: accepted ``--model`` aliases -> the exact (model, pretrained) pair.
#: An alias PINS pretrained too: the contract is with a checkpoint, not
#: an architecture. "clip" is the explicitly-legacy escape hatch (see
#: LEGACY_CLIP_MODEL above).
MODEL_ALIASES: dict[str, tuple[str, str]] = {
    "clip": (LEGACY_CLIP_MODEL, LEGACY_CLIP_PRETRAINED),
    "siglip2": (SIGLIP2_MODEL, SIGLIP2_PRETRAINED),
}

DEFAULT_CACHE_DIR = Path("~/.cache/dreamlake/assets-embed")


def resolve_model(model: str, pretrained: str) -> tuple[str, str]:
    """Expand a model alias ("clip", "siglip2") to its pinned
    ``(model, pretrained)`` pair; explicit open_clip names pass through
    with the given pretrained tag untouched."""
    return MODEL_ALIASES.get(model.lower(), (model, pretrained))


def model_id(model: str = DEFAULT_MODEL,
             pretrained: str = DEFAULT_PRETRAINED) -> str:
    """The identifier written to the sidecar's ``model`` field.

    The server's fusion gate compares this string against its query
    encoder id, so it must stay ``open_clip/<model>/<pretrained>``
    verbatim -- the default resolves to
    ``open_clip/ViT-B-16-SigLIP2/webli``, the only id the server fuses
    (``open_clip/ViT-L-14-quickgelu/openai`` is the legacy CLIP-era
    string the gate now degrades to keyword-only).
    """
    model, pretrained = resolve_model(model, pretrained)
    return f"open_clip/{model}/{pretrained}"


def asset_text(asset: Asset) -> str:
    """``"{title}. {description}. {tags joined by ', '}"``, empties dropped.

    Returns ``""`` when the asset has no title, description, or tags --
    the caller then writes ``text: null`` for it.
    """
    parts = [
        asset.title or "",
        asset.description or "",
        ", ".join(asset.tags or []),
    ]
    return ". ".join(p for p in parts if p)


# ─── encoder (lazy torch/open_clip) ──────────────────────────────────


def _load_encoder(model: str, pretrained: str, device: str):
    """``(encode_image(path), encode_text(str))``, each -> 1-D float32.

    open_clip model + its own val preprocess + its tokenizer. Raw
    (un-normalized) features out; normalization happens once, at
    matrix-write time.

    ``ViT-B-16-SigLIP2`` (open_clip >= 2.31, hf-hub weights) matches
    the server's ONNX text tower (services/siglip2Text.ts), its
    tokenizer canonicalizing text the way the server's query path does.
    (For the legacy clip alias, the "-quickgelu" model name loads the
    openai checkpoint with its trained activation -- no "QuickGELU
    mismatch" warning.)
    """
    try:
        import open_clip
        import torch
    except ImportError as e:
        raise ImportError(
            "dreamlake.assets_tools.embed needs torch and open_clip. "
            'Install them with: pip install "dreamlake[embed]"'
        ) from e
    from PIL import Image

    clip, _, preprocess = open_clip.create_model_and_transforms(
        model, pretrained=pretrained, device=device)
    tokenizer = open_clip.get_tokenizer(model)
    clip.eval()

    def encode_image(path: Path) -> np.ndarray:
        with Image.open(path) as img:
            tensor = preprocess(img.convert("RGB")).unsqueeze(0).to(device)
        with torch.no_grad():
            features = clip.encode_image(tensor)
        return features[0].detach().cpu().numpy().astype(np.float32)

    def encode_text(text: str) -> np.ndarray:
        tokens = tokenizer([text]).to(device)
        with torch.no_grad():
            features = clip.encode_text(tokens)
        return features[0].detach().cpu().numpy().astype(np.float32)

    return encode_image, encode_text


# ─── content-hash vector cache ───────────────────────────────────────


def _cache_load(path: Path, force: bool) -> np.ndarray | None:
    """The cached vector, or None on miss/--force/corruption."""
    if force or not path.is_file():
        return None
    try:
        vec = np.load(path)
    except Exception:
        return None
    if vec.ndim != 1 or vec.size == 0 \
            or not np.issubdtype(vec.dtype, np.floating):
        return None
    return vec.astype(np.float32, copy=False)


def _cache_store(path: Path, vec: np.ndarray) -> None:
    """Write-tmp-then-rename so parallel runs never see a torn .npy."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            np.save(f, vec)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ─── sidecar writer ──────────────────────────────────────────────────


def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(
        dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _embed_assets(
    assets: list[Asset],
    resolve_thumbnail,
    *,
    where: str,
    model: str = DEFAULT_MODEL,
    pretrained: str = DEFAULT_PRETRAINED,
    device: str = "cpu",
    cache_dir: str | Path | None = None,
    force: bool = False,
) -> dict:
    """The mode-independent core: encode assets, return sidecar pieces.

    ``resolve_thumbnail(asset)`` maps an asset to the on-disk path of
    its image source (or ``None`` when it has no thumbnail) -- the only
    thing that differs between in-dir and manifest mode.

    Per asset: an image vector from the resolved thumbnail file
    (skipped when it has none), a text vector from :func:`asset_text`
    (skipped when empty). Rows are L2-normalized float32 in assignment
    order.

    Vectors are cached (default ``~/.cache/dreamlake/assets-embed/``)
    keyed by sha256 of the thumbnail bytes / of ``model_id + text``, so
    unchanged assets never re-run the model -- the encoder is not even
    loaded when every vector is a cache hit. ``force=True`` bypasses
    cache reads (but still refreshes the cache).

    Returns ``{"items", "matrix", "dim", "images", "texts"}``; raises
    :class:`ValueError` when there is nothing at all to embed.
    """
    model, pretrained = resolve_model(model, pretrained)
    mid = model_id(model, pretrained)
    cache_ns = (
        Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
    ).expanduser() / mid.replace("/", "__")

    encoders: tuple | None = None

    def get_encoders() -> tuple:
        nonlocal encoders
        if encoders is None:
            encoders = _load_encoder(model, pretrained, device)
        return encoders

    rows: list[np.ndarray] = []
    dim: int | None = None

    def add_row(vec: np.ndarray, what: str) -> int | None:
        """L2-normalize + append; None for zero/non-finite vectors."""
        nonlocal dim
        vec = np.asarray(vec, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vec))
        if not math.isfinite(norm) or norm == 0.0:
            warnings.warn(
                f"{what}: zero or non-finite vector, skipped", stacklevel=2)
            return None
        if dim is None:
            dim = int(vec.size)
        elif vec.size != dim:
            raise ValueError(
                f"{what}: vector dim {vec.size} != established dim {dim}")
        rows.append(vec / norm)
        return len(rows) - 1

    items: list[dict] = []
    images = {"embedded": 0, "cached": 0, "missing": 0, "failed": 0}
    texts = {"embedded": 0, "cached": 0, "empty": 0, "failed": 0}

    for asset in assets:
        image_row: int | None = None
        text_row: int | None = None

        thumb = resolve_thumbnail(asset)
        if thumb is not None:
            thumb = Path(thumb)
            if not thumb.is_file():
                warnings.warn(
                    f"{asset.id}: thumbnail {asset.thumbnail!r} missing "
                    f"on disk at {thumb}, skipped", stacklevel=2)
                images["missing"] += 1
            else:
                key = hashlib.sha256(thumb.read_bytes()).hexdigest()
                cpath = cache_ns / f"image-{key}.npy"
                vec = _cache_load(cpath, force)
                if vec is not None:
                    images["cached"] += 1
                else:
                    encode_image, _ = get_encoders()
                    try:
                        vec = np.asarray(encode_image(thumb))
                    except Exception as e:
                        warnings.warn(
                            f"{asset.id}: image embed failed for {thumb}: "
                            f"{type(e).__name__}: {e}", stacklevel=2)
                        images["failed"] += 1
                        vec = None
                    if vec is not None:
                        _cache_store(cpath, np.asarray(vec, np.float32))
                        images["embedded"] += 1
                if vec is not None:
                    image_row = add_row(vec, f"{asset.id} image")

        text = asset_text(asset)
        if not text:
            texts["empty"] += 1
        else:
            key = hashlib.sha256(
                (mid + "\x00" + text).encode("utf-8")).hexdigest()
            cpath = cache_ns / f"text-{key}.npy"
            vec = _cache_load(cpath, force)
            if vec is not None:
                texts["cached"] += 1
            else:
                _, encode_text = get_encoders()
                try:
                    vec = np.asarray(encode_text(text))
                except Exception as e:
                    warnings.warn(
                        f"{asset.id}: text embed failed: "
                        f"{type(e).__name__}: {e}", stacklevel=2)
                    texts["failed"] += 1
                    vec = None
                if vec is not None:
                    _cache_store(cpath, np.asarray(vec, np.float32))
                    texts["embedded"] += 1
            if vec is not None:
                text_row = add_row(vec, f"{asset.id} text")

        items.append({"id": asset.id, "image": image_row, "text": text_row})

    if not rows:
        raise ValueError(
            f"{where}: nothing to embed (no thumbnails, no text)")

    return {
        "items": items,
        "matrix": np.stack(rows).astype("<f4"),
        "dim": dim,
        "images": images,
        "texts": texts,
        "model": mid,
    }


def _write_vectors(out_json: Path, out_f32: Path, result: dict) -> None:
    """Write the two-file sidecar from an :func:`_embed_assets` result.

    Matrix first, manifest second: the .json is the pointer readers
    trust, so it must never name rows the .f32 does not yet hold. Both
    files land atomically (tmp + rename).
    """
    _atomic_write(out_f32, result["matrix"].tobytes(order="C"))
    doc = {
        "schema": VECTORS_SCHEMA,
        "model": result["model"],
        "dim": result["dim"],
        "items": result["items"],
    }
    _atomic_write(
        out_json,
        (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode(
            "utf-8"),
    )


def _stats(result: dict, n_assets: int, out_json: Path,
           out_f32: Path) -> dict:
    return {
        "model": result["model"],
        "dim": result["dim"],
        "assets": n_assets,
        "rows": len(result["matrix"]),
        "images": result["images"],
        "texts": result["texts"],
        "json": str(out_json),
        "f32": str(out_f32),
    }


def embed_library(
    lib_dir: str | Path,
    *,
    model: str = DEFAULT_MODEL,
    pretrained: str = DEFAULT_PRETRAINED,
    device: str = "cpu",
    cache_dir: str | Path | None = None,
    force: bool = False,
) -> dict:
    """Legacy in-dir mode: sidecar next to ``<lib_dir>/assets.json``.

    Thumbnails resolve inside the library directory; output lands at
    ``<lib_dir>/assets.vectors.{json,f32}``. See :func:`_embed_assets`
    for the shared per-asset behavior and caching.

    Returns a stats dict: ``model``, ``dim``, ``assets``, ``rows``,
    ``images``/``texts`` counters, and the two output paths.
    """
    lib_dir = Path(lib_dir)
    manifest = load_manifest(lib_dir / "assets.json")

    def resolve(asset: Asset) -> Path | None:
        return lib_dir / asset.thumbnail if asset.thumbnail else None

    result = _embed_assets(
        manifest.assets, resolve, where=str(lib_dir), model=model,
        pretrained=pretrained, device=device, cache_dir=cache_dir,
        force=force,
    )
    out_json = lib_dir / VECTORS_JSON
    out_f32 = lib_dir / VECTORS_F32
    _write_vectors(out_json, out_f32, result)
    return _stats(result, len(manifest.assets), out_json, out_f32)


def embed_manifest(
    manifest_path: str | Path,
    *,
    files_root: str | Path,
    thumbs_dir: str | Path,
    out_dir: str | Path,
    model: str = DEFAULT_MODEL,
    pretrained: str = DEFAULT_PRETRAINED,
    device: str = "cpu",
    cache_dir: str | Path | None = None,
    force: bool = False,
) -> dict:
    """Manifest mode: the CLI's push-time step, decoupled from layout.

    Reads assets from the built wire manifest at ``manifest_path``. Per
    asset, the image source is: ``<thumbs_dir>/<id>.webp`` when its
    ``thumbnail`` sits under the reserved ``.dreamlake/thumbnails/``
    prefix (the batch renderer's cache layout), else
    ``<files_root>/<thumbnail>`` (a user-authored image in the source
    tree). Text metadata comes from the manifest as in in-dir mode.

    Writes ``<out_dir>/vectors.json`` + ``<out_dir>/vectors.f32`` (note:
    no ``assets.`` prefix -- these live in the CLI cache, not in a
    library directory), same inner format and content-hash cache as
    in-dir mode. Returns the same stats dict shape.
    """
    manifest_path = Path(manifest_path)
    files_root = Path(files_root)
    thumbs_dir = Path(thumbs_dir)
    out_dir = Path(out_dir)
    manifest = load_manifest(manifest_path)

    def resolve(asset: Asset) -> Path | None:
        if not asset.thumbnail:
            return None
        if asset.thumbnail.startswith(DREAMLAKE_THUMBS_PREFIX):
            return thumbs_dir / f"{asset.id}.webp"
        return files_root / asset.thumbnail

    result = _embed_assets(
        manifest.assets, resolve, where=str(manifest_path), model=model,
        pretrained=pretrained, device=device, cache_dir=cache_dir,
        force=force,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / OUT_VECTORS_JSON
    out_f32 = out_dir / OUT_VECTORS_F32
    _write_vectors(out_json, out_f32, result)
    return _stats(result, len(manifest.assets), out_json, out_f32)


# ─── CLI ─────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m dreamlake.assets_tools.embed",
        description=(
            "Generate the embeddings sidecar for an asset library. "
            "Manifest mode (--manifest + --files-root + --thumbs-dir + "
            "--out-dir) writes vectors.json + vectors.f32 into --out-dir "
            "and prints one JSON stats line to stdout; legacy in-dir "
            "mode (a library directory argument) writes "
            "assets.vectors.json + assets.vectors.f32 next to its "
            "assets.json. DreamLake semantic search needs the server's "
            "query-encoder space: siglip2 (ViT-B-16-SigLIP2/webli, the "
            "default). Any other model -- including the explicitly-"
            "legacy clip alias (ViT-L-14-quickgelu/openai) -- writes a "
            "valid sidecar the server degrades to keyword-only search."
        ),
    )
    parser.add_argument(
        "lib_dir", nargs="?", type=Path, default=None,
        help="legacy in-dir mode: library directory containing "
             "assets.json")
    parser.add_argument(
        "--manifest", type=Path, default=None,
        help="manifest mode: path to a built wire manifest JSON")
    parser.add_argument(
        "--files-root", type=Path, default=None,
        help="manifest mode: the user source dir non-.dreamlake "
             "thumbnail paths resolve under")
    parser.add_argument(
        "--thumbs-dir", type=Path, default=None,
        help="manifest mode: cache dir holding <asset id>.webp for "
             ".dreamlake/thumbnails/ entries")
    parser.add_argument(
        "--out-dir", type=Path, default=None,
        help="manifest mode: directory receiving vectors.json + "
             "vectors.f32")
    parser.add_argument(
        "--model", default=os.environ.get(
            "DREAMLAKE_EMBED_MODEL", DEFAULT_MODEL),
        help=f"open_clip model name or alias ({'|'.join(MODEL_ALIASES)}; "
             f"an alias pins its pretrained tag; clip is legacy -- the "
             f"server no longer fuses it). Default: "
             f"$DREAMLAKE_EMBED_MODEL or {DEFAULT_MODEL}")
    parser.add_argument(
        "--pretrained", default=DEFAULT_PRETRAINED,
        help=f"open_clip pretrained tag (default: {DEFAULT_PRETRAINED}; "
             f"ignored when --model is an alias)")
    parser.add_argument(
        "--device", default="cpu", help="torch device (default: cpu)")
    parser.add_argument(
        "--cache-dir", type=Path, default=None,
        help=f"vector cache directory (default: {DEFAULT_CACHE_DIR})")
    parser.add_argument(
        "--force", action="store_true",
        help="re-encode everything, ignoring cached vectors")
    args = parser.parse_args(argv)

    if (args.manifest is None) == (args.lib_dir is None):
        parser.error("pass exactly one of <lib_dir> or --manifest")
    if args.manifest is not None:
        missing = [
            flag for flag, value in (
                ("--files-root", args.files_root),
                ("--thumbs-dir", args.thumbs_dir),
                ("--out-dir", args.out_dir),
            ) if value is None
        ]
        if missing:
            parser.error(
                f"--manifest mode requires {', '.join(missing)}")
    elif any(v is not None
             for v in (args.files_root, args.thumbs_dir, args.out_dir)):
        parser.error(
            "--files-root/--thumbs-dir/--out-dir only apply with "
            "--manifest")

    common = {
        "model": args.model, "pretrained": args.pretrained,
        "device": args.device, "cache_dir": args.cache_dir,
        "force": args.force,
    }
    try:
        if args.manifest is not None:
            stats = embed_manifest(
                args.manifest, files_root=args.files_root,
                thumbs_dir=args.thumbs_dir, out_dir=args.out_dir,
                **common)
        else:
            stats = embed_library(args.lib_dir, **common)
    except (FileNotFoundError, ValueError, ManifestError, ImportError,
            RuntimeError) as e:
        # RuntimeError: open_clip wraps weight-download failures in it.
        print(f"error: {e}", file=sys.stderr)
        return 1

    summary = (
        f"wrote {stats['json']} + {Path(stats['f32']).name}: "
        f"{stats['assets']} assets, {stats['rows']} rows, "
        f"dim {stats['dim']} ({stats['model']})")
    im, tx = stats["images"], stats["texts"]
    counters = (
        f"images: {im['embedded']} embedded, {im['cached']} cached, "
        f"{im['missing']} missing, {im['failed']} failed | "
        f"texts: {tx['embedded']} embedded, {tx['cached']} cached, "
        f"{tx['empty']} empty, {tx['failed']} failed")
    if args.manifest is not None:
        # stdout is the machine channel: exactly one JSON line, the
        # stats dict the CLI parses. Chatter goes to stderr.
        print(summary, file=sys.stderr)
        print(counters, file=sys.stderr)
        print(json.dumps(stats, ensure_ascii=False))
    else:
        print(summary)
        print(counters)
    return 0


if __name__ == "__main__":
    sys.exit(main())
