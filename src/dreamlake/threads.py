"""Build browser URLs for saved DreamLake Threads."""

import re
from urllib.parse import urlencode

_UUID = re.compile(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", re.IGNORECASE | re.ASCII)
_SESSION_ID = re.compile(r"(?:archive:[A-Za-z0-9_-]{1,128}:)?[A-Za-z0-9_-]{1,128}")


def threads_url(namespace: str) -> str:
    """Return the Threads list URL for a namespace."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", namespace):
        raise ValueError("Invalid namespace.")
    return f"https://dreamlake.ai/{namespace}/sessions"


def thread_url(thread_id: str, *, session: str | None = None) -> str:
    """Build a URL for an existing browser-local Thread, with optional focus."""
    if not _UUID.fullmatch(thread_id):
        raise ValueError("Invalid Thread UUID.")
    query: dict[str, str] = {}
    if session is not None:
        if not _SESSION_ID.fullmatch(session):
            raise ValueError("Invalid saved session ID.")
        query["session"] = session
    suffix = f"?{urlencode(query)}" if query else ""
    return f"https://dreamlake.ai/threads/{thread_id.lower()}{suffix}"
