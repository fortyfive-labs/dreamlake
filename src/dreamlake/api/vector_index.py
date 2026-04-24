"""
VectorIndex — named vector index backed by Qdrant.

Lazy-created on first .add(). Supports search with text or vector queries.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from ._client import DreamLakeClient, get_client

if TYPE_CHECKING:
    from .video import Video


@dataclass
class SearchResult:
    score: float
    caption: str | None
    payload: dict


class VectorIndex:
    """Named vector index backed by Qdrant."""

    def __init__(
        self,
        name: str,
        client: DreamLakeClient | None = None,
        dim: int = 768,
    ):
        self._name = name
        self._client = client or get_client()
        self._dim = dim
        self._created = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def count(self) -> int:
        return self._client.qdrant_count(self._name)

    @property
    def dim(self) -> int:
        return self._dim

    def _ensure_collection(self) -> None:
        if not self._created:
            self._client.qdrant_ensure_collection(self._name, self._dim)
            self._created = True

    def add(
        self,
        vector,
        caption: str | None = None,
        source: Video | None = None,
        **extra_payload,
    ) -> VectorIndex:
        """Add a vector to the index.

        Args:
            vector: numpy array or list of floats
            caption: optional text caption
            source: optional Video (extracts st, et, sf, ef, videoId)
            **extra_payload: additional metadata
        """
        self._ensure_collection()

        if isinstance(vector, np.ndarray):
            vec_list = vector.tolist()
        elif isinstance(vector, list):
            vec_list = vector
        else:
            vec_list = list(vector)

        # Auto-detect dim from first vector
        if len(vec_list) != self._dim:
            self._dim = len(vec_list)

        payload = dict(extra_payload)
        if caption:
            payload["caption"] = caption

        if source is not None:
            payload["st"] = source.st
            payload["et"] = source.et
            payload["duration"] = source.duration
            if source._video_id:
                payload["videoId"] = source._video_id
            if hasattr(source, "fps"):
                fps = source.fps
                meta_st = source._meta.get("st", 0) if source.__dict__.get("_Video__meta") else 0
                payload["sf"] = int((source.st - meta_st) * fps)
                payload["ef"] = int((source.et - meta_st) * fps)

        vectors: dict = {"image": vec_list}

        point = {
            "id": str(uuid.uuid4()),
            "vector": vectors,
            "payload": payload,
        }

        self._client.qdrant_upsert(self._name, [point])
        return self

    def search(self, query, limit: int = 10, using: str = "image") -> list[SearchResult]:
        """Search the index.

        Args:
            query: text string (requires CLIP service) or vector (list/numpy)
            limit: max results
            using: which vector to search against ("image" or "caption")
        """
        if isinstance(query, str):
            # Text query → need to embed via CLIP
            import httpx
            clip_url = self._client.bss_url.replace(":10234", ":8000")  # hacky
            r = httpx.post(f"{clip_url}/embed/text", json={"text": query}, timeout=30)
            r.raise_for_status()
            vec = r.json()["embedding"]
        elif isinstance(query, np.ndarray):
            vec = query.tolist()
        else:
            vec = list(query)

        results = self._client.qdrant_search(self._name, vec, using=using, limit=limit)
        return [
            SearchResult(
                score=r.get("score", 0),
                caption=r.get("payload", {}).get("caption"),
                payload=r.get("payload", {}),
            )
            for r in results
        ]

    def __repr__(self) -> str:
        try:
            c = self.count
        except Exception:
            c = "?"
        return f'VectorIndex("{self._name}", count={c}, dim={self._dim})'
