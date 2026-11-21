"""Built-in agent transport. A page pathname does not provide a browser executor.

No retries: a disconnected turn may already have executed tools. Retain the
context event's sessionId and inspect receipts before submitting another turn.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

import httpx


class AgentError(Exception):
    """Sanitized transport/protocol failure; arbitrary server bodies stay private."""

    def __init__(self, code: str, status: int | None = None):
        super().__init__(code)
        self.code = code
        self.status = status


def _conversation(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{24}", value):
        raise ValueError("Use a 24-hex built-in conversation ID, not a native session UUID")
    return value


class Agent:
    def __init__(self, client):
        self.client = client

    def chat(
        self,
        message: str,
        *,
        session_id: str | None = None,
        acting_namespace: str | None = None,
        pathname: str = "/",
        capabilities: dict[str, int] | None = None,
        timeout: float = 120,
    ) -> Iterator[dict[str, Any]]:
        """Yield context, delta, tool and terminal events without buffering a turn.

        Requests no write/control capabilities by default. Explicit capability
        flags never grant resource access. Use contextlib.closing if stopping
        iteration early. Browser actions cannot execute in this Python client.
        """
        if not isinstance(message, str) or not 1 <= len(message) <= 8000:
            raise ValueError("message must contain 1–8000 characters")
        if not isinstance(pathname, str) or not pathname.startswith("/"):
            raise ValueError("pathname must be an absolute app path")
        if type(timeout) not in (int, float) or not 0 < timeout < float("inf"):
            raise ValueError("timeout must be positive and finite")
        body: dict[str, Any] = {"message": message, "page": {"pathname": pathname, "layouts": []}}
        if session_id is not None:
            body["sessionId"] = _conversation(session_id)
        if acting_namespace is not None:
            if not isinstance(acting_namespace, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", acting_namespace):
                raise ValueError("Invalid acting namespace")
            body["actingNamespace"] = acting_namespace
        if capabilities is not None:
            allowed = {"sessionControl", "notesWrite", "hostMove", "toolsWrite", "memoryWrite"}
            if not isinstance(capabilities, dict) or any(k not in allowed or type(v) is not int or v != 1 for k, v in capabilities.items()):
                raise ValueError("Only explicit supported non-browser capability flags with value 1 are allowed")
            body["capabilities"] = capabilities
        try:
            with self.client.http() as http:
                with http.stream("POST", "/agent/chat", json=body, headers={"Accept": "text/event-stream"}, timeout=timeout, follow_redirects=False) as response:
                    if not response.is_success:
                        raise AgentError("agent_request_failed", response.status_code)
                    if response.headers.get("content-type", "").split(";", 1)[0].strip() != "text/event-stream":
                        raise AgentError("agent_invalid_response")
                    data: list[str] = []
                    size = 0
                    for line in response.iter_lines():
                        if line == "":
                            if not data:
                                continue
                            try:
                                event = json.loads("\n".join(data))
                            except ValueError:
                                raise AgentError("agent_invalid_event") from None
                            data, size = [], 0
                            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                                raise AgentError("agent_invalid_event")
                            yield event
                            if event["type"] in ("done", "error"):
                                return
                        elif line.startswith("data:"):
                            value = line[5:]
                            if value.startswith(" "):
                                value = value[1:]
                            size += len(value)
                            if size > 1024 * 1024:
                                raise AgentError("agent_event_too_large")
                            data.append(value)
                    raise AgentError("agent_stream_interrupted")
        except httpx.HTTPError:
            raise AgentError("agent_transport_unavailable") from None

    def tool_calls(self, session_id: str, *, cursor: str | None = None, limit: int = 50) -> dict[str, Any]:
        """Read one receipt page; preserve nextCursor and uncertain tool status."""
        session_id = _conversation(session_id)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("limit must be 1–200")
        params: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            if not isinstance(cursor, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", cursor):
                raise ValueError("Invalid receipt cursor")
            params["cursor"] = cursor
        try:
            with self.client.http() as http:
                response = http.get(f"/agent/sessions/{session_id}/tool-calls", params=params)
            if not response.is_success:
                raise AgentError("agent_request_failed", response.status_code)
            result = response.json()
        except httpx.HTTPError:
            raise AgentError("agent_transport_unavailable") from None
        except ValueError:
            raise AgentError("agent_invalid_response") from None
        if not isinstance(result, dict) or not isinstance(result.get("toolCalls"), list):
            raise AgentError("agent_invalid_response")
        return result
