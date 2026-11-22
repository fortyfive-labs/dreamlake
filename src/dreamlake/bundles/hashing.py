"""Reference implementation of the bundle hash rules (bundle contract v1).

The server (``src/bundles/hashing.ts``) and the CLI implement the same rules.
All three test against ``fixtures/bundles/hash-vectors.json``. Change a rule
here only together with the vectors and the other two implementations.

Rules:

* Hashes are lowercase hex sha256. APIs and lock files write ``sha256:<hex>``.
* file hash = sha256(file bytes).
* dir hash = sha256 of the concatenation, over the children sorted by the
  UTF-8 bytes of their names, of ``<name>\\0<t>\\0<childhex>\\n``. ``t`` is
  ``f`` for a file and ``d`` for a directory. An empty or missing directory
  has the hash sha256("").
* A bundle tree is a set of file paths. Directories exist only as the parents
  of files. Thus an empty directory is not part of a bundle and does not
  change any hash.
* root hash = dir hash of the bundle root.
* prefix hash: ``/`` is the root hash. ``a/b/`` is the dir hash of ``a/b``.
  ``a/b/mug-*`` uses the dir line format over the children of ``a/b`` whose
  name starts with ``mug-``.
* Manifests are JSON with comments: ``bundle.json`` and
  ``entries/<name>.json``. Hashes use the raw manifest bytes, never a parsed
  form. Thus a comment or whitespace edit changes the entry version.
* effective hash = sha256(``entry\\0`` + manifest file hex + ``\\n`` + one
  line ``<item>\\0<prefixhex>\\n`` per ``uses`` item in declared order + one
  line ``<ref>\\0<refeffhex>\\n`` per ref, sorted by the UTF-8 bytes of the
  canonical ref string: ``ns/bundle@N#entry`` with N the displayed version,
  or ``#entry`` for a local ref to an entry of the same bundle version).
* displayed version of an entry at version V = the smallest W <= V such that
  the entry exists with the same effective hash in every version from W to V.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import jsonc

EMPTY_HEX = hashlib.sha256(b"").hexdigest()
PREFIX = "sha256:"
_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class BundleHashError(ValueError):
    """A value that the hash rules do not accept."""


# --------------------------------------------------------------------------
# sha256 helpers


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def to_wire(hex_or_wire: str) -> str:
    """Return ``sha256:<hex>``. Accepts bare hex or the wire form."""
    return PREFIX + to_hex(hex_or_wire)


def to_hex(value: str) -> str:
    """Return the bare lowercase hex. Accepts ``sha256:<hex>`` or bare hex."""
    if not isinstance(value, str):
        raise BundleHashError(f"hash must be a string, got {value!r}")
    hex_ = value.removeprefix(PREFIX)
    hex_ = hex_.lower()
    if not _HEX_RE.match(hex_):
        raise BundleHashError(f"not a sha256 hash: {value!r}")
    return hex_


def file_hash(data: bytes) -> str:
    """Hex hash of the bytes of one file."""
    return sha256_hex(data)


def file_hash_path(path: str | os.PathLike) -> str:
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


# --------------------------------------------------------------------------
# Trees


def _name_key(name: str) -> bytes:
    return name.encode("utf-8")


def dir_line(name: str, kind: str, child_hex: str) -> bytes:
    if kind not in ("f", "d"):
        raise BundleHashError(f"child kind must be 'f' or 'd', got {kind!r}")
    return name.encode("utf-8") + b"\0" + kind.encode() + b"\0" + child_hex.encode() + b"\n"


def dir_preimage(children: Iterable[tuple[str, str, str]]) -> bytes:
    """Bytes hashed for one directory. ``children`` is ``(name, kind, hex)``."""
    ordered = sorted(children, key=lambda c: _name_key(c[0]))
    names = [c[0] for c in ordered]
    if len(set(names)) != len(names):
        raise BundleHashError(f"duplicate child names: {names}")
    return b"".join(dir_line(*c) for c in ordered)


def check_path(path: str) -> str:
    """Validate one bundle file path (relative, ``/`` separated, no ``..``)."""
    if not isinstance(path, str) or not path:
        raise BundleHashError(f"bad bundle path {path!r}")
    if path.startswith("/") or "\\" in path or "\0" in path:
        raise BundleHashError(f"bad bundle path {path!r}")
    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise BundleHashError(f"bad bundle path {path!r}")
    return path


class Tree:
    """A bundle tree: file path -> file hex. Directories are implicit.

    Build it from bytes (:meth:`from_files`), from a server tree listing
    (:meth:`from_items`) or from a local directory (:meth:`from_dir`).
    """

    def __init__(self, files: Mapping[str, str]):
        self.files: dict[str, str] = {}
        for path, hex_ in files.items():
            self.files[check_path(path)] = to_hex(hex_)
        self._nodes: dict[str, dict[str, tuple[str, str | None]]] | None = None
        self._dir_cache: dict[str, str] = {}
        self._check_conflicts()

    # constructors --------------------------------------------------------

    @classmethod
    def from_files(cls, files: Mapping[str, bytes]) -> Tree:
        return cls({p: file_hash(b) for p, b in files.items()})

    @classmethod
    def from_items(cls, items: Iterable[Mapping[str, Any]]) -> Tree:
        return cls({i["path"]: i["sha256"] for i in items})

    @classmethod
    def from_dir(cls, root: str | os.PathLike) -> Tree:
        """Hash a local directory. Empty directories are ignored. Symbolic
        links are rejected, because the server tree cannot hold them."""
        root = Path(root)
        files: dict[str, str] = {}
        for dirpath, dirnames, filenames in os.walk(root):
            base = Path(dirpath)
            for name in dirnames + filenames:
                if (base / name).is_symlink():
                    raise BundleHashError(f"symbolic link in bundle: {base / name}")
            for name in filenames:
                rel = (base / name).relative_to(root).as_posix()
                files[rel] = file_hash_path(base / name)
        return cls(files)

    # structure -----------------------------------------------------------

    def _check_conflicts(self) -> None:
        for path in self.files:
            parts = path.split("/")
            for i in range(1, len(parts)):
                if "/".join(parts[:i]) in self.files:
                    raise BundleHashError(f"path is both a file and a directory: {'/'.join(parts[:i])}")

    def _build(self) -> dict[str, dict[str, tuple[str, str | None]]]:
        if self._nodes is None:
            nodes: dict[str, dict[str, tuple[str, str | None]]] = {"": {}}
            for path, hex_ in self.files.items():
                parts = path.split("/")
                for i in range(len(parts) - 1):
                    parent = "/".join(parts[:i])
                    here = "/".join(parts[: i + 1])
                    nodes.setdefault(parent, {})[parts[i]] = ("d", None)
                    nodes.setdefault(here, {})
                nodes.setdefault("/".join(parts[:-1]), {})[parts[-1]] = ("f", hex_)
            self._nodes = nodes
        return self._nodes

    def is_dir(self, path: str) -> bool:
        return path.strip("/") in self._build()

    def children(self, dirpath: str) -> list[tuple[str, str, str]]:
        """``(name, kind, hex)`` of the children of a directory ("" = root)."""
        dirpath = dirpath.strip("/")
        node = self._build().get(dirpath)
        if node is None:
            return []
        out = []
        for name, (kind, hex_) in node.items():
            if kind == "d":
                hex_ = self.dir_hash(f"{dirpath}/{name}" if dirpath else name)
            out.append((name, kind, hex_))
        return out

    def dir_hash(self, dirpath: str = "") -> str:
        """Hex dir hash. A missing directory gives sha256("")."""
        dirpath = dirpath.strip("/")
        if dirpath not in self._dir_cache:
            self._dir_cache[dirpath] = sha256_hex(dir_preimage(self.children(dirpath)))
        return self._dir_cache[dirpath]

    @property
    def root_hash(self) -> str:
        return self.dir_hash("")

    def prefix_hash(self, item: str) -> str:
        """Hex hash of one validated ``uses`` item."""
        check_uses_item(item)
        if item == "/":
            return self.root_hash
        if item.endswith("/"):
            return self.dir_hash(item[:-1])
        parent, _, last = item.rpartition("/")
        stem = last[:-1]
        selected = [c for c in self.children(parent) if c[0].startswith(stem)]
        return sha256_hex(dir_preimage(selected))

    def select(self, item: str) -> list[str]:
        """File paths covered by one ``uses`` item, sorted."""
        return sorted(p for p in self.files if path_in_uses(p, item))


def path_in_uses(path: str, item: str) -> bool:
    """True when the file ``path`` is inside the ``uses`` item. Every item
    reduces to a plain string prefix: ``/`` -> "", ``a/`` -> "a/",
    ``a/mug-*`` -> "a/mug-"."""
    return path.startswith(uses_string_prefix(item))


def uses_string_prefix(item: str) -> str:
    check_uses_item(item)
    if item == "/":
        return ""
    return item.removesuffix("*")


# --------------------------------------------------------------------------
# uses items and refs

_REF_RE = re.compile(
    r"^(?P<ns>[^\s/@#]+)/(?P<name>[^\s/@#]+)"
    r"(?:@(?P<version>latest|[1-9][0-9]*))?"
    r"(?:#(?P<entry>[^\s/@#]+))?$"
)


def check_uses_item(item: str) -> str:
    """Validate one ``uses`` item. Raise :class:`BundleHashError` if bad.

    Good: ``/``, ``a/b/``, ``a/b/mug-*``, ``mug-*``, ``*``. Bad: ``**``,
    a ``*`` that is not the last character, ``?``, ``[``, absolute paths,
    ``..``, ``.``, empty segments, and a path that has neither a trailing
    ``/`` nor a trailing ``*``.
    """
    if not isinstance(item, str) or not item:
        raise BundleHashError(f"bad uses item {item!r}")
    if item == "/":
        return item
    if item.startswith("/"):
        raise BundleHashError(f"uses item must be relative: {item!r}")
    if "?" in item or "[" in item or "\\" in item or "\0" in item:
        raise BundleHashError(f"uses item has a bad character: {item!r}")
    if "**" in item:
        raise BundleHashError(f"recursive wildcard is not supported: {item!r}")
    if "*" in item[:-1]:
        raise BundleHashError(f"'*' is allowed only at the end: {item!r}")
    if not (item.endswith("/") or item.endswith("*")):
        raise BundleHashError(f"uses item must end with '/' or '*': {item!r}")
    body = item[:-1]
    if item.endswith("*"):
        parent, _, _stem = body.rpartition("/")
        segments = parent.split("/") if parent else []
    else:
        segments = body.split("/")
    if any(s in ("", ".", "..") for s in segments):
        raise BundleHashError(f"bad path segment in uses item: {item!r}")
    return item


@dataclass(frozen=True)
class Ref:
    """``ns/name[@version][#entry]``, or a local ref ``#entry`` (an entry of
    the same bundle version). ``version`` is an int, "latest" or None."""

    namespace: str | None
    name: str | None
    version: int | str | None = None
    entry: str | None = None

    @property
    def local(self) -> bool:
        return self.namespace is None

    @property
    def bundle(self) -> str | None:
        return None if self.local else f"{self.namespace}/{self.name}"

    def __str__(self) -> str:
        if self.local:
            return f"#{self.entry}"
        out = self.bundle
        if self.version is not None:
            out += f"@{self.version}"
        if self.entry is not None:
            out += f"#{self.entry}"
        return out

    @property
    def pinned(self) -> bool:
        return isinstance(self.version, int)


_LOCAL_REF_RE = re.compile(r"^#(?P<entry>[^\s/@#]+)$")


def parse_ref(text: str) -> Ref:
    """Parse ``ns/name[@N|@latest][#entry]`` (a leading ``bundle:`` is
    allowed) or a local ref ``#entry``."""
    if not isinstance(text, str):
        raise BundleHashError(f"bad bundle ref {text!r}")
    local = _LOCAL_REF_RE.match(text)
    if local:
        return Ref(None, None, None, local.group("entry"))
    raw = text.removeprefix("bundle:")
    m = _REF_RE.match(raw)
    if not m:
        raise BundleHashError(f"bad bundle ref {text!r} (want ns/name[@N][#entry])")
    version: int | str | None = m.group("version")
    if version is not None and version != "latest":
        version = int(version)
    return Ref(m.group("ns"), m.group("name"), version, m.group("entry"))


def canonical_ref(bundle: str, version: int, entry: str) -> str:
    """``ns/bundle@N#entry``, with N the displayed (normalized) version."""
    ref = parse_ref(f"{bundle}@{int(version)}#{entry}")
    return str(ref)


# --------------------------------------------------------------------------
# Manifests (JSON, contract v1.3)

MANIFEST_DIR = "entries/"
MANIFEST_SUFFIX = ".json"
BUNDLE_MANIFEST = "bundle.json"


def manifest_path(entry: str) -> str:
    return f"{MANIFEST_DIR}{entry}{MANIFEST_SUFFIX}"


def load_manifest(data: str | bytes) -> dict:
    """Parse a manifest. Stored manifests are plain JSON; source manifests
    may have comments (see :mod:`.jsonc`), so this parser accepts both. The
    hashes never use the parsed value; they use the stored bytes."""
    try:
        value = jsonc.loads(data)
    except jsonc.JsoncError as e:
        raise BundleHashError(f"manifest is not valid JSON: {e}") from e
    if not isinstance(value, dict):
        raise BundleHashError("manifest must be a JSON object")
    return value


def is_stored_form(data: str | bytes) -> bool:
    """True when the bytes are a manifest in the stored form (contract
    v1.6: ``JSON.stringify(sortKeys(value), null, 2) + "\\n"``)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    try:
        return jsonc.to_stored(data) == data
    except jsonc.JsoncError:
        return False


# --------------------------------------------------------------------------
# Effective hash


def manifest_uses(manifest: Mapping[str, Any]) -> list[str]:
    """The validated ``uses`` list. Default ``["/"]``."""
    uses = manifest.get("uses", ["/"])
    if uses is None:
        uses = ["/"]
    if not isinstance(uses, list) or not all(isinstance(u, str) for u in uses):
        raise BundleHashError(f"uses must be a list of strings, got {uses!r}")
    for item in uses:
        check_uses_item(item)
    return list(uses)


def manifest_refs(manifest: Mapping[str, Any]) -> list[Ref]:
    """The parsed ``refs`` list. Each item is ``ns/bundle@N#entry`` (or
    ``@latest`` in authoring), or a local ref ``#entry``."""
    refs = manifest.get("refs") or []
    if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
        raise BundleHashError(f"refs must be a list of strings, got {refs!r}")
    out = []
    for raw in refs:
        ref = parse_ref(raw)
        if not ref.local and (ref.entry is None or ref.version is None):
            raise BundleHashError(f"ref needs a version and an entry: {raw!r}")
        out.append(ref)
    return out


