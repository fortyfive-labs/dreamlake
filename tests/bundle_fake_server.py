"""An in-memory bundle server for tests (the REST shapes of contract v1.1).

It loads the corpus (``<ns>/<bundle>/v<N>/``) and computes hashes with the
reference implementation, as the real server does.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote

import httpx

from dreamlake.bundles import hashing as h
from dreamlake.bundles.vectors import CorpusResolver, load_corpus

CORPUS = Path(__file__).parent / "fixtures" / "bundles" / "client-corpus"
SHARED_CORPUS = Path(__file__).parent / "fixtures" / "bundles" / "corpus"
STORAGE = "https://storage.test/blobs/"


class FakeBundleServer:
    def __init__(self, corpus: Path = CORPUS, page_size: int = 3):
        self.corpus = load_corpus(corpus)
        self.resolver = CorpusResolver(self.corpus)
        self.page_size = page_size
        self.blobs: dict[str, bytes] = {}
        for versions in self.corpus.values():
            for _, files in versions:
                for data in files.values():
                    self.blobs[h.file_hash(data)] = data
        self.requests: list[str] = []
        self.blob_gets: list[str] = []
        self.tamper: dict[str, bytes] = {}  # hex -> bytes served instead

    # helpers -------------------------------------------------------------

    def files(self, bundle: str, version: int) -> dict[str, bytes]:
        return dict(self.corpus[bundle])[version]

    def result(self, bundle: str, version: int) -> h.VersionResult:
        return next(v for v in self.resolver.history(bundle) if v.version == version)

    def _entry_json(self, bundle: str, version: int, name: str) -> dict:
        res = self.result(bundle, version)
        entry = res.entries[name]
        raw = self.files(bundle, version)[h.manifest_path(name)]
        manifest = h.load_manifest(raw)
        refs = []
        for canonical, hex_ in entry.refs.items():
            r = h.parse_ref(canonical)
            refs.append({"ref": canonical, "bundle": r.bundle or bundle,
                         "version": version if r.local else r.version,
                         "entry": r.entry, "effectiveHash": h.to_wire(hex_)})
        return {"name": name, "kind": manifest.get("kind"), "manifest": manifest,
                "effectiveHash": h.to_wire(entry.effective_hash),
                "displayedVersion": entry.displayed_version, "version": version,
                "uses": h.manifest_uses(manifest), "refs": refs}

    # transports ----------------------------------------------------------

    def api(self, request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.path)
        self.requests.append(f"{request.method} {path}")
        m = re.fullmatch(r"/namespaces/([^/]+)/bundles/([^/]+)(/.*)?", path)
        if not m:
            return httpx.Response(404, json={"error": "not found"})
        bundle, rest = f"{m.group(1)}/{m.group(2)}", m.group(3) or ""
        if bundle not in self.corpus:
            return httpx.Response(404, json={"error": "bundle not found"})
        versions = dict(self.corpus[bundle])
        latest = max(versions)
        params = request.url.params
        if rest == "":
            return httpx.Response(200, json={"name": bundle, "latestVersion": latest})
        if rest == "/versions":
            return httpx.Response(200, json={"versions": [
                {"version": v, "rootHash": h.to_wire(self.result(bundle, v).root_hash)} for v in versions]})
        m = re.fullmatch(r"/versions/(\d+)(/tree|/presign)?", rest)
        if m:
            v = int(m.group(1))
            if v not in versions:
                return httpx.Response(404, json={"error": "version not found"})
            files = versions[v]
            if m.group(2) is None:
                res = self.result(bundle, v)
                return httpx.Response(200, json={"version": v, "rootHash": h.to_wire(res.root_hash),
                                                 "entries": [self._entry_json(bundle, v, n) for n in res.entries]})
            if m.group(2) == "/tree":
                prefix = params.get("prefix", "")
                items = [{"path": p, "hash": h.to_wire(h.file_hash(b)), "size": len(b)}
                         for p, b in sorted(files.items()) if p.startswith(prefix)]
                start = int(params.get("cursor", "0"))
                page = items[start:start + self.page_size]
                body = {"items": page}
                if start + self.page_size < len(items):
                    body["nextCursor"] = str(start + self.page_size)
                return httpx.Response(200, json=body)
            body = json.loads(request.content or b"{}")
            urls = []
            for p in body.get("paths", []):
                if p in files:
                    hex_ = h.file_hash(files[p])
                    urls.append({"path": p, "hash": h.to_wire(hex_), "url": STORAGE + hex_})
            return httpx.Response(200, json={"urls": urls})
        m = re.fullmatch(r"/entries/([^/]+)", rest)
        if m:
            v = int(params.get("version", latest))
            if v not in versions or m.group(1) not in self.result(bundle, v).entries:
                return httpx.Response(404, json={"error": "entry not found"})
            return httpx.Response(200, json=self._entry_json(bundle, v, m.group(1)))
        return httpx.Response(404, json={"error": "not found"})

    def storage(self, request: httpx.Request) -> httpx.Response:
        assert "authorization" not in {k.lower() for k in request.headers}
        hex_ = str(request.url).rsplit("/", 1)[-1]
        self.blob_gets.append(hex_)
        if hex_ in self.tamper:
            return httpx.Response(200, content=self.tamper[hex_])
        if hex_ not in self.blobs:
            return httpx.Response(404)
        return httpx.Response(200, content=self.blobs[hex_])

    def bundles(self, cache_dir: Path):
        from dreamlake.api._client import DreamLakeClient
        from dreamlake.bundles import Bundles

        client = DreamLakeClient(dl_url="https://api.test", token="t", transport=httpx.MockTransport(self.api))
        return Bundles(client, cache_dir=cache_dir, storage_transport=httpx.MockTransport(self.storage))
