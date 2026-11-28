"""Read and change your saved order of browser-registered Threads."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TypedDict


class ThreadOrderEntry(TypedDict):
    id: str
    name: str


class ThreadOrder(TypedDict):
    revision: int
    threads: list[ThreadOrderEntry]


def _path(namespace: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", namespace):
        raise ValueError("An explicit namespace slug is required")
    return f"/namespaces/{namespace}/thread-order"


class Threads:
    def __init__(self, client):
        self.client = client

    def order(self, namespace: str) -> ThreadOrder:
        """Return the ordered IDs, names and revision for this namespace."""
        with self.client.http() as http:
            response = http.get(_path(namespace))
            response.raise_for_status()
            return response.json()

    def reorder(
        self, namespace: str, thread_ids: Sequence[str], *, expected_revision: int
    ) -> ThreadOrder:
        """Write a complete permutation using the revision returned by order()."""
        if isinstance(thread_ids, (str, bytes)):
            raise ValueError("Thread IDs must be a sequence of IDs")
        with self.client.http() as http:
            response = http.patch(
                _path(namespace),
                json={
                    "expectedRevision": expected_revision,
                    "threadIds": list(thread_ids),
                },
            )
            response.raise_for_status()
            return response.json()