def effective_preimage(
    manifest_bytes: bytes,
    tree: Tree,
    ref_hashes: Mapping[str, str] | None = None,
) -> bytes:
    """Bytes hashed for an entry.

    ``manifest_bytes`` is the raw manifest file. ``ref_hashes`` maps each
    canonical ref string (``ns/b@N#e``, N the displayed version) to the
    effective hash of that entry.
    """
    if isinstance(manifest_bytes, str):
        manifest_bytes = manifest_bytes.encode("utf-8")
    manifest = load_manifest(manifest_bytes)
    out = [b"entry\0", file_hash(manifest_bytes).encode(), b"\n"]
    for item in manifest_uses(manifest):
        out.append(item.encode("utf-8") + b"\0" + tree.prefix_hash(item).encode() + b"\n")
    ref_hashes = ref_hashes or {}
    for ref in sorted(ref_hashes, key=lambda r: r.encode("utf-8")):
        out.append(ref.encode("utf-8") + b"\0" + to_hex(ref_hashes[ref]).encode() + b"\n")
    return b"".join(out)


def effective_hash(
    manifest_bytes: bytes,
    tree: Tree,
    ref_hashes: Mapping[str, str] | None = None,
) -> str:
    """Hex effective hash of an entry. See :func:`effective_preimage`."""
    return sha256_hex(effective_preimage(manifest_bytes, tree, ref_hashes))


