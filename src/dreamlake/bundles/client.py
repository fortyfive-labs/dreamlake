"""Bundle client: resolve, lock, fetch and check (bundle contract v1).

Delivery rules:

* ``fetch`` downloads only the ``uses`` prefixes of an entry, its manifest,
  ``bundle.json`` and the closure of its ``refs``. It does not parse files.
* Blobs are stored once by sha256 in ``<cache>/blobs/<hex>`` (read-only).
* Each entry is materialized once per effective hash in
  ``<cache>/views/<hex>/``. Files are hard links to blobs (or copies when a
  hard link is not possible). ``<cache>/views/<hex>.json`` records the view
  and is written last; it marks a complete view.
* Before it writes a view, ``fetch`` computes the effective hash from the
  downloaded manifest, the tree listing and the ref hashes, and compares it
  with the server value.

The default cache is ``~/.cache/dreamlake/bundles`` (``DREAMLAKE_BUNDLE_CACHE``
overrides it).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from . import hashing as h
from . import jsonc

LOCK_FILE = "dreamlake.lock"
LOCK_SCHEMA = "dreamlake.lock/v1"
PRESIGN_BATCH = 500


class BundleError(RuntimeError):
    """A bundle API, integrity or delivery failure."""

    def __init__(self, code: str, message: str | None = None, status: int | None = None):
        self.code, self.status = code, status
        super().__init__(message or code)


def default_cache_dir() -> Path:
    env = os.environ.get("DREAMLAKE_BUNDLE_CACHE")
    if env:
        return Path(env)
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "dreamlake" / "bundles"


def _seg(value: str) -> str:
    if not isinstance(value, str) or not value or value in (".", ".."):
        raise ValueError(f"bad path segment {value!r}")
    return quote(value, safe="")


def _wire(value: str | None) -> str | None:
    return None if value is None else h.to_wire(value)


@dataclass(frozen=True)
class Resolved:
    """A resolved ref. ``version`` is the bundle version that the ref names
    (``@latest`` resolved); ``displayed_version`` is the version where the
    entry last changed. Hashes are in the wire form ``sha256:<hex>``.
    ``effective_hash`` and ``displayed_version`` are None for a whole-bundle
    ref (no ``#entry``)."""

    bundle: str
    version: int
    entry: str | None
    effective_hash: str | None
    displayed_version: int | None
    root_hash: str
    uses: tuple[str, ...] = ("/",)
    refs: tuple[dict, ...] = ()
    manifest: dict | None = field(default=None, compare=False)

    @property
    def ref(self) -> str:
        """Normalized ref: ``ns/b@W#e`` (W the displayed version)."""
        if self.entry is None:
            return f"{self.bundle}@{self.version}"
        return h.canonical_ref(self.bundle, self.displayed_version, self.entry)


@dataclass
class CheckReport:
    """Result of :func:`check`. ``missing`` lists files that the model needs
    and that are not in the declared prefixes; ``undeclared`` is the subset
    that exists elsewhere in the bundle (add a ``uses`` prefix for them)."""

    target: str
    root: Path
    effective_hash: str | None = None
    files: int = 0
    missing: list[str] = field(default_factory=list)
    undeclared: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    smoke_exit: int | None = None

    @property
    def ok(self) -> bool:
        return not self.missing and not self.errors and self.smoke_exit in (None, 0)

    def to_json(self) -> dict[str, Any]:
        return {"target": self.target, "ok": self.ok, "root": str(self.root),
                "effectiveHash": self.effective_hash, "files": self.files,
                "missing": self.missing, "undeclared": self.undeclared,
                "errors": self.errors, "smokeExit": self.smoke_exit}


class Bundles:
    """Client for the bundle REST API with a local content-addressed cache.

    ``client`` is a :class:`dreamlake.api._client.DreamLakeClient` (default:
    a new one from the environment). ``storage_transport`` is for tests: it
    serves the signed blob URLs. Signed URLs never get the DreamLake token.
    """

    def __init__(self, client=None, *, cache_dir: str | os.PathLike | None = None,
                 storage_transport: httpx.BaseTransport | None = None):
        if client is None:
            from dreamlake.api._client import DreamLakeClient
            client = DreamLakeClient()
        self.client = client
        self.cache_dir = Path(cache_dir) if cache_dir is not None else default_cache_dir()
        self._storage_transport = storage_transport

    # ------------------------------------------------------------------ REST

    def _request(self, method: str, path: str, **kwargs) -> Any:
        with self.client.http() as http:
            response = http.request(method, path, **kwargs)
        if not response.is_success:
            try:
                body = response.json()
                detail = body.get("error") or body.get("message") if isinstance(body, dict) else None
            except ValueError:
                detail = None
            code = "not_found" if response.status_code == 404 else "bundle_request_failed"
            raise BundleError(code, f"{method} {path}: {response.status_code} {detail or ''}".strip(),
                              response.status_code)
        try:
            return response.json()
        except ValueError as e:
            raise BundleError("invalid_response", f"{method} {path}: not JSON") from e

    @staticmethod
    def _base(bundle: str) -> str:
        ns, _, name = bundle.partition("/")
        return f"/namespaces/{_seg(ns)}/bundles/{_seg(name)}"

    def list(self, namespace: str, *, kind: str | None = None, tag: str | None = None,
             q: str | None = None, cursor: str | None = None) -> dict:
        params = {k: v for k, v in dict(kind=kind, tag=tag, q=q, cursor=cursor).items() if v is not None}
        return self._request("GET", f"/namespaces/{_seg(namespace)}/bundles", params=params)

    def detail(self, bundle: str) -> dict:
        return self._request("GET", self._base(bundle))

    def versions(self, bundle: str) -> Any:
        return self._request("GET", self._base(bundle) + "/versions")

    def version(self, bundle: str, version: int) -> dict:
        return self._request("GET", f"{self._base(bundle)}/versions/{int(version)}")

    def entry(self, bundle: str, entry: str, version: int | None = None) -> dict:
        params = {"version": int(version)} if version is not None else {}
        return self._request("GET", f"{self._base(bundle)}/entries/{_seg(entry)}", params=params)

    def entry_versions(self, bundle: str, entry: str) -> Any:
        return self._request("GET", f"{self._base(bundle)}/entries/{_seg(entry)}/versions")

    def search(self, q: str, *, kind: str | None = None, ns: str | None = None) -> dict:
        params = {k: v for k, v in dict(q=q, kind=kind, ns=ns).items() if v is not None}
        return self._request("GET", "/bundle-search", params=params)

    def tree(self, bundle: str, version: int, prefix: str = "") -> Iterator[dict]:
        """All tree items under a string prefix. Follows ``nextCursor``."""
        cursor = None
        seen: set[str] = set()
        while True:
            params = {"prefix": prefix}
            if cursor:
                params["cursor"] = cursor
            page = self._request("GET", f"{self._base(bundle)}/versions/{int(version)}/tree", params=params)
            for item in page.get("items", []):
                yield {"path": h.check_path(item["path"]), "sha256": h.to_hex(item["sha256"]),
                       "size": int(item.get("size", 0))}
            cursor = page.get("nextCursor")
            if not cursor:
                return
            if cursor in seen:
                raise BundleError("invalid_cursor", "tree cursor repeats")
            seen.add(cursor)

    def presign(self, bundle: str, version: int, *, paths: Sequence[str] = (),
                prefixes: Sequence[str] = ()) -> list[dict]:
        body: dict[str, Any] = {}
        if paths:
            body["paths"] = list(paths)
        if prefixes:
            body["prefixes"] = list(prefixes)
        out = self._request("POST", f"{self._base(bundle)}/versions/{int(version)}/presign", json=body)
        return [{"path": u["path"], "sha256": h.to_hex(u["sha256"]), "url": u["url"]}
                for u in out.get("urls", [])]

    # --------------------------------------------------------------- resolve

    def resolve(self, ref: str | h.Ref) -> Resolved:
        """Resolve ``ns/name[@N|@latest][#entry]`` to a pinned version."""
        r = h.parse_ref(ref) if isinstance(ref, str) else ref
        version = r.version
        if not isinstance(version, int):
            detail = self.detail(r.bundle)
            version = detail.get("latestVersion")
            if not isinstance(version, int):
                raise BundleError("no_versions", f"{r.bundle} has no versions")
        if r.entry is None:
            info = self.version(r.bundle, version)
            return Resolved(r.bundle, version, None, None, None, h.to_wire(info["rootHash"]))
        e = self.entry(r.bundle, r.entry, version)
        root = e.get("rootHash")
        if root is None:
            root = self.version(r.bundle, version)["rootHash"]
        manifest = e.get("manifest")
        uses = e.get("uses")
        if uses is None:
            uses = h.manifest_uses(manifest or {})
        return Resolved(
            bundle=r.bundle, version=int(e.get("version", version)), entry=r.entry,
            effective_hash=h.to_wire(e["effectiveHash"]),
            displayed_version=int(e["displayedVersion"]), root_hash=h.to_wire(root),
            uses=tuple(uses), refs=tuple(e.get("refs") or ()),
            manifest=manifest if isinstance(manifest, dict) else None,
        )

    # ------------------------------------------------------------------ lock

    def lock(self, refs: Iterable[str], path: str | os.PathLike = LOCK_FILE) -> Path:
        """Resolve refs and write them to a lock file (plain JSON).

        Each record: ``ref`` (as given), ``resolved`` (``ns/b@W#e``),
        ``bundle``, ``entry``, ``version`` (W, the normalized version),
        ``effectiveHash`` and ``rootHash`` (of version W). Records for other
        refs already in the file are kept.
        """
        path = Path(path)
        records: dict[str, dict] = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("schema") != LOCK_SCHEMA:
                raise BundleError("bad_lock_file", f"{path}: schema is not {LOCK_SCHEMA}")
            records = {rec["ref"]: rec for rec in data.get("bundles", [])}
        for ref in refs:
            res = self.resolve(ref)
            if res.entry is None:
                records[ref] = {"ref": ref, "resolved": res.ref, "bundle": res.bundle, "entry": None,
                                "version": res.version, "effectiveHash": None, "rootHash": res.root_hash}
                continue
            root = res.root_hash
            if res.displayed_version != res.version:
                root = h.to_wire(self.version(res.bundle, res.displayed_version)["rootHash"])
            records[ref] = {"ref": ref, "resolved": res.ref, "bundle": res.bundle, "entry": res.entry,
                            "version": res.displayed_version, "effectiveHash": res.effective_hash,
                            "rootHash": root}
        data = {"schema": LOCK_SCHEMA, "bundles": [records[k] for k in sorted(records)]}
        path.write_text(jsonc.stored_form(data), encoding="utf-8")
        return path

    # ----------------------------------------------------------------- fetch

    def _blob(self, hex_: str) -> Path:
        return self.cache_dir / "blobs" / hex_

    def _storage(self) -> httpx.Client:
        return httpx.Client(timeout=60, follow_redirects=False, transport=self._storage_transport)

    def _download(self, bundle: str, version: int, items: Sequence[dict]) -> None:
        """Put the blobs of ``items`` into the blob cache (verified)."""
        need: dict[str, dict] = {}
        for item in items:
            if not self._blob(item["sha256"]).is_file():
                need.setdefault(item["sha256"], item)
        if not need:
            return
        blobs = self.cache_dir / "blobs"
        blobs.mkdir(parents=True, exist_ok=True)
        wanted = list(need.values())
        with self._storage() as storage:
            for start in range(0, len(wanted), PRESIGN_BATCH):
                batch = wanted[start:start + PRESIGN_BATCH]
                urls = {u["path"]: u for u in self.presign(bundle, version, paths=[i["path"] for i in batch])}
                for item in batch:
                    signed = urls.get(item["path"])
                    if signed is None:
                        raise BundleError("presign_missing", f"no signed URL for {item['path']}")
                    if signed["sha256"] != item["sha256"]:
                        raise BundleError("hash_mismatch", f"{item['path']}: tree and presign disagree")
                    self._get_blob(storage, signed["url"], item["sha256"], item["path"])

    def _get_blob(self, storage: httpx.Client, url: str, hex_: str, label: str) -> None:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.username or parts.password:
            raise BundleError("unsafe_download_url", f"{label}: signed URL must be https")
        target = self._blob(hex_)
        fd, tmp = tempfile.mkstemp(prefix=".dl-", dir=target.parent)
        try:
            digest = hashlib.sha256()
            with os.fdopen(fd, "wb") as out, storage.stream("GET", url) as response:
                if not response.is_success:
                    raise BundleError("download_failed", f"{label}: {response.status_code}", response.status_code)
                for chunk in response.iter_bytes():
                    digest.update(chunk)
                    out.write(chunk)
            if digest.hexdigest() != hex_:
                raise BundleError("integrity_failed", f"{label}: sha256 does not match")
            os.chmod(tmp, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    @staticmethod
    def _place(blob: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            target.unlink()
        try:
            os.link(blob, target)
        except OSError:
            shutil.copyfile(blob, target)
            os.chmod(target, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

    def _selection(self, res: Resolved) -> list[dict]:
        """Tree items of the declared prefixes plus the manifests."""
        chosen: dict[str, dict] = {}
        for item in res.uses:
            h.check_uses_item(item)
            if item == "/":
                server_prefix = ""
            elif item.endswith("/"):
                server_prefix = item
            else:  # name prefix: list the parent directory, filter here
                parent = item.rpartition("/")[0]
                server_prefix = parent + "/" if parent else ""
            for t in self.tree(res.bundle, res.version, server_prefix):
                if h.path_in_uses(t["path"], item):
                    chosen[t["path"]] = t
        extra = ["bundle.json"] + ([h.manifest_path(res.entry)] if res.entry else [])
        missing = [p for p in extra if p not in chosen]
        if missing:
            for t in self.presign(res.bundle, res.version, paths=missing):
                chosen[t["path"]] = {"path": h.check_path(t["path"]), "sha256": t["sha256"], "size": None}
        return sorted(chosen.values(), key=lambda t: t["path"])

    def _verify(self, res: Resolved, items: list[dict]) -> None:
        if res.entry is None:
            return
        mpath = h.manifest_path(res.entry)
        manifest_item = next((t for t in items if t["path"] == mpath), None)
        if manifest_item is None:
            raise BundleError("manifest_missing", f"{mpath} is not in {res.bundle}@{res.version}")
        raw = self._blob(manifest_item["sha256"]).read_bytes()
        declared = [i for i in res.uses]
        if h.manifest_uses(h.load_manifest(raw)) != declared:
            raise BundleError("uses_mismatch", f"{res.bundle}#{res.entry}: server uses differ from manifest")
        selected = {t["path"]: t["sha256"] for t in items if any(h.path_in_uses(t["path"], u) for u in declared)}
        ref_hashes = {}
        for r in res.refs:
            ref_hashes[_ref_hash_key(r)] = h.to_hex(r["effectiveHash"])
        local = h.effective_hash(raw, h.Tree(selected), ref_hashes)
        if local != h.to_hex(res.effective_hash):
            raise BundleError("effective_hash_mismatch",
                              f"{res.ref}: local effective hash sha256:{local} != server {res.effective_hash}")

    def _view_key(self, res: Resolved) -> str:
        return h.to_hex(res.effective_hash) if res.entry else "root-" + h.to_hex(res.root_hash)

    def fetch(self, ref: str | Resolved, dest: str | os.PathLike | None = None, lazy: bool = False) -> Path:
        """Materialize an entry (declared prefixes only) and return its path.

        Without ``dest`` the result is the shared cache view
        (``<cache>/views/<effective hex>``); do not write into it. With
        ``dest`` the view is copied (hard links where possible) into that
        directory. Refs are fetched recursively into their own views; the
        view record (``views/<hex>.json``) lists them.

        ``lazy=True`` downloads only the manifests and the tree index. Get
        files later with :meth:`ensure`; a later non-lazy fetch completes
        the view.
        """
        res = ref if isinstance(ref, Resolved) else self.resolve(ref)
        views = self.cache_dir / "views"
        key = self._view_key(res)
        view, record_path = views / key, views / f"{key}.json"
        record = json.loads(record_path.read_text()) if record_path.is_file() else None
        if record is None or (record.get("lazy") and not lazy):
            record = self._build_view(res, view, record_path, lazy)
        if dest is None:
            return view
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        for item in record["files"]:
            src = view / item["path"]
            if src.is_file():
                self._place(src, dest.joinpath(*PurePosixPath(item["path"]).parts))
        return dest

    def _build_view(self, res: Resolved, view: Path, record_path: Path, lazy: bool) -> dict:
        items = self._selection(res)
        manifests = [t for t in items if t["path"] == "bundle.json"
                     or (res.entry and t["path"] == h.manifest_path(res.entry))]
        self._download(res.bundle, res.version, items if not lazy else manifests)
        self._verify(res, items)
        ref_views = {}
        by_path = {t["path"]: t for t in items}
        for r in res.refs:
            sub_view = self.fetch(_ref_target(r, res), lazy=lazy)
            ref_views[_ref_hash_key(r)] = str(sub_view)
            if str(r.get("ref", "")).startswith("#"):
                # A local ref is in the same bundle version: its files join
                # this view (the closure of the entry in one tree).
                sub = json.loads((sub_view.parent / f"{sub_view.name}.json").read_text())
                for f in sub["files"]:
                    by_path.setdefault(f["path"], {"path": f["path"], "sha256": h.to_hex(f["sha256"]),
                                                   "size": f["size"]})
        items = sorted(by_path.values(), key=lambda t: t["path"])
        views = view.parent
        views.mkdir(parents=True, exist_ok=True)
        if lazy:
            view.mkdir(parents=True, exist_ok=True)
            for t in manifests:
                self._place(self._blob(t["sha256"]), view / t["path"])
        else:
            tmp = Path(tempfile.mkdtemp(prefix=".tmp-", dir=views))
            try:
                for t in items:
                    self._place(self._blob(t["sha256"]), tmp.joinpath(*PurePosixPath(t["path"]).parts))
                if view.exists():
                    shutil.rmtree(view)  # an old lazy view
                os.replace(tmp, view)
            finally:
                if tmp.exists():
                    shutil.rmtree(tmp)
        record = {
            "ref": res.ref, "bundle": res.bundle, "version": res.version, "entry": res.entry,
            "effectiveHash": res.effective_hash, "rootHash": res.root_hash, "lazy": lazy,
            "files": [{"path": t["path"], "sha256": h.to_wire(t["sha256"]), "size": t["size"]} for t in items],
            "refs": ref_views,
        }
        fd, tmp_record = tempfile.mkstemp(prefix=".rec-", dir=views)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(jsonc.stored_form(record))
        os.replace(tmp_record, record_path)
        return record

    def ensure(self, view: str | os.PathLike, *paths: str) -> list[Path]:
        """Download files of a lazy view on demand. With no paths, download
        all files of the view. Returns the local paths."""
        view = Path(view)
        record_path = view.parent / f"{view.name}.json"
        record = json.loads(record_path.read_text())
        index = {f["path"]: f for f in record["files"]}
        wanted = list(paths) or list(index)
        unknown = [p for p in wanted if p not in index]
        if unknown:
            raise BundleError("not_declared", f"not in the declared prefixes: {unknown}")
        items = [{"path": p, "sha256": h.to_hex(index[p]["sha256"]), "size": index[p]["size"]} for p in wanted]
        self._download(record["bundle"], record["version"], items)
        out = []
        for item in items:
            target = view.joinpath(*PurePosixPath(item["path"]).parts)
            if not target.is_file():
                self._place(self._blob(item["sha256"]), target)
            out.append(target)
        return out

    # ----------------------------------------------------------------- check

    def check(self, target: str, smoke: str | Sequence[str] | None = None, *,
              keep: bool = False, compile_model: bool = True) -> CheckReport:
        """Materialize only the declared prefixes in a temporary directory,
        then look for missing files.

        ``target`` is a ref (``ns/b@N#entry``) or a local source directory
        with an entry (``path/to/bundle#entry``). The check reads the entry
        ``model`` (MJCF or URDF), follows ``file``/``filename`` attributes and
        ``<include>``, and compiles the MJCF with MuJoCo when it is
        installed. ``smoke`` is a command that runs in the directory.
        The directory is deleted unless ``keep`` is True.
        """
        root = Path(tempfile.mkdtemp(prefix="dl-bundle-check-"))
        report = CheckReport(target=target, root=root)
        try:
            local_dir, entry = _split_local(target)
            if local_dir is not None:
                everything = self._check_local(local_dir, entry, root, report)
            else:
                res = self.resolve(target)
                if res.entry is None:
                    raise BundleError("entry_required", "check needs a ref with #entry")
                self.fetch(res, dest=root)
                report.effective_hash = res.effective_hash
                entry = res.entry
                everything = {t["path"] for t in self.tree(res.bundle, res.version, "")}
            manifest = h.load_manifest((root / h.manifest_path(entry)).read_bytes())
            report.files = sum(1 for p in root.rglob("*") if p.is_file())
            model = manifest.get("model")
            if isinstance(model, str):
                for path in _model_files(root, model, report.errors):
                    report.missing.append(path)
                    if path in everything:
                        report.undeclared.append(path)
                if compile_model and model.endswith(".xml") and not report.missing:
                    _compile(root / model, report.errors)
            if smoke:
                result = subprocess.run(smoke, cwd=root, shell=isinstance(smoke, str), check=False)
                report.smoke_exit = result.returncode
        except (BundleError, h.BundleHashError, OSError) as e:
            report.errors.append(f"{type(e).__name__}: {e}")
        finally:
            if not keep:
                shutil.rmtree(root, ignore_errors=True)
        return report

    def _check_local(self, src: Path, entry: str, root: Path, report: CheckReport) -> set[str]:
        """Copy the declared prefixes of a local source bundle into ``root``:
        the ``uses`` of the entry and of its local refs (``#e``), plus the
        manifests in the stored form. Cross-bundle refs are fetched into the
        cache. Sets ``report.effective_hash`` when all refs resolve. Returns
        all paths of the source bundle."""
        all_paths = set()
        for dirpath, dirs, names in os.walk(src):
            dirs[:] = [d for d in dirs if not (Path(dirpath) == src and d == ".git")]
            for name in names:
                all_paths.add((Path(dirpath) / name).relative_to(src).as_posix())
        stored: dict[str, bytes] = {
            p: jsonc.to_stored((src / p).read_bytes())
            for p in all_paths if p == "bundle.json" or re.fullmatch(r"entries/[^/]+\.json", p)}
        files = {p: stored[p] if p in stored else (src / p).read_bytes() for p in all_paths}
        tree = h.Tree.from_files(files)
        effective: dict[str, str | None] = {}
        chosen: set[str] = {"bundle.json"} & all_paths
        active: list[str] = []

        def visit(name: str) -> str | None:
            if name in effective:
                return effective[name]
            if name in active:
                raise h.BundleHashError(f"local ref cycle: {' -> '.join([*active, name])}")
            path = h.manifest_path(name)
            if path not in files:
                raise h.BundleHashError(f"local ref to a missing entry: #{name}")
            active.append(name)
            manifest = h.load_manifest(files[path])
            uses = h.manifest_uses(manifest)
            chosen.add(path)
            chosen.update(p for p in all_paths if any(h.path_in_uses(p, u) for u in uses))
            ref_hashes: dict[str, str] | None = {}
            for ref in h.manifest_refs(manifest):
                if ref.local:
                    sub = visit(ref.entry)
                    if sub is None or ref_hashes is None:
                        ref_hashes = None
                    else:
                        ref_hashes[str(ref)] = sub
                    continue
                try:
                    res = self.resolve(ref)
                    self.fetch(res)
                    if ref_hashes is not None:
                        ref_hashes[res.ref] = res.effective_hash
                except Exception as e:  # noqa: BLE001 - report and continue
                    report.errors.append(f"ref {ref} not resolved: {e}")
                    ref_hashes = None
            active.pop()
            effective[name] = None if ref_hashes is None else h.effective_hash(files[path], tree, ref_hashes)
            return effective[name]

        own = visit(entry)
        report.effective_hash = None if own is None else h.to_wire(own)
        for p in sorted(chosen):
            target = root.joinpath(*PurePosixPath(p).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(files[p])
        return all_paths


def _ref_hash_key(r: dict) -> str:
    """The canonical ref string of a resolved ref (entry GET ``refs[]``):
    ``#entry`` for a local ref, else ``ns/b@W#entry`` (W normalized)."""
    if str(r.get("ref", "")).startswith("#"):
        return f"#{r['entry']}"
    return h.canonical_ref(r["bundle"], int(r["version"]), r["entry"])


def _ref_target(r: dict, parent: Resolved) -> str:
    """A ref to fetch. A local ref names an entry of the parent version."""
    if str(r.get("ref", "")).startswith("#"):
        return f"{r.get('bundle') or parent.bundle}@{r.get('version') or parent.version}#{r['entry']}"
    return h.canonical_ref(r["bundle"], int(r["version"]), r["entry"])


def _split_local(target: str) -> tuple[Path | None, str | None]:
    base, sep, entry = target.rpartition("#")
    if sep and entry and Path(base).is_dir():
        return Path(base), entry
    return None, None


_ATTR_RE = re.compile(r"""\b(file|filename)\s*=\s*("([^"]*)"|'([^']*)')""")
_COMPILER_RE = re.compile(r"<compiler\b[^>]*>")
_DIR_ATTR_RE = re.compile(r"""\b(meshdir|texturedir|assetdir)\s*=\s*("([^"]*)"|'([^']*)')""")


def _model_files(root: Path, model: str, errors: list[str]) -> list[str]:
    """Bundle paths that the model needs and that are not in ``root``.

    A light scan, not a parser: ``file``/``filename`` attributes in the model
    and in included XML files. ``package://`` and URL references are
    skipped. MJCF ``meshdir``/``texturedir``/``assetdir`` are tried.
    """
    missing: list[str] = []
    seen: set[str] = set()
    todo = [model]
    root_resolved = root.resolve()

    def rel(p: Path) -> str | None:
        try:
            return p.resolve().relative_to(root_resolved).as_posix()
        except ValueError:
            return None

    while todo:
        current = todo.pop()
        if current in seen:
            continue
        seen.add(current)
        path = root / current
        if not path.is_file():
            missing.append(current)
            continue
        if path.suffix.lower() not in (".xml", ".urdf", ".mjcf"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        dirs = {}
        for m in _COMPILER_RE.finditer(text):
            for d in _DIR_ATTR_RE.finditer(m.group(0)):
                dirs[d.group(1)] = d.group(3) if d.group(3) is not None else d.group(4)
        base = path.parent
        bases = [base / dirs[k] for k in ("meshdir", "texturedir", "assetdir") if k in dirs] + [base]
        for m in _ATTR_RE.finditer(text):
            value = m.group(3) if m.group(3) is not None else m.group(4)
            if not value or "://" in value:
                continue
            candidates = [b / value for b in ([base] if value.startswith("/") else bases)]
            hit = next((c for c in candidates if c.is_file()), None)
            if hit is not None:
                r = rel(hit)
                if r is not None and hit.suffix.lower() in (".xml", ".urdf"):
                    todo.append(r)
                continue
            r = rel(candidates[0])
            if r is None:
                errors.append(f"{current}: reference leaves the bundle: {value}")
            elif r not in missing:
                missing.append(r)
    return sorted(set(missing))


def _compile(model: Path, errors: list[str]) -> None:
    try:
        import mujoco  # type: ignore
    except ImportError:
        return
    try:
        mujoco.MjModel.from_xml_path(str(model))
    except Exception as e:  # noqa: BLE001 - any compile error is a finding
        errors.append(f"mujoco compile: {e}")


# ---------------------------------------------------------------------------
# Module-level helpers with a default client


_default: Bundles | None = None


def _bundles(bundles: Bundles | None) -> Bundles:
    global _default
    if bundles is not None:
        return bundles
    if _default is None:
        _default = Bundles()
    return _default


def resolve(ref: str, *, bundles: Bundles | None = None) -> Resolved:
    """Resolve ``ns/name[@N|@latest][#entry]``. See :meth:`Bundles.resolve`."""
    return _bundles(bundles).resolve(ref)


def lock(refs: Iterable[str], path: str | os.PathLike = LOCK_FILE, *, bundles: Bundles | None = None) -> Path:
    """Write ``dreamlake.lock``. See :meth:`Bundles.lock`."""
    return _bundles(bundles).lock(refs, path)


def fetch(ref: str, dest: str | os.PathLike | None = None, lazy: bool = False, *,
          bundles: Bundles | None = None) -> Path:
    """Materialize an entry. See :meth:`Bundles.fetch`."""
    return _bundles(bundles).fetch(ref, dest, lazy)


def check(target: str, smoke: str | Sequence[str] | None = None, *, keep: bool = False,
          bundles: Bundles | None = None) -> CheckReport:
    """Check declared prefixes. See :meth:`Bundles.check`."""
    return _bundles(bundles).check(target, smoke, keep=keep)
