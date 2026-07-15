"""
HTTP client for BSS and dreamlake-server.

Reads config from environment variables:
  DREAMLAKE_BSS_URL   (default: http://localhost:10234)
  DREAMLAKE_REMOTE    (default: https://api.dreamlake.ai)
  DREAMLAKE_API_KEY   (token)
  QDRANT_URL          (default: http://localhost:6333)
"""

import os
import re

import httpx

from dreamlake.config import DEFAULT_REMOTE_URL

_DEFAULT_BSS = "http://localhost:10234"
_DEFAULT_DL = DEFAULT_REMOTE_URL
_DEFAULT_QDRANT = "http://localhost:6333"


class DreamLakeClient:
    """Unified HTTP client for BSS, dreamlake-server, and Qdrant."""

    def __init__(
        self,
        bss_url: str | None = None,
        dl_url: str | None = None,
        qdrant_url: str | None = None,
        token: str | None = None,
    ):
        self.bss_url = (bss_url or os.environ.get("DREAMLAKE_BSS_URL", _DEFAULT_BSS)).rstrip("/")
        self.dl_url = (dl_url or os.environ.get("DREAMLAKE_REMOTE", _DEFAULT_DL)).rstrip("/")
        self.qdrant_url = (qdrant_url or os.environ.get("QDRANT_URL", _DEFAULT_QDRANT)).rstrip("/")
        self._token = token or os.environ.get("DREAMLAKE_API_KEY")

    def _headers(self) -> dict:
        h = {}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    # ── BSS: Video metadata ─────────────────────────────────────────────

    def get_video_meta(self, video_id: str) -> dict:
        r = httpx.get(f"{self.bss_url}/videos/{video_id}/metadata", timeout=30)
        r.raise_for_status()
        return r.json()

    def get_stream_playlist(self, video_id: str, stream_hash: str) -> str:
        r = httpx.get(f"{self.bss_url}/videos/{video_id}/stream/{stream_hash}.m3u8", timeout=30)
        r.raise_for_status()
        return r.text

    def parse_chunk_hashes(self, m3u8_content: str) -> list[dict]:
        """Parse m3u8 playlist into ordered list of {hash, duration, url}."""
        chunks = []
        duration = 0.0
        for line in m3u8_content.splitlines():
            line = line.strip()
            if line.startswith("#EXTINF:"):
                duration = float(line.split(":")[1].rstrip(","))
            elif line and not line.startswith("#"):
                match = re.search(r"/chunks/([a-f0-9]+)\.ts", line)
                if match:
                    chunks.append({
                        "hash": match.group(1),
                        "duration": duration,
                        "url": line,
                    })
        return chunks

    def download_chunk(self, url: str) -> bytes:
        r = httpx.get(url, timeout=30, follow_redirects=True)
        r.raise_for_status()
        return r.content

    # ── BSS: Multipart upload ───────────────────────────────────────────

    def upload_init(self, asset_type: str, owner: str, project: str,
                    file_hash: str, content_type: str) -> dict:
        r = httpx.post(
            f"{self.bss_url}/{asset_type}/upload/multipart/init",
            json={"owner": owner, "project": project, "hash": file_hash, "contentType": content_type},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def upload_parts(self, asset_type: str, upload_id: str, key: str,
                     part_numbers: list[int]) -> dict:
        r = httpx.post(
            f"{self.bss_url}/{asset_type}/upload/multipart/parts",
            json={"uploadId": upload_id, "key": key, "partNumbers": part_numbers},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["parts"]

    def upload_complete(self, asset_type: str, upload_id: str, key: str,
                        parts: list[dict]) -> None:
        r = httpx.post(
            f"{self.bss_url}/{asset_type}/upload/multipart/complete",
            json={"uploadId": upload_id, "key": key, "parts": parts},
            timeout=60,
        )
        r.raise_for_status()

    def register_bss_asset(self, asset_type: str, body: dict) -> dict:
        """Register a file with BSS via unified /files route.

        `asset_type` is kept for backward compat but ignored — kind lives in body.
        """
        r = httpx.post(f"{self.bss_url}/files", json=body, timeout=30)
        r.raise_for_status()
        return r.json()

    # ── DreamLake Server ────────────────────────────────────────────────

    def register_node(self, body: dict) -> dict:
        """POST /nodes — create any node (project, episode, folder, or file asset)."""
        r = httpx.post(
            f"{self.dl_url}/nodes",
            json=body, headers=self._headers(), timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def register_dl_asset(self, asset_type: str, body: dict) -> dict:
        """Deprecated — use register_node() instead."""
        return self.register_node(body)

    def get_auth_me(self) -> dict:
        r = httpx.get(f"{self.dl_url}/auth/me", headers=self._headers(), timeout=10)
        r.raise_for_status()
        return r.json()

    def lookup_node(self, namespace: str, project: str, path: str,
                    episode: str | None = None) -> dict:
        """GET /nodes/lookup — resolve a node by namespace + project + path."""
        params: dict = {"namespace": namespace, "project": project, "path": path}
        if episode:
            params["episode"] = episode
        r = httpx.get(f"{self.dl_url}/nodes/lookup", params=params,
                      headers=self._headers(), timeout=15)
        r.raise_for_status()
        return r.json()

    def get_node_download_url(self, node_id: str) -> dict:
        """GET /nodes/:id/download — returns { url, filename } (presigned S3)."""
        r = httpx.get(f"{self.dl_url}/nodes/{node_id}/download",
                      headers=self._headers(), timeout=30, follow_redirects=False)
        r.raise_for_status()
        return r.json()

    # ── Bindrs ───────────────────────────────────────────────────────────

    def create_bindr(self, namespace: str, project: str, name: str,
                     members: list[str] | None = None,
                     description: str | None = None,
                     tags: list[str] | None = None) -> dict:
        body: dict = {"name": name}
        if members is not None: body["members"] = members
        if description is not None: body["description"] = description
        if tags is not None: body["tags"] = tags
        r = httpx.post(
            f"{self.dl_url}/namespaces/{namespace}/projects/{project}/bindrs",
            json=body, headers=self._headers(), timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def get_bindr(self, namespace: str, project: str, name: str) -> dict:
        r = httpx.get(
            f"{self.dl_url}/namespaces/{namespace}/projects/{project}/bindrs/{name}",
            headers=self._headers(), timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def get_bindr_by_id(self, bindr_id: str) -> dict:
        """Lookup a bindr by its ID — used to resolve nested bindr members."""
        r = httpx.get(
            f"{self.dl_url}/bindrs/{bindr_id}",
            headers=self._headers(), timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def add_bindr_members(self, namespace: str, project: str, name: str,
                          add: list) -> dict:
        """Add members to a bindr.

        `add` is a list of:
          - str            → treated as a node ID (backwards compat)
          - {"type":"node",  "id":...}
          - {"type":"bindr", "id":...}
        Server rejects cycles for bindr-typed refs.
        """
        r = httpx.post(
            f"{self.dl_url}/namespaces/{namespace}/projects/{project}/bindrs/{name}/members",
            json={"add": add}, headers=self._headers(), timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def get_node_descendants(self, node_id: str,
                             leaves_only: bool = False,
                             kind: str | None = None) -> dict:
        """GET /nodes/:id/descendants — returns { root, rootPrefix, descendants, total }."""
        params: dict = {}
        if leaves_only:
            params["leavesOnly"] = "true"
        if kind:
            params["kind"] = kind
        r = httpx.get(f"{self.dl_url}/nodes/{node_id}/descendants",
                      params=params, headers=self._headers(), timeout=30)
        r.raise_for_status()
        return r.json()

    # ── Qdrant ──────────────────────────────────────────────────────────

    def qdrant_ensure_collection(self, collection: str, dim: int = 768) -> None:
        r = httpx.get(f"{self.qdrant_url}/collections/{collection}", timeout=10)
        if r.status_code == 404:
            r = httpx.put(f"{self.qdrant_url}/collections/{collection}", json={
                "vectors": {
                    "image": {"size": dim, "distance": "Cosine"},
                    "caption": {"size": dim, "distance": "Cosine"},
                },
            }, timeout=10)
            r.raise_for_status()

    def qdrant_upsert(self, collection: str, points: list[dict]) -> None:
        r = httpx.put(
            f"{self.qdrant_url}/collections/{collection}/points",
            json={"points": points},
            timeout=30,
        )
        r.raise_for_status()

    def qdrant_search(self, collection: str, vector: list[float],
                      using: str = "image", limit: int = 10,
                      filter_: dict | None = None) -> list[dict]:
        body: dict = {"query": vector, "using": using, "limit": limit, "with_payload": True}
        if filter_:
            body["filter"] = filter_
        r = httpx.post(
            f"{self.qdrant_url}/collections/{collection}/points/query",
            json=body, timeout=30,
        )
        r.raise_for_status()
        return r.json().get("result", {}).get("points", [])

    def qdrant_count(self, collection: str) -> int:
        r = httpx.get(f"{self.qdrant_url}/collections/{collection}", timeout=10)
        if r.status_code == 404:
            return 0
        r.raise_for_status()
        return r.json().get("result", {}).get("points_count", 0)


# Singleton client, lazy-initialized
_default_client: DreamLakeClient | None = None


def get_client() -> DreamLakeClient:
    global _default_client
    if _default_client is None:
        _default_client = DreamLakeClient()
    return _default_client