# --------------------------------------------------------------------------
# Versions


def displayed_version(history: Sequence[tuple[int, Mapping[str, str]]], version: int, entry: str) -> int:
    """Displayed version of ``entry`` at bundle ``version``.

    ``history`` lists ``(version, {entry: effective hash})`` for every stored
    version of one bundle, in any order. Gaps in the version numbers do not
    break a run: only stored versions are compared.
    """
    ordered = sorted(history, key=lambda h: h[0])
    index = next((i for i, (v, _) in enumerate(ordered) if v == version), None)
    if index is None:
        raise BundleHashError(f"no version {version} in history")
    current = ordered[index][1].get(entry)
    if current is None:
        raise BundleHashError(f"entry {entry!r} does not exist at version {version}")
    current = to_hex(current)
    shown = version
    for v, entries in reversed(ordered[:index]):
        h = entries.get(entry)
        if h is None or to_hex(h) != current:
            break
        shown = v
    return shown


@dataclass
class EntryResult:
    name: str
    effective_hash: str
    displayed_version: int
    refs: dict[str, str]  # canonical ref -> hex


@dataclass
class VersionResult:
    version: int
    root_hash: str
    entries: dict[str, EntryResult]


def entry_manifests(tree_paths: Iterable[str]) -> dict[str, str]:
    """Entry name -> manifest path, for every ``entries/<name>.json``."""
    out = {}
    for path in tree_paths:
        m = re.match(r"^entries/([^/]+)\.json$", path)
        if m:
            out[m.group(1)] = path
    return out


