"""CLIP embedding sidecar for asset libraries.

``python -m dreamlake.assets_tools.embed <library-dir>`` reads the
library's ``assets.json`` and writes the two-file embeddings sidecar
next to it, in exactly the layout the server parser
(``dreamlake-server`` ``assetLibrarySearch.ts::parseVectorsSidecar``)
expects:

* ``assets.vectors.json`` -- ``{"schema":
  "dreamlake.assets.vectors/v1", "model": ..., "dim": ..., "items":
  [{"id", "image", "text"}]}`` where ``image``/``text`` are row indices
  into the matrix, or null when the asset has no thumbnail / no text;
* ``assets.vectors.f32`` -- the matrix: concatenated little-endian
  float32 rows, each row L2-normalized, row *i* at byte ``i * dim * 4``.

The embedding model is a COMPATIBILITY CONTRACT with the query-time
encoder (``scripts/clip-service/server.py``): open_clip ``ViT-L-14``
with the ``openai`` pretrained weights (768-dim), images through the
model's own val preprocess (PIL, ``convert("RGB")``), texts through the
model tokenizer. Change either side and image/text similarity turns to
garbage.

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
VECTORS_JSON = "assets.vectors.json"
VECTORS_F32 = "assets.vectors.f32"

#: MUST match scripts/clip-service/server.py (the query-time encoder).
DEFAULT_MODEL = "ViT-L-14"
DEFAULT_PRETRAINED = "openai"

DEFAULT_CACHE_DIR = Path("~/.cache/dreamlake/assets-embed")


def model_id(model: str = DEFAULT_MODEL,
             pretrained: str = DEFAULT_PRETRAINED) -> str:
    """The identifier written to the sidecar's ``model`` field."""
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

    Mirrors scripts/clip-service/server.py: open_clip model + its own
    val preprocess + its tokenizer. Raw (un-normalized) features out;
    normalization happens once, at matrix-write time.

    open_clip >= 2.24 warns "QuickGELU mismatch" for ViT-L-14/openai --
    expected and harmless HERE ONLY BECAUSE the clip-service passes the
    exact same identifiers to the same (recent) open_clip, so both
    sides build the identical model either way.
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


def embed_library(
    lib_dir: str | Path,
    *,
    model: str = DEFAULT_MODEL,
    pretrained: str = DEFAULT_PRETRAINED,
    device: str = "cpu",
    cache_dir: str | Path | None = None,
    force: bool = False,
) -> dict:
    """Write ``assets.vectors.{json,f32}`` next to ``<lib_dir>/assets.json``.

    Per asset: an image vector from its ``thumbnail`` file (skipped when
    it has none), a text vector from :func:`asset_text` (skipped when
    empty). Rows are L2-normalized float32, written little-endian in
    assignment order; both files land atomically (tmp + rename).

    Vectors are cached (default ``~/.cache/dreamlake/assets-embed/``)
    keyed by sha256 of the thumbnail bytes / of ``model_id + text``, so
    unchanged assets never re-run the model -- the encoder is not even
    loaded when every vector is a cache hit. ``force=True`` bypasses
    cache reads (but still refreshes the cache).

    Returns a stats dict: ``model``, ``dim``, ``assets``, ``rows``,
    ``images``/``texts`` counters, and the two output paths.
    """
    lib_dir = Path(lib_dir)
    manifest = load_manifest(lib_dir / "assets.json")
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

    for asset in manifest.assets:
        image_row: int | None = None
        text_row: int | None = None

        if asset.thumbnail:
            thumb = lib_dir / asset.thumbnail
            if not thumb.is_file():
                warnings.warn(
                    f"{asset.id}: thumbnail {asset.thumbnail!r} missing "
                    f"on disk, skipped", stacklevel=2)
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
            f"{lib_dir}: nothing to embed (no thumbnails, no text)")

    # Matrix first, manifest second: the .json is the pointer readers
    # trust, so it must never name rows the .f32 does not yet hold.
    matrix = np.stack(rows).astype("<f4")
    _atomic_write(lib_dir / VECTORS_F32, matrix.tobytes(order="C"))
    doc = {
        "schema": VECTORS_SCHEMA,
        "model": mid,
        "dim": dim,
        "items": items,
    }
    _atomic_write(
        lib_dir / VECTORS_JSON,
        (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode(
            "utf-8"),
    )

    return {
        "model": mid,
        "dim": dim,
        "assets": len(manifest.assets),
        "rows": len(rows),
        "images": images,
        "texts": texts,
        "json": str(lib_dir / VECTORS_JSON),
        "f32": str(lib_dir / VECTORS_F32),
    }


# ─── CLI ─────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m dreamlake.assets_tools.embed",
        description=(
            "Generate the CLIP embeddings sidecar "
            "(assets.vectors.json + assets.vectors.f32) for an asset "
            "library directory. The model must match the query-time "
            "clip-service (ViT-L-14/openai, 768-dim)."
        ),
    )
    parser.add_argument(
        "lib_dir", type=Path, help="library directory containing assets.json")
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"open_clip model name (default: {DEFAULT_MODEL})")
    parser.add_argument(
        "--pretrained", default=DEFAULT_PRETRAINED,
        help=f"open_clip pretrained tag (default: {DEFAULT_PRETRAINED})")
    parser.add_argument(
        "--device", default="cpu", help="torch device (default: cpu)")
    parser.add_argument(
        "--cache-dir", type=Path, default=None,
        help=f"vector cache directory (default: {DEFAULT_CACHE_DIR})")
    parser.add_argument(
        "--force", action="store_true",
        help="re-encode everything, ignoring cached vectors")
    args = parser.parse_args(argv)

    try:
        stats = embed_library(
            args.lib_dir, model=args.model, pretrained=args.pretrained,
            device=args.device, cache_dir=args.cache_dir, force=args.force,
        )
    except (FileNotFoundError, ValueError, ManifestError, ImportError,
            RuntimeError) as e:
        # RuntimeError: open_clip wraps weight-download failures in it.
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(
        f"wrote {stats['json']} + {Path(stats['f32']).name}: "
        f"{stats['assets']} assets, {stats['rows']} rows, "
        f"dim {stats['dim']} ({stats['model']})")
    im, tx = stats["images"], stats["texts"]
    print(
        f"images: {im['embedded']} embedded, {im['cached']} cached, "
        f"{im['missing']} missing, {im['failed']} failed | "
        f"texts: {tx['embedded']} embedded, {tx['cached']} cached, "
        f"{tx['empty']} empty, {tx['failed']} failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
