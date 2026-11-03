"""First-class runnable definitions, including inline and note-backed agents.

Definitions do not start sessions. Requests use the caller's existing client
identity; the server checks current namespace, project and note permissions.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict, cast
from urllib.parse import quote

if TYPE_CHECKING:
    from ._client import DreamLakeClient


class RunnableSkill(TypedDict):
    kind: Literal["note", "folder"]
    id: str


class RunnableContent(TypedDict):
    instructionNoteId: str
    skills: NotRequired[list[RunnableSkill]]


class RunnableFields(TypedDict, total=False):
    name: str
    module: str
    qualname: str
    kind: str
    description: str | None
    markdown: str | None
    content: RunnableContent | None
    channel: dict[str, Any] | None
    metadata: dict[str, Any] | None


class RunnableCreate(RunnableFields, total=False):
    projects: list[str]


class RunnableUpdate(RunnableFields, total=False):
    archived: bool
    expectedUpdatedAt: str


class Runnable(TypedDict):
    id: str
    namespaceId: str
    name: str
    module: str
    qualname: str
    kind: str
    description: str | None
    markdown: str | None
    content: RunnableContent | None
    channel: dict[str, Any] | None
    metadata: dict[str, Any] | None
    latestVersion: str | None
    archivedAt: str | None
    createdAt: str
    updatedAt: str


class RunnablePage(TypedDict):
    runnables: list[Runnable]
    page: int
    pageSize: int
    total: int
    totalPages: int


class RunnableDeleteResult(TypedDict):
    success: bool


class RunnableError(Exception):
    """A server refusal, exposing the status and error code without its body."""

    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


def _segment(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value in {".", ".."}:
        raise ValueError(f"An explicit {label} is required")
    return quote(value, safe="")


class Runnables:
    def __init__(self, client: DreamLakeClient):
        self.client = client

    def _request(self, namespace: str, method: str = "GET", *,
                 runnable: str | None = None, payload: Any = None,
                 params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        path = f"/namespaces/{_segment(namespace, 'namespace')}/runnables"
        if runnable is not None:
            path += "/" + _segment(runnable, "runnable ID or name")
        with self.client.http() as http:
            response = http.request(method, path, json=payload, params=params)
        if not response.is_success:
            try:
                body = response.json()
                code = (body.get("code") or body.get("error")) if isinstance(body, dict) else None
                if isinstance(code, dict):
                    code = code.get("code")
            except ValueError:
                code = None
            raise RunnableError(response.status_code, code if isinstance(code, str) and code else "RUNNABLE_REQUEST_FAILED")
        if method == "DELETE" and not response.content:
            return None
        try:
            body = response.json()
        except ValueError:
            raise RunnableError(response.status_code, "INVALID_RUNNABLE_RESPONSE") from None
        if not isinstance(body, dict):
            raise RunnableError(response.status_code, "INVALID_RUNNABLE_RESPONSE")
        return body

    def list(self, namespace: str, *, kind: str | None = None,
             module: str | None = None, include_archived: bool = False,
             page: int = 1, page_size: int = 50) -> RunnablePage:
        """List a single server-authorized page; archived definitions are hidden by default."""
        if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 200:
            raise ValueError("page must be positive and page_size must be between 1 and 200")
        if not isinstance(include_archived, bool):
            raise ValueError("include_archived must be a boolean")
        params: dict[str, Any] = {"page": page, "pageSize": page_size,
                                  "includeArchived": str(include_archived).lower()}
        if kind is not None:
            params["kind"] = kind
        if module is not None:
            params["module"] = module
        return cast(RunnablePage, self._request(namespace, params=params))

    def get(self, namespace: str, runnable: str) -> Runnable:
        """Read by database ID or name, including an archived definition."""
        return cast(Runnable, self._request(namespace, runnable=runnable))

    def create(self, namespace: str, definition: RunnableCreate) -> Runnable:
        """Create a definition and optional project filings in one transaction.

        Set kind='agent'; choose inline markdown or content references. Supply
        a dotted name or module and qualname. Creating never starts execution.
        """
        return cast(Runnable, self._request(namespace, "POST", payload=definition))

    def update(self, namespace: str, runnable: str, changes: RunnableUpdate) -> Runnable:
        """Patch only supplied fields. expectedUpdatedAt rejects stale saves with 409.

        Omitted content, metadata and archive state remain unchanged. To switch
        from referenced notes to inline instructions, send content=None with
        markdown; to switch back, send markdown=None with content.
        """
        return cast(Runnable, self._request(namespace, "PATCH", runnable=runnable, payload=changes))

    def delete(self, namespace: str, runnable: str) -> RunnableDeleteResult | None:
        """Soft-delete and remove project filings; immutable versions are retained."""
        return cast(RunnableDeleteResult | None, self._request(namespace, "DELETE", runnable=runnable))

    def archive(self, namespace: str, runnable: str, *, expected_updated_at: str | None = None) -> Runnable:
        """Hide from the default catalog without changing instructions or filings."""
        return self._archive(namespace, runnable, True, expected_updated_at)

    def restore(self, namespace: str, runnable: str, *, expected_updated_at: str | None = None) -> Runnable:
        """Restore an archived definition; does not restore a deleted resource."""
        return self._archive(namespace, runnable, False, expected_updated_at)

    def _archive(self, namespace: str, runnable: str, archived: bool,
                 expected_updated_at: str | None) -> Runnable:
        changes: RunnableUpdate = {"archived": archived}
        if expected_updated_at is not None:
            changes["expectedUpdatedAt"] = expected_updated_at
        return self.update(namespace, runnable, changes)
