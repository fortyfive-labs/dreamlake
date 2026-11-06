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
from typing import TYPE_CHECKING
from urllib.parse import quote

if TYPE_CHECKING:
    from .runnables import Runnables
    from .artifacts import Artifacts

import httpx

from dreamlake.config import DEFAULT_REMOTE_URL

_DEFAULT_DL = DEFAULT_REMOTE_URL
_DEFAULT_QDRANT = "http://localhost:6333"


def _saved_remote() -> str | None:
    """The server `dreamlake env use` selected, if any."""
    try:
        from dreamlake.config import Config

        return Config().remote_url
    except Exception:
        return None


class _BorrowedTransport(httpx.BaseTransport):
    """Let short-lived HTTP clients share a transport owned by their caller."""

    def __init__(self, transport: httpx.BaseTransport):
        self._transport = transport

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self._transport.handle_request(request)

    def close(self) -> None:
        # Only the caller that supplied the transport controls its lifetime.
        pass


class BindrMembershipError(RuntimeError):
    """A bulk filing stopped; completed filings and the Bindr remain in place.

    A transport failure can leave the failed node's outcome unknown. Inspect
    the Bindr before retrying; filing a node again is idempotent.
    """

    def __init__(self, bindr_id: str, completed_node_ids: list[str], failed_node_id: str):
        self.bindr_id = bindr_id
        self.completed_node_ids = list(completed_node_ids)
        self.failed_node_id = failed_node_id
        super().__init__(
            f"Bindr {bindr_id}: confirmed {len(completed_node_ids)} filing(s); "
            f"filing {failed_node_id} failed. The Bindr and confirmed filings remain."
        )


def _bindr_node_ids(members: list) -> list[str]:
    """Accept node IDs and legacy node refs, never reinterpret a Bindr as a node."""
    ids = []
    for member in members:
        node_id = member
        if isinstance(member, dict):
            if member.get("type", "node") != "node":
                raise ValueError("Bindrs cannot be members; use the Bindr parent hierarchy instead")
            node_id = member.get("nodeId", member.get("id"))
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("Each Bindr member must have a nonempty node ID")
        if node_id not in ids:
            ids.append(node_id)
    return ids