def compute_version(
    files: Mapping[str, bytes],
    resolve_ref: Callable[[Ref], tuple[str, str]] | None = None,
) -> tuple[str, dict[str, tuple[str, dict[str, str]]]]:
    """Root hash and effective hashes of all entries of one bundle version.

    ``files`` holds all files of the version (path -> bytes), including
    ``bundle.json`` and ``entries/*.json``. ``resolve_ref(ref)`` returns
    ``(canonical ref, effective hash)`` for one cross-bundle ref; the server
    pins ``@latest`` and normalizes the version to the displayed version.
    A local ref ``#e`` uses the canonical string ``#e`` and the effective
    hash of entry ``e`` in this version. A local ref cycle is an error.
    Returns ``(root hex, {entry: (effective hex, {canonical ref: hex})})``.
    """
    tree = Tree.from_files(files)
    paths = entry_manifests(files)
    out: dict[str, tuple[str, dict[str, str]]] = {}
    active: list[str] = []

    def visit(name: str) -> str:
        if name in out:
            return out[name][0]
        if name in active:
            raise BundleHashError(f"local ref cycle: {' -> '.join([*active, name])}")
        if name not in paths:
            raise BundleHashError(f"local ref to a missing entry: #{name}")
        active.append(name)
        raw = files[paths[name]]
        ref_hashes: dict[str, str] = {}
        for ref in manifest_refs(load_manifest(raw)):
            if ref.local:
                ref_hashes[str(ref)] = visit(ref.entry)
                continue
            if resolve_ref is None:
                raise BundleHashError(f"no resolver for ref {ref}")
            canonical, hex_ = resolve_ref(ref)
            ref_hashes[canonical] = to_hex(hex_)
        active.pop()
        out[name] = (effective_hash(raw, tree, ref_hashes), ref_hashes)
        return out[name][0]

    for name in sorted(paths):
        visit(name)
    return tree.root_hash, dict(sorted(out.items()))


def compute_history(
    versions: Sequence[tuple[int, Mapping[str, bytes]]],
    resolve_ref: Callable[[Ref], tuple[str, str]] | None = None,
) -> list[VersionResult]:
    """Hashes and displayed versions for every version of one bundle."""
    raw = []
    for version, files in sorted(versions, key=lambda v: v[0]):
        root, entries = compute_version(files, resolve_ref)
        raw.append((version, root, entries))
    history = [(v, {n: e[0] for n, e in entries.items()}) for v, _, entries in raw]
    return [
        VersionResult(version, root, {
            name: EntryResult(name, eff, displayed_version(history, version, name), refs)
            for name, (eff, refs) in entries.items()
        })
        for version, root, entries in raw
    ]
