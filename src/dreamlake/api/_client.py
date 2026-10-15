"""
HTTP client for dreamlake-server.

Reads config from environment variables:
  DREAMLAKE_REMOTE    (default: https://api.dreamlake.ai)
  DREAMLAKE_API_KEY   (token)
  QDRANT_URL          (default: http://localhost:6333)

With no DREAMLAKE_API_KEY, the token and server saved by `dreamlake login`
are used instead. Both tools keep them in ~/.dreamlake, so logging in once
is enough — otherwise a user who had just logged in would find the Python
side unauthenticated with nothing to say why.
"""

import os
import re

import httpx

from dreamlake.config import DEFAULT_REMOTE_URL

_DEFAULT_DL = DEFAULT_REMOTE_URL
_DEFAULT_QDRANT = "http://localhost:6333"


def _saved_token() -> str | None:
    """The token `dreamlake login` stored, if there is one.

    Best-effort: the auth extras (keyring, cryptography) are optional, and a
    missing or unreadable store means "not logged in" rather than an error the
    caller cannot act on.
    """
    try:
        from dreamlake.auth.token_storage import get_token_storage

        # The same key `dreamlake login` writes. Guessing it wrong is a silent
        # failure — the load returns None and the client is simply anonymous.
        return get_token_storage().load("dreamlake-token")
    except Exception:
        return None


def _saved_remote() -> str | None:
    """The server `dreamlake env use` selected, if any."""
    try:
        from dreamlake.config import Config

        return Config().remote_url
    except Exception:
        return None


class DreamLakeClient:
    """Unified HTTP client for dreamlake-server and Qdrant."""

    def __init__(
        self,
        dl_url: str | None = None,
        qdrant_url: str | None = None,
        token: str | None = None,
        transport: "httpx.BaseTransport | None" = None,
    ):
        # Injectable so a caller — in practice a test — can serve requests
        # without a server. Only the notes surface routes through it; the older
        # methods still call module-level httpx directly.
        self._transport = transport
        self.dl_url = (
            dl_url or os.environ.get("DREAMLAKE_REMOTE") or _saved_remote() or _DEFAULT_DL
        ).rstrip("/")
        self.qdrant_url = (qdrant_url or os.environ.get("QDRANT_URL", _DEFAULT_QDRANT)).rstrip("/")
        self._token = token or os.environ.get("DREAMLAKE_API_KEY") or _saved_token()

    @property
    def sources(self):
        """Source management, access and terminal deletion under server authority."""
        from .sources import Sources
        return Sources(self)

    @property
    def projects(self):
        """Project grants and resource attachments, under current server authority."""
        from .projects import Projects
        return Projects(self)

    @property
    def ownership(self):
        """Server-authorized ownership previews and transfer inbox decisions."""
        from .ownership import Ownership
        return Ownership(self)

    @property
    def providers(self):
        """Provider declarations, owned associations and recovery receipts."""
        from .providers import Providers
        return Providers(self)

    @property
    def runs(self):
        """Tracked execution through the account-authenticated main API."""
        from .runs import Runs
        return Runs(self)

    @property
    def hosts(self):
        """Host enrollment, input validation, and status using this account."""
        from .hosts import Hosts
        return Hosts(self)

    def http(self) -> "httpx.Client":
        """An httpx client bound to dreamlake-server, with auth applied."""
        return httpx.Client(
            base_url=self.dl_url,
            headers=self._headers(),
            transport=self._transport,
            timeout=30,
            event_hooks={"request": [self._note_agent_headers]},
        )

    def _note_agent_headers(self, request: httpx.Request) -> None:
        """Attribute explicit agent Notes operations; never infer from Python use."""
        base = httpx.URL(self.dl_url)
        if (request.url.scheme, request.url.host, request.url.port) != (base.scheme, base.host, base.port):
            return
        if not re.match(r"^/namespaces/[^/]+/notes/[^/]+/(body|diff|sections)(/|$)", request.url.path):
            return
        agent_id = os.getenv("DREAMLAKE_AGENT_ID")
        if not agent_id:
            return
        name = os.getenv("DREAMLAKE_AGENT_NAME")
        if not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,128}", agent_id):
            raise ValueError("DREAMLAKE_AGENT_ID must be a 1-128 character session identifier")
        if name is not None and (len(name) > 64 or re.search(r"[^\x20-\x7e]", name)):
            raise ValueError("DREAMLAKE_AGENT_NAME must be at most 64 printable ASCII characters")
        request.headers["X-DreamLake-Agent-Id"] = agent_id
        if name:
            request.headers["X-DreamLake-Agent-Name"] = name

    def _headers(self) -> dict:
        h = {}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    # ── DreamLake Server ────────────────────────────────────────────────

    def authorize_upload(self, namespace: str, project: str, files: list[dict]) -> dict:
        """Ask dreamlake-server for keys and signed requests.

        Replaces `upload_init` + `upload_parts`: one call answers with the
        object key each file gets and the whole transfer plan for it. The key
        is minted here and echoed back on registration — this client never
        builds one, because the server checks that a registered key is one it
        authorized for that project.
        """
        r = httpx.post(
            f"{self.dl_url}/namespaces/{namespace}/projects/{project}/upload-authorizations",
            json={"files": files}, headers=self._headers(), timeout=60,
        )
        r.raise_for_status()
        return r.json()

    def resume_upload(self, namespace: str, project: str, object_key: str,
                      upload_id: str, size: int) -> dict:
        """Pick up an interrupted multipart upload.

        Which parts exist is STORAGE's answer, not ours: a part can fail after
        we record it, or succeed after we give up, and a local list that
        disagrees completes the upload naming parts storage does not have.

        Raises `httpx.HTTPStatusError`; 404 means the upload is gone (finished,
        aborted, or swept by the lifecycle rule) and 409 that what is stored
        describes a different partitioning of the file. Both mean "start over".
        """
        r = httpx.post(
            f"{self.dl_url}/namespaces/{namespace}/projects/{project}"
            f"/upload-authorizations/resume",
            json={"objectKey": object_key, "uploadId": upload_id, "size": size},
            headers=self._headers(), timeout=60,
        )
        r.raise_for_status()
        return r.json()

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