class DreamLakeClient:
    """Unified HTTP client for dreamlake-server and Qdrant."""

    def __init__(
        self,
        dl_url: str | None = None,
        qdrant_url: str | None = None,
        token: str | None = None,
        transport: "httpx.BaseTransport | None" = None,
    ):
        # Applies to every DreamLake request. Qdrant uses its own service
        # connection and never receives DreamLake authentication headers.
        self._transport = transport
        self.dl_url = (
            dl_url or os.environ.get("DREAMLAKE_REMOTE") or _saved_remote() or _DEFAULT_DL
        ).rstrip("/")
        self.qdrant_url = (qdrant_url or os.environ.get("QDRANT_URL", _DEFAULT_QDRANT)).rstrip("/")
        from dreamlake._session import get_token_or_none
        self._token = token or get_token_or_none()

    @property
    def sources(self):
        """Source management, access and terminal deletion under server authority."""
        from .sources import Sources
        return Sources(self)

    @property
    def artifacts(self) -> "Artifacts":
        """Artifact catalog reads and visibility management."""
        from .artifacts import Artifacts
        return Artifacts(self)

    @property
    def runnables(self) -> "Runnables":
        """Agent and runnable definitions, project filings, archival and restore."""
        from .runnables import Runnables
        return Runnables(self)

    @property
    def projects(self):
        """Project grants and resource attachments, under current server authority."""
        from .projects import Projects
        return Projects(self)

    @property
    def infra(self):
        """Infrastructure registrations and reviewed metadata moves; no cloud mutations."""
        from .infrastructure import Infrastructure
        return Infrastructure(self)

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
    def experiments(self):
        """DreamLake-native project Experiments and logging."""
        from .experiments import Experiments
        return Experiments(self)

    @property
    def hosts(self):
        """Host enrollment, input validation, and status using this account."""
        from .hosts import Hosts
        return Hosts(self)

    @property
    def memos(self):
        """Callable Memos facade bound to this client.

        ``client.memos("ns/note-id")`` opens a handle without a request;
        ``client.memos.list(namespace=...)`` lists. Nothing else lives here.
        """
        from ._memos_facade import Memos
        return Memos(self)

    @property
    def notes(self):
        """Deprecated alias for :attr:`memos`."""
        return self.memos

    def http(self) -> "httpx.Client":
        """Return an HTTP client; close it after use, preferably with ``with``.

        Injected transports are borrowed and remain caller-owned. Without an
        injection, closing this client releases its own connection pools.
        """
        return httpx.Client(
            base_url=self.dl_url,
            headers=self._headers(),
            transport=_BorrowedTransport(self._transport) if self._transport is not None else None,
            timeout=30,
            event_hooks={"request": [self._memo_agent_headers]},
        )

    def _memo_agent_headers(self, request: httpx.Request) -> None:
        """Attribute explicit agent Memos operations; never infer from Python use."""
        base = httpx.URL(self.dl_url)
        if (request.url.scheme, request.url.host, request.url.port) != (base.scheme, base.host, base.port):
            return
        route = re.match(r"^/namespaces/[^/]+/notes/[^/]+/(body|diff|sections|presence)(/|$)", request.url.path)
        if not route:
            return
        # Presence is attributed only when it is asserted (POST: visit/seek).
        # Reading the roster registers nothing and needs no identity, as the
        # CLI's roster reader does not send one.
        if route.group(1) == "presence" and request.method != "POST":
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
        with self.http() as http:
            r = http.post(
                f"/namespaces/{namespace}/projects/{project}/upload-authorizations",
                json={"files": files}, timeout=60,
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
        with self.http() as http:
            r = http.post(
                f"/namespaces/{namespace}/projects/{project}"
                f"/upload-authorizations/resume",
                json={"objectKey": object_key, "uploadId": upload_id, "size": size},
                timeout=60,
            )
        r.raise_for_status()
        return r.json()

    def register_node(self, body: dict) -> dict:
        """POST /nodes — create any node (project, episode, folder, or file asset)."""
        with self.http() as http:
            r = http.post(
                f"/nodes",
                json=body, timeout=30,
            )
        r.raise_for_status()
        return r.json()

    def register_dl_asset(self, asset_type: str, body: dict) -> dict:
        """Deprecated — use register_node() instead."""
        return self.register_node(body)

    def get_auth_me(self) -> dict:
        with self.http() as http:
            r = http.get(f"/auth/me", timeout=10)
        r.raise_for_status()
        return r.json()

    def lookup_node(self, namespace: str, project: str, path: str,
                    episode: str | None = None) -> dict:
        """GET /nodes/lookup — resolve a node by namespace + project + path."""
        params: dict = {"namespace": namespace, "project": project, "path": path}
        if episode:
            params["episode"] = episode
        with self.http() as http:
            r = http.get(f"/nodes/lookup", params=params,
                          timeout=15)
        r.raise_for_status()
        return r.json()

    def archive_folder(self, node_id: str) -> dict:
        """Archive a folder. The server rejects other node kinds."""
        return self._set_folder_archived(node_id, True)

    def unarchive_folder(self, node_id: str) -> dict:
        """Restore this folder and its live archived ancestors, not siblings."""
        return self._set_folder_archived(node_id, False)

    def _set_folder_archived(self, node_id: str, archived: bool) -> dict:
        from urllib.parse import quote
        if not node_id:
            raise ValueError("A folder node ID is required")
        with self.http() as http:
            response = http.patch("/nodes/" + quote(node_id, safe=""),
                                  json={"archived": archived})
        response.raise_for_status()
        return response.json()

    def get_node_children(self, parent_id: str | None = None, *,
                          namespace: str | None = None, project: str | None = None,
                          include_archived: bool = False, search: str | None = None,
                          recursive: bool = False, after: str | None = None,
                          page: int = 1, page_size: int = 50,
                          sort: str | None = None) -> dict:
        """List folder children or project roots, retaining server paging metadata.

        Archived items and descendants of archived folders are hidden by
        default. Recursive search cursors are scoped to include_archived.
        """
        if not parent_id and not (namespace and project):
            raise ValueError("Supply parent_id or both namespace and project")
        if recursive and not search:
            raise ValueError("Recursive search requires search")
        params = {"includeArchived": str(include_archived).lower(),
                  "page": page, "pageSize": page_size}
        for key, value in (("parentId", parent_id), ("namespace", namespace),
                           ("project", project), ("search", search),
                           ("after", after), ("sort", sort)):
            if value is not None:
                params[key] = value
        if recursive:
            params["recursive"] = "true"
        with self.http() as http:
            response = http.get("/nodes/children", params=params)
        response.raise_for_status()
        return response.json()

    def get_node_download_url(self, node_id: str) -> dict:
        """GET /nodes/:id/download — returns { url, filename } (presigned S3)."""
        with self.http() as http:
            r = http.get(f"/nodes/{node_id}/download",
                          timeout=30, follow_redirects=False)
        r.raise_for_status()
        return r.json()

    # ── Bindrs ───────────────────────────────────────────────────────────

    def create_bindr(self, namespace: str, project: str, name: str,
                     members: list[str | dict] | None = None,
                     description: str | None = None,
                     tags: list[str] | None = None) -> dict:
        """Create a Bindr and file nodes, returning its flat view and members.

        Creation and the first filing are atomic. Later filings are sequential;
        BindrMembershipError exposes confirmed progress if a later filing fails.
        """
        node_ids = _bindr_node_ids(members or [])
        body: dict = {"name": name}
        if node_ids:
            body["member"] = {"nodeId": node_ids[0]}
        if description is not None:
            body["description"] = description
        if tags is not None:
            body["tags"] = tags
        with self.http() as http:
            r = http.post(
                f"/namespaces/{quote(namespace, safe='')}/projects/{quote(project, safe='')}/bindrs",
                json=body, timeout=30,
            )
        r.raise_for_status()
        bindr = r.json()["bindr"]
        self._file_bindr_nodes(bindr["id"], node_ids[1:], completed=node_ids[:1])
        return {**bindr, "memberCount": len(node_ids),
                "members": [{"nodeId": node_id} for node_id in node_ids]}

    def get_bindr(self, namespace: str, project: str, name: str) -> dict:
        """Resolve an exact project-scoped name, then read the canonical ID route."""
        with self.http() as http:
            r = http.get(
                f"/namespaces/{quote(namespace, safe='')}/projects/{quote(project, safe='')}/bindrs",
                timeout=15,
            )
        r.raise_for_status()
        matches = [row for row in r.json()["bindrs"] if row["name"] == name]
        if not matches:
            response = httpx.Response(404, request=r.request, json={"error": "Bindr not found"})
            response.raise_for_status()
        if len(matches) != 1:
            raise ValueError(f"Ambiguous Bindr name: {name!r}; use its ID")
        return self.get_bindr_by_id(matches[0]["id"])

    def get_bindr_by_id(self, bindr_id: str) -> dict:
        """Read all member pages, retaining the legacy flat Bindr return shape.

        The server includes filings in child Bindrs and deduplicates node IDs.
        """
        members = []
        page = 1
        while True:
            with self.http() as http:
                r = http.get(f"/bindrs/{quote(bindr_id, safe='')}",
                             params={"page": page, "pageSize": 100, "recursive": "true"}, timeout=15)
            r.raise_for_status()
            result = r.json()
            members.extend(result["members"])
            if page >= result["totalPages"]:
                return {**result["bindr"], "members": members, "memberCount": result["total"]}
            page += 1

    def _file_bindr_nodes(self, bindr_id: str, node_ids: list[str], *,
                         completed: list[str] | None = None) -> None:
        completed = list(completed or [])
        for node_id in node_ids:
            try:
                with self.http() as http:
                    r = http.post(f"/bindrs/{quote(bindr_id, safe='')}/members",
                                  json={"nodeId": node_id}, timeout=30)
                r.raise_for_status()
            except httpx.HTTPError as error:
                raise BindrMembershipError(bindr_id, completed, node_id) from error
            completed.append(node_id)

    def add_bindr_members(self, namespace: str, project: str, name: str,
                          add: list) -> dict:
        """File node IDs or legacy node refs using singular canonical requests."""
        node_ids = _bindr_node_ids(add)
        bindr = self.get_bindr(namespace, project, name)
        self._file_bindr_nodes(bindr["id"], node_ids)
        return self.get_bindr_by_id(bindr["id"])

    def get_node_descendants(self, node_id: str,
                             leaves_only: bool = False,
                             kind: str | None = None, *,
                             include_archived: bool = False) -> dict:
        """GET /nodes/:id/descendants — returns { root, rootPrefix, descendants, total }."""
        params: dict = {"includeArchived": str(include_archived).lower()}
        if leaves_only:
            params["leavesOnly"] = "true"
        if kind:
            params["kind"] = kind
        from urllib.parse import quote
        with self.http() as http:
            r = http.get("/nodes/" + quote(node_id, safe="") + "/descendants", params=params)
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
