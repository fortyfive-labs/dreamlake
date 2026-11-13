"""Caller-authorized Library reads. Signed URLs are transient; downloads never forward auth."""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path, PurePosixPath
import tempfile
from typing import Any, TYPE_CHECKING
from urllib.parse import quote, urlsplit

import httpx

if TYPE_CHECKING:
    from ._client import DreamLakeClient


class LibraryError(RuntimeError):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code
        super().__init__(code)


def _segment(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256 or value in (".", ".."):
        raise ValueError("Explicit namespace, library and assetId are required")
    return quote(value, safe="")


class Libraries:
    def __init__(self, client: DreamLakeClient):
        self.client = client

    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        with self.client.http() as http:
            response = http.request(method, path, **kwargs)
        if not response.is_success:
            raise LibraryError(response.status_code, "library_request_failed")
        result = response.json()
        if not isinstance(result, dict):
            raise LibraryError(502, "invalid_library_response")
        return result

    def list(self, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """Visible catalog page. Follow nextOffset; an absent cursor is a legacy server."""
        return self._request("GET", "/libraries", params={"limit": limit, "offset": offset})

    def search(self, libraries: list[str], query: str = "", *, limit: int = 20,
               offset: int = 0, category: str | None = None, kind: str | None = None,
               license: str | None = None, tag: str | None = None) -> dict[str, Any]:
        """An independent search, never a browser snapshot. Total is before pagination."""
        if not 1 <= len(libraries) <= 50:
            raise ValueError("Search requires 1–50 explicit namespace/library scopes")
        params = dict(libraries=",".join(libraries), q=query, limit=limit, offset=offset,
                      category=category, kind=kind, license=license, tag=tag)
        return self._request("GET", "/library-search", params={k: v for k, v in params.items() if v is not None})

    @staticmethod
    def _asset_path(namespace: str, library: str, asset_id: str) -> str:
        return f"/namespaces/{_segment(namespace)}/libraries/{_segment(library)}/assets/{_segment(asset_id)}"

    def asset(self, namespace: str, library: str, asset_id: str, *, file_offset: int = 0,
              file_limit: int = 100) -> dict[str, Any]:
        """Metadata, entry points, effective license, image references and a file page."""
        return self._request("GET", self._asset_path(namespace, library, asset_id),
                             params={"fileOffset": file_offset, "fileLimit": file_limit})

    def batch(self, requests: list[dict[str, Any]]) -> dict[str, Any]:
        """Independent search/asset requests with unique IDs and per-item errors.

        Search item: {id, operation: 'search', libraries, q, limit?, offset?}.
        Asset item: {id, operation: 'asset', namespace, library, assetId}.
        """
        if not 1 <= len(requests) <= 10:
            raise ValueError("A batch requires 1–10 requests")
        ids = [r.get("id") for r in requests]
        if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
            raise ValueError("Batch request IDs must be unique nonempty strings")
        return self._request("POST", "/library-batch", json={"requests": requests})

    def image(self, namespace: str, library: str, asset_id: str, *, path: str | None = None) -> dict[str, Any]:
        """Bounded image bytes plus source/purpose; no_image has no content field.

        Defaults to thumbnail. An explicit path must name an asset PNG/JPEG/WebP.
        Current ACL is checked server-side; no signed capability is persisted.
        """
        result = self._request("GET", self._asset_path(namespace, library, asset_id) + "/image",
                               params={"path": path} if path else {})
        if result.get("status") == "ready":
            result["content"] = base64.b64decode(result.pop("data"), validate=True)
            if len(result["content"]) > 2 * 1024 * 1024:
                raise LibraryError(413, "image_too_large")
        return result

    def download(self, namespace: str, library: str, asset_id: str, destination: str | Path,
                 *, max_bytes: int = 512 * 1024 * 1024) -> list[Path]:
        """Download the asset's declared dependency closure, verifying sizes and SHA256.

        Preserves relative paths and unrelated files. Partial success is retained;
        rerunning skips verified files. No DreamLake bearer is sent to storage.
        Refreshes an expired signature once using the current caller's asset API.
        """
        root = Path(destination).resolve()
        root.mkdir(parents=True, exist_ok=True)
        offset, total, paths = 0, 0, []
        revision = None
        with httpx.Client(timeout=30, follow_redirects=False) as storage:
            while True:
                asset = self.asset(namespace, library, asset_id, file_offset=offset)
                if revision is not None and asset.get("revision") != revision:
                    raise LibraryError(409, "library_changed")
                revision = asset.get("revision")
                for file in asset["files"]:
                    relative = file["path"]
                    parts = PurePosixPath(relative).parts
                    if not parts or relative.startswith("/") or "\\" in relative or any(p in (".", "..") for p in relative.split("/")):
                        raise LibraryError(400, "unsafe_library_path")
                    target = root.joinpath(*parts)
                    if target.is_symlink() or any(p.is_symlink() for p in target.parents if p != root and root in p.parents):
                        raise LibraryError(400, "unsafe_library_path")
                    total += file["size"]
                    if total > max_bytes:
                        raise LibraryError(413, "download_budget_exceeded")
                    if target.is_file() and target.stat().st_size == file["size"] and file.get("sha256") and _digest(target) == file["sha256"]:
                        paths.append(target)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    for attempt in range(2):
                        url = urlsplit(file["url"])
                        if url.scheme != "https" or url.username or url.password:
                            raise LibraryError(400, "unsafe_download_url")
                        with storage.stream("GET", file["url"]) as response:
                            if response.status_code in (401, 403) and attempt == 0:
                                fresh = self.asset(namespace, library, asset_id, file_offset=offset)
                                if fresh.get("revision") != revision:
                                    raise LibraryError(409, "library_changed")
                                file = next(f for f in fresh["files"] if f["path"] == relative)
                                continue
                            if not response.is_success:
                                raise LibraryError(response.status_code, "library_download_failed")
                            fd, temporary = tempfile.mkstemp(prefix=".dl-", dir=target.parent)
                            size, digest = 0, hashlib.sha256()
                            try:
                                with os.fdopen(fd, "wb") as out:
                                    for chunk in response.iter_bytes():
                                        size += len(chunk)
                                        if size > file["size"] or size > max_bytes:
                                            raise LibraryError(413, "download_size_mismatch")
                                        digest.update(chunk)
                                        out.write(chunk)
                                if size != file["size"] or file.get("sha256") and digest.hexdigest() != file["sha256"]:
                                    raise LibraryError(502, "download_integrity_failed")
                                os.replace(temporary, target)
                            finally:
                                if os.path.exists(temporary):
                                    os.unlink(temporary)
                        break
                    paths.append(target)
                next_offset = asset.get("nextFileOffset")
                if next_offset is None:
                    return paths
                if not isinstance(next_offset, int) or next_offset <= offset:
                    raise LibraryError(502, "invalid_file_cursor")
                offset = next_offset


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
