"""The ``dreamlake.assets/v1`` manifest: dataclasses, validation, I/O.

The wire manifest the CLI builds at push time and the server validates
STRICTLY -- unknown keys are rejected -- so this module is the single
place that knows the schema: the dataclasses carry exactly the allowed
keys, :func:`validate` mirrors the server's rules
(``dreamlake-server`` ``assetLibraryManifest.ts``), and
:func:`load_manifest` refuses unknown keys the same way the server
would.

Generated artifacts (thumbnails, vectors) live under the reserved
``.dreamlake/`` prefix and are declared in the OPTIONAL top-level
``generated`` list -- canonical ``.dreamlake/...`` paths, no
duplicates, never ``.dreamlake/manifest.json`` itself. Asset
``files[].path`` must NOT sit under ``.dreamlake/``; an asset's
``thumbnail`` may point either at one of its files or at a
``generated`` entry, while ``entry``/``entryPoints[*].file`` stay
files-only.

Output is deterministic: ``json.dumps(..., sort_keys=True)`` (key order
is not semantic) with asset order preserved as given (it is).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA = "dreamlake.assets/v1"

#: viewer-dispatch kinds an asset may declare
ASSET_KINDS = ("mjcf", "urdf", "mesh", "splat", "texture", "image", "file")
#: kinds an entry point may declare
ENTRYPOINT_KINDS = ("scene", "robot")

MAX_ASSETS = 20_000
MAX_FILE_ENTRIES = 100_000
MAX_GENERATED = 50_000
MAX_MANIFEST_BYTES = 20 * 1024 * 1024
MAX_META_BYTES = 8 * 1024
MAX_TAGS = 32
MAX_TAG_LEN = 64
MAX_ENTRYPOINTS = 32
MAX_TITLE_LEN = 200
MAX_DESCRIPTION_LEN = 2_000
MAX_ATTRIBUTION_LEN = 1_000

_LIBRARY_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_ASSET_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
_CATEGORY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_MANIFEST_KEYS = {"schema", "library", "assets", "generated"}

#: the reserved prefix for CLI-generated artifacts; forbidden in asset
#: files[], required on every generated[] entry
GENERATED_PREFIX = ".dreamlake/"
#: the wire manifest itself may never appear in generated[]
_RESERVED_MANIFEST_PATH = ".dreamlake/manifest.json"
_LIBRARY_KEYS = {
    "name", "type", "title", "description", "provider", "homepage",
    "license", "tags", "upstream",
}
_LIBRARY_UPSTREAM_KEYS = {"repo", "commit"}
_ASSET_KEYS = {
    "id", "title", "description", "category", "tags", "kind", "format",
    "license", "attribution", "upstream", "entry", "entryPoints", "files",
    "thumbnail", "meta",
}
_ASSET_UPSTREAM_KEYS = {"id", "url"}
_ENTRYPOINT_KEYS = {"kind", "file"}
_FILE_KEYS = {"path", "size", "sha256"}


class ManifestError(ValueError):
    """A manifest that the server would reject."""


# ─── dataclasses ─────────────────────────────────────────────────────


@dataclass
class AssetFile:
    """One file entry: library-root-relative path + size + sha256."""

    path: str
    size: int
    sha256: str

    def to_json(self) -> dict:
        return {"path": self.path, "size": self.size, "sha256": self.sha256}


@dataclass
class LibraryInfo:
    """Library-level metadata; everything optional except ``type``."""

    name: str | None = None
    type: str = "3d"
    title: str | None = None
    description: str | None = None
    provider: str | None = None
    homepage: str | None = None
    license: str | None = None  # SPDX id, the library-wide default
    tags: list[str] | None = None
    upstream: dict[str, str] | None = None  # {"repo": ..., "commit": ...}

    def to_json(self) -> dict:
        doc: dict[str, Any] = {}
        for key in ("name", "title", "description", "provider", "homepage",
                    "license"):
            value = getattr(self, key)
            if value is not None:
                doc[key] = value
        doc["type"] = self.type
        if self.tags:
            doc["tags"] = list(self.tags)
        if self.upstream:
            doc["upstream"] = dict(self.upstream)
        return doc


@dataclass
class Asset:
    """One asset: id + files, plus optional metadata.

    ``entry_points`` maps name -> ``{"kind": "scene"|"robot", "file":
    <path>}`` and serializes under the schema's ``entryPoints`` key.
    """

    id: str
    files: list[AssetFile] = field(default_factory=list)
    title: str | None = None
    description: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    kind: str = "file"
    format: str | None = None
    license: str | None = None  # overrides the library default
    attribution: str | None = None
    upstream: dict[str, str] | None = None  # {"id": ..., "url": ...}
    entry: str | None = None
    entry_points: dict[str, dict] | None = None
    thumbnail: str | None = None
    meta: dict | None = None

    def to_json(self) -> dict:
        doc: dict[str, Any] = {"id": self.id, "kind": self.kind}
        for key in ("title", "description", "category", "format", "license",
                    "attribution", "entry", "thumbnail"):
            value = getattr(self, key)
            if value is not None:
                doc[key] = value
        if self.tags:
            doc["tags"] = list(self.tags)
        if self.upstream:
            doc["upstream"] = dict(self.upstream)
        if self.entry_points:
            doc["entryPoints"] = {
                name: dict(ep) for name, ep in self.entry_points.items()
            }
        doc["files"] = [f.to_json() for f in self.files]
        if self.meta is not None:
            doc["meta"] = self.meta
        return doc


@dataclass
class LibraryManifest:
    """The full ``assets.json`` document.

    ``generated`` (optional) lists the canonical ``.dreamlake/...``
    paths of CLI-generated artifacts uploaded alongside the library
    (thumbnails, vectors); asset thumbnails may reference them.
    """

    library: LibraryInfo = field(default_factory=LibraryInfo)
    assets: list[Asset] = field(default_factory=list)
    generated: list[str] | None = None
    schema: str = SCHEMA

    def to_json(self) -> dict:
        doc = {
            "schema": self.schema,
            "library": self.library.to_json(),
            "assets": [a.to_json() for a in self.assets],
        }
        if self.generated:
            doc["generated"] = list(self.generated)
        return doc

    def dumps(self) -> str:
        """Deterministic serialization (sorted keys, stable asset order)."""
        return json.dumps(
            self.to_json(), indent=2, sort_keys=True, ensure_ascii=False
        ) + "\n"


# ─── validation ──────────────────────────────────────────────────────


def _check_str(value: Any, where: str, max_len: int | None = None) -> None:
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{where}: must be a non-empty string")
    if max_len is not None and len(value) > max_len:
        raise ManifestError(
            f"{where}: length {len(value)} exceeds {max_len}")


def _check_tags(tags: Any, where: str) -> None:
    if not isinstance(tags, list):
        raise ManifestError(f"{where}: must be a list")
    if len(tags) > MAX_TAGS:
        raise ManifestError(f"{where}: {len(tags)} tags exceed {MAX_TAGS}")
    for i, tag in enumerate(tags):
        _check_str(tag, f"{where}[{i}]", MAX_TAG_LEN)


def _check_path(path: Any, where: str) -> None:
    _check_str(path, where)
    if "\\" in path:
        raise ManifestError(f"{where}: backslash in path {path!r}")
    if path.startswith("/"):
        raise ManifestError(f"{where}: leading slash in path {path!r}")
    segments = path.split("/")
    if any(seg == "" for seg in segments):
        raise ManifestError(f"{where}: empty segment in path {path!r}")
    if any(seg == ".." for seg in segments):
        raise ManifestError(f"{where}: '..' segment in path {path!r}")


def _check_upstream(upstream: Any, allowed: set[str], where: str) -> None:
    if not isinstance(upstream, dict):
        raise ManifestError(f"{where}: must be an object")
    unknown = set(upstream) - allowed
    if unknown:
        raise ManifestError(
            f"{where}: unknown keys {sorted(unknown)} "
            f"(allowed: {sorted(allowed)})")
    for key, value in upstream.items():
        _check_str(value, f"{where}.{key}")


def _validate_library(lib: LibraryInfo) -> None:
    if lib.name is not None:
        _check_str(lib.name, "library.name")
        if not _LIBRARY_NAME_RE.match(lib.name):
            raise ManifestError(
                f"library.name: {lib.name!r} does not match "
                f"{_LIBRARY_NAME_RE.pattern}")
    _check_str(lib.type, "library.type")
    for key in ("title", "description", "provider", "homepage", "license"):
        value = getattr(lib, key)
        if value is not None:
            _check_str(value, f"library.{key}")
    if lib.tags is not None:
        _check_tags(lib.tags, "library.tags")
    if lib.upstream is not None:
        _check_upstream(lib.upstream, _LIBRARY_UPSTREAM_KEYS,
                        "library.upstream")


def _validate_generated(generated: Any) -> set[str]:
    """Validate the top-level ``generated`` list; returns it as a set.

    Every entry must be a canonical path under ``.dreamlake/``, unique,
    and never the wire manifest itself.
    """
    if not isinstance(generated, list):
        raise ManifestError("generated: must be a list")
    if len(generated) > MAX_GENERATED:
        raise ManifestError(
            f"generated: {len(generated)} entries exceed {MAX_GENERATED}")
    seen: set[str] = set()
    for i, path in enumerate(generated):
        where = f"generated[{i}]"
        _check_path(path, where)
        if not path.startswith(GENERATED_PREFIX):
            raise ManifestError(
                f"{where}: {path!r} must start with {GENERATED_PREFIX!r}")
        if path == _RESERVED_MANIFEST_PATH:
            raise ManifestError(
                f"{where}: {path!r} is reserved (the wire manifest "
                f"itself)")
        if path in seen:
            raise ManifestError(f"{where}: duplicate path {path!r}")
        seen.add(path)
    return seen


def _validate_asset(
    asset: Asset, index: int, shared: dict[str, tuple[int, str, str]],
    generated: set[str],
) -> int:
    """Validate one asset; returns its file-entry count.

    ``shared`` accumulates path -> (size, sha256, owner-id) across assets
    to enforce that a path shared between assets is byte-identical.
    ``generated`` is the (validated) top-level generated[] set --
    thumbnails may point into it; entry/entryPoints may not.
    """
    where = f"assets[{index}]"
    _check_str(asset.id, f"{where}.id")
    if not _ASSET_ID_RE.match(asset.id):
        raise ManifestError(
            f"{where}.id: {asset.id!r} does not match {_ASSET_ID_RE.pattern}")
    where = f"assets[{index}] (id {asset.id!r})"

    if asset.title is not None:
        _check_str(asset.title, f"{where}.title", MAX_TITLE_LEN)
    if asset.description is not None:
        _check_str(asset.description, f"{where}.description",
                   MAX_DESCRIPTION_LEN)
    if asset.category is not None:
        _check_str(asset.category, f"{where}.category")
        if not _CATEGORY_RE.match(asset.category):
            raise ManifestError(
                f"{where}.category: {asset.category!r} does not match "
                f"{_CATEGORY_RE.pattern}")
    if asset.tags is not None:
        _check_tags(asset.tags, f"{where}.tags")
    if asset.kind not in ASSET_KINDS:
        raise ManifestError(
            f"{where}.kind: {asset.kind!r} not one of {ASSET_KINDS}")
    if asset.format is not None:
        _check_str(asset.format, f"{where}.format")
    if asset.license is not None:
        _check_str(asset.license, f"{where}.license")
    if asset.attribution is not None:
        _check_str(asset.attribution, f"{where}.attribution",
                   MAX_ATTRIBUTION_LEN)
    if asset.upstream is not None:
        _check_upstream(asset.upstream, _ASSET_UPSTREAM_KEYS,
                        f"{where}.upstream")

    # files: object form, valid paths/hashes, no dup path within the
    # asset, byte-identical wherever shared across assets
    if not isinstance(asset.files, list) or not asset.files:
        raise ManifestError(f"{where}.files: must be a non-empty list")
    paths: set[str] = set()
    for i, f in enumerate(asset.files):
        fwhere = f"{where}.files[{i}]"
        if not isinstance(f, AssetFile):
            raise ManifestError(f"{fwhere}: must be an AssetFile")
        _check_path(f.path, f"{fwhere}.path")
        if f.path.startswith(GENERATED_PREFIX):
            raise ManifestError(
                f"{fwhere}.path: {f.path!r} is under the reserved "
                f"{GENERATED_PREFIX!r} prefix (generated artifacts are "
                f"declared in top-level generated[], never in files)")
        if not isinstance(f.size, int) or isinstance(f.size, bool) \
                or f.size < 0:
            raise ManifestError(
                f"{fwhere}.size: must be a non-negative integer")
        if not isinstance(f.sha256, str) or not _SHA256_RE.match(f.sha256):
            raise ManifestError(
                f"{fwhere}.sha256: must be 64 lowercase hex chars")
        if f.path in paths:
            raise ManifestError(f"{fwhere}: duplicate path {f.path!r}")
        paths.add(f.path)
        prior = shared.get(f.path)
        if prior is None:
            shared[f.path] = (f.size, f.sha256, asset.id)
        elif prior[:2] != (f.size, f.sha256):
            raise ManifestError(
                f"{fwhere}: path {f.path!r} differs from the copy in "
                f"asset {prior[2]!r} (shared paths must have identical "
                f"size and sha256)")

    if asset.entry is not None:
        _check_str(asset.entry, f"{where}.entry")
        if asset.entry not in paths:
            raise ManifestError(
                f"{where}.entry: {asset.entry!r} is not listed in files")
    if asset.entry_points is not None:
        if not isinstance(asset.entry_points, dict):
            raise ManifestError(f"{where}.entryPoints: must be an object")
        if len(asset.entry_points) > MAX_ENTRYPOINTS:
            raise ManifestError(
                f"{where}.entryPoints: {len(asset.entry_points)} entries "
                f"exceed {MAX_ENTRYPOINTS}")
        for name, ep in asset.entry_points.items():
            epwhere = f"{where}.entryPoints[{name!r}]"
            _check_str(name, f"{where}.entryPoints key")
            if not isinstance(ep, dict):
                raise ManifestError(f"{epwhere}: must be an object")
            unknown = set(ep) - _ENTRYPOINT_KEYS
            if unknown:
                raise ManifestError(
                    f"{epwhere}: unknown keys {sorted(unknown)}")
            if ep.get("kind") not in ENTRYPOINT_KINDS:
                raise ManifestError(
                    f"{epwhere}.kind: {ep.get('kind')!r} not one of "
                    f"{ENTRYPOINT_KINDS}")
            if ep.get("file") not in paths:
                raise ManifestError(
                    f"{epwhere}.file: {ep.get('file')!r} is not listed "
                    f"in files")
    if asset.thumbnail is not None:
        _check_str(asset.thumbnail, f"{where}.thumbnail")
        if asset.thumbnail not in paths and asset.thumbnail not in generated:
            raise ManifestError(
                f"{where}.thumbnail: {asset.thumbnail!r} is not listed "
                f"in files or generated")
    if asset.meta is not None:
        if not isinstance(asset.meta, dict):
            raise ManifestError(f"{where}.meta: must be an object")
        try:
            meta_bytes = len(
                json.dumps(asset.meta, sort_keys=True).encode("utf-8"))
        except (TypeError, ValueError) as e:
            raise ManifestError(
                f"{where}.meta: not JSON-serializable: {e}") from e
        if meta_bytes > MAX_META_BYTES:
            raise ManifestError(
                f"{where}.meta: {meta_bytes} bytes as JSON exceed "
                f"{MAX_META_BYTES}")
    return len(asset.files)


def validate(manifest: LibraryManifest) -> None:
    """Raise :class:`ManifestError` on anything the server would reject."""
    if not isinstance(manifest, LibraryManifest):
        raise ManifestError("manifest must be a LibraryManifest")
    if manifest.schema != SCHEMA:
        raise ManifestError(
            f"schema: expected {SCHEMA!r}, got {manifest.schema!r}")
    if not isinstance(manifest.library, LibraryInfo):
        raise ManifestError("library: must be a LibraryInfo")
    _validate_library(manifest.library)
    generated = (
        _validate_generated(manifest.generated)
        if manifest.generated is not None else set()
    )
    if not isinstance(manifest.assets, list):
        raise ManifestError("assets: must be a list")
    if len(manifest.assets) > MAX_ASSETS:
        raise ManifestError(
            f"assets: {len(manifest.assets)} assets exceed {MAX_ASSETS}")
    seen_ids: set[str] = set()
    shared: dict[str, tuple[int, str, str]] = {}
    total_files = 0
    for i, asset in enumerate(manifest.assets):
        if not isinstance(asset, Asset):
            raise ManifestError(f"assets[{i}]: must be an Asset")
        if asset.id in seen_ids:
            raise ManifestError(f"assets[{i}]: duplicate id {asset.id!r}")
        total_files += _validate_asset(asset, i, shared, generated)
        seen_ids.add(asset.id)
    if total_files > MAX_FILE_ENTRIES:
        raise ManifestError(
            f"files: {total_files} file entries exceed {MAX_FILE_ENTRIES}")


# ─── I/O ─────────────────────────────────────────────────────────────


def write_manifest(manifest: LibraryManifest, out_dir: str | Path) -> Path:
    """Validate + write ``<out_dir>/assets.json``; returns its path."""
    validate(manifest)
    text = manifest.dumps()
    if len(text.encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise ManifestError(
            f"manifest serializes to more than {MAX_MANIFEST_BYTES} bytes")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "assets.json"
    path.write_text(text, encoding="utf-8")
    return path


def _reject_unknown(doc: dict, allowed: set[str], where: str) -> None:
    unknown = set(doc) - allowed
    if unknown:
        raise ManifestError(
            f"{where}: unknown keys {sorted(unknown)} "
            f"(allowed: {sorted(allowed)})")


def _load_library(doc: Any) -> LibraryInfo:
    if not isinstance(doc, dict):
        raise ManifestError("library: must be an object")
    _reject_unknown(doc, _LIBRARY_KEYS, "library")
    kwargs = {k: doc[k] for k in _LIBRARY_KEYS - {"type"} if k in doc}
    return LibraryInfo(type=doc.get("type", "3d"), **kwargs)


def _load_asset(doc: Any, index: int) -> Asset:
    where = f"assets[{index}]"
    if not isinstance(doc, dict):
        raise ManifestError(f"{where}: must be an object")
    _reject_unknown(doc, _ASSET_KEYS, where)
    if "id" not in doc:
        raise ManifestError(f"{where}: missing 'id'")
    files_doc = doc.get("files")
    if not isinstance(files_doc, list):
        raise ManifestError(
            f"{where}.files: must be a list of objects "
            f"(the object form is required)")
    files = []
    for i, f in enumerate(files_doc):
        fwhere = f"{where}.files[{i}]"
        if not isinstance(f, dict):
            raise ManifestError(
                f"{fwhere}: must be an object (the object form is required)")
        _reject_unknown(f, _FILE_KEYS, fwhere)
        missing = _FILE_KEYS - set(f)
        if missing:
            raise ManifestError(f"{fwhere}: missing keys {sorted(missing)}")
        files.append(
            AssetFile(path=f["path"], size=f["size"], sha256=f["sha256"]))
    plain = {
        k: doc[k]
        for k in _ASSET_KEYS - {"id", "files", "entryPoints", "kind"}
        if k in doc
    }
    return Asset(
        id=doc["id"],
        files=files,
        kind=doc.get("kind", "file"),
        entry_points=doc.get("entryPoints"),
        **plain,
    )


def load_manifest(path: str | Path) -> LibraryManifest:
    """Parse + strictly validate an ``assets.json`` file."""
    path = Path(path)
    raw = path.read_bytes()
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ManifestError(
            f"{path}: {len(raw)} bytes exceed {MAX_MANIFEST_BYTES}")
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ManifestError(f"{path}: invalid JSON: {e}") from e
    if not isinstance(doc, dict):
        raise ManifestError(f"{path}: manifest must be a JSON object")
    _reject_unknown(doc, _MANIFEST_KEYS, "manifest")
    if doc.get("schema") != SCHEMA:
        raise ManifestError(
            f"schema: expected {SCHEMA!r}, got {doc.get('schema')!r}")
    assets_doc = doc.get("assets")
    if not isinstance(assets_doc, list):
        raise ManifestError("assets: must be a list")
    manifest = LibraryManifest(
        schema=doc["schema"],
        library=_load_library(doc.get("library", {})),
        assets=[_load_asset(a, i) for i, a in enumerate(assets_doc)],
        generated=doc.get("generated"),
    )
    validate(manifest)
    return manifest


# ─── file helpers ────────────────────────────────────────────────────


def hash_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    """Streamed sha256 of a file; returns lowercase hex."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def file_entry(root: str | Path, relpath: str | Path) -> AssetFile:
    """Build the :class:`AssetFile` for ``<root>/<relpath>``.

    ``relpath`` is normalized to a POSIX-style library-root-relative
    path (the only form the manifest accepts).
    """
    rel = PurePosixPath(Path(relpath).as_posix())
    full = Path(root) / Path(relpath)
    return AssetFile(
        path=str(rel),
        size=full.stat().st_size,
        sha256=hash_file(full),
    )
