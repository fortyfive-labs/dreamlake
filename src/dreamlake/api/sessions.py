"""Read the shared Codex/Claude session registry without starting a process.

Registry IDs identify saved sessions; native provider UUIDs do not select API
resources. Reads preserve the server's freshness, visibility and binding fields.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote


class SessionError(Exception):
    """Sanitized registry failure; no response body or credentials are echoed."""

    def __init__(self, code: str, status: int | None = None):
        super().__init__(code)
        self.code = code
        self.status = status


def _namespace(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value
    ):
        raise ValueError("An explicit namespace slug is required")
    return quote(value, safe="")


def _id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{24}", value):
        raise ValueError("Use the DreamLake registry ID, not a provider session UUID")
    return value


class Sessions:
    """Metadata-only session reads using the client's existing account/transport."""

    def __init__(self, client):
        self.client = client

    def _get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        import httpx

        try:
            with self.client.http() as http:
                response = http.get(path, params=params)
        except httpx.HTTPError:
            raise SessionError("session_transport_unavailable") from None
        if not response.is_success:
            # Server codes are useful but arbitrary bodies can contain private data.
            code = {
                400: "session_invalid_request",
                401: "session_unauthorized",
                403: "session_forbidden",
                404: "session_not_found",
                409: "session_conflict",
                422: "session_search_scope_too_large",
            }.get(response.status_code, "session_request_failed")
            raise SessionError(code, response.status_code)
        try:
            body = response.json()
        except ValueError:
            raise SessionError(
                "session_invalid_response", response.status_code
            ) from None
        if not isinstance(body, dict):
            raise SessionError("session_invalid_response", response.status_code)
        return body

    @staticmethod
    def _query(
        *,
        q=None,
        search_in=None,
        provider=None,
        machine_id=None,
        page_size=50,
        cursor=None,
        sort="recent",
        archived="exclude",
    ) -> dict:
        if type(page_size) is not int or not 1 <= page_size <= 100:
            raise ValueError("page_size must be 1–100")
        if search_in not in (None, "title", "all"):
            raise ValueError("search_in must be title or all")
        if provider not in (None, "codex", "claude", "dreamlake-agent"):
            raise ValueError("Unknown session provider")
        if sort not in ("created", "recent") or archived not in (
            "exclude",
            "include",
            "only",
        ):
            raise ValueError("Invalid session sort or archive filter")
        for value, limit in [(q, 200), (machine_id, 512), (cursor, 1024)]:
            if value is not None and (
                not isinstance(value, str)
                or len(value) > limit
                or re.search(r"[\x00-\x1f\x7f]", value)
            ):
                raise ValueError("Invalid session query")
        if machine_id == "":
            raise ValueError("machine_id must be nonempty")
        return {
            key: value
            for key, value in {
                "q": q,
                "searchIn": search_in,
                "provider": provider,
                "machineId": machine_id,
                "pageSize": page_size,
                "cursor": cursor,
                "sort": sort,
                "archived": archived,
            }.items()
            if value is not None
        }

    def list(self, namespace: str, **query) -> dict[str, Any]:
        """One bounded page, including hasMore/nextCursor; no automatic traversal."""
        return self._get(
            f"/namespaces/{_namespace(namespace)}/terminal-sessions",
            self._query(**query),
        )

    def search(
        self, namespace: str, query: str, *, search_in: str | None = None, **filters
    ) -> dict[str, Any]:
        """Search before pagination; omitted search_in preserves broad search."""
        return self.list(namespace, q=query, search_in=search_in, **filters)

    def available(
        self, *, owner: str | None = None, team: str | None = None, **query
    ) -> dict[str, Any]:
        """Explicit wider search of readable sessions; filters grant no access."""
        params = self._query(**query)
        for key, value in [("owner", owner), ("team", team)]:
            if value is not None:
                if not isinstance(value, str) or not re.fullmatch(
                    r"@?[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value
                ):
                    raise ValueError("Invalid session owner or team selector")
                params[key] = value
        return self._get("/terminal-sessions/available", params)

    def get(self, namespace: str, session_id: str) -> dict[str, Any]:
        """Read descriptor metadata; does not request terminal execution authority."""
        body = self._get(
            f"/namespaces/{_namespace(namespace)}/terminal-sessions/{_id(session_id)}/metadata"
        )
        if (
            not isinstance(body.get("session"), dict)
            or body["session"].get("id") != session_id
        ):
            raise SessionError("session_invalid_response")
        return body["session"]

    def attention(self, namespace: str) -> dict[str, Any]:
        """Personal owner's durable waiting/error counts and newest 20 previews."""
        return self._get(f"/namespaces/{_namespace(namespace)}/sessions/attention")

    def memo_progress(self, memo_namespace: str, memo_id: str, *, page_size: int = 50, cursor: str | None = None) -> dict[str, Any]:
        """One access-aware page of exact linked sessions and sourced observations.

        Namespace owns the memo, not necessarily its sessions. Requires the
        matching Visibility backend. No probing, dispatch or acceptance inference.
        An empty page with nextCursor is not proof that there are no more links.
        """
        if type(page_size) is not int or not 1 <= page_size <= 50:
            raise ValueError("page_size must be 1–50")
        params = {"pageSize": page_size}
        if cursor is not None:
            params["cursor"] = _id(cursor)
        result = self._get(f"/namespaces/{_namespace(memo_namespace)}/notes/{_id(memo_id)}/progress", params)
        if result.get("schemaVersion") != 1 or not isinstance(result.get("sessions"), list) or not isinstance(result.get("task"), dict) or result["task"].get("id") != memo_id:
            raise SessionError("session_invalid_response")
        return result

    def targets(self, namespace: str) -> dict[str, Any]:
        """Read authorized enrolled SSH targets; never choose the first as default."""
        return self._get(f"/namespaces/{_namespace(namespace)}/hosts/terminal-targets")

    def target(self, namespace: str, session_id: str) -> dict[str, Any]:
        """Resolve the exact live binding under execution-read authorization.

        This reads target metadata; it does not issue a ticket or attach/start.
        """
        return self._get(
            f"/namespaces/{_namespace(namespace)}/terminal-sessions/{_id(session_id)}/target"
        )

    @staticmethod
    def reference(namespace: str, session_id: str) -> str:
        """Native memo reference; titles and native provider IDs are not identities."""
        return f":session[{_namespace(namespace)}/{_id(session_id)}]"
