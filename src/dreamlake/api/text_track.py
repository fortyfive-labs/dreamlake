"""
TextTrack — time-aligned text entries (captions, subtitles, annotations).

Entries are buffered in memory until .flush() writes them as JSONL,
uploads to BSS, and registers in dreamlake-server.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import httpx

from ._client import DreamLakeClient, get_client
from .prefix import resolve_path, resolve_space
from .resource_id import encode_resource_id

if TYPE_CHECKING:
    from .video import Video

CHUNK_SIZE = 10 * 1024 * 1024


class TextTrack:
    """A buffered text track that collects entries and uploads on flush."""

    def __init__(
        self,
        prefix: str | None = None,
        space: str | None = None,
        *,
        path: str | None = None,
        client: DreamLakeClient | None = None,
    ):
        self._prefix = resolve_path(prefix or path or "")
        self._space = resolve_space(space)
        self._client = client or get_client()
        self._entries: list[dict] = []
        self._id: str | None = None

    @property
    def id(self) -> str:
        return self._id or ""

    @property
    def prefix(self) -> str:
        return self._prefix

    @property
    def space(self) -> str | None:
        return self._space

    @property
    def count(self) -> int:
        return len(self._entries)

    def add(
        self,
        caption: str,
        *,
        source: Video | None = None,
        st: float | None = None,
        et: float | None = None,
        sf: int | None = None,
        ef: int | None = None,
    ) -> TextTrack:
        """Add a text entry. If source is a Video, st/et/sf/ef are inferred."""
        entry: dict = {"caption": caption}

        if source is not None:
            entry["st"] = source.st
            entry["et"] = source.et
            entry["duration"] = source.duration
            if hasattr(source, "fps"):
                fps = source.fps
                entry["sf"] = int((source.st - source._meta.get("st", 0)) * fps)
                entry["ef"] = int((source.et - source._meta.get("st", 0)) * fps)
        if st is not None:
            entry["st"] = st
        if et is not None:
            entry["et"] = et
        if sf is not None:
            entry["sf"] = sf
        if ef is not None:
            entry["ef"] = ef
        if "st" in entry and "et" in entry and "duration" not in entry:
            entry["duration"] = entry["et"] - entry["st"]

        self._entries.append(entry)
        return self

    def flush(self) -> str | None:
        """Write JSONL, upload to BSS, register in dreamlake-server. Returns asset ID."""
        if not self._entries:
            return None
        if not self._space:
            raise ValueError("space is required for flush. Set via dl.Prefix or space= arg.")

        # Parse space into namespace + space slug
        parts = self._space.split("@")
        if len(parts) == 2:
            space_slug, namespace = parts[0], parts[1]
        else:
            space_slug = parts[0]
            # Try to resolve namespace from auth
            try:
                me = self._client.get_auth_me()
                namespace = me.get("namespace", {}).get("slug", "")
            except Exception:
                raise ValueError("Cannot resolve namespace. Use space='space@namespace' format.")

        # Write JSONL to temp file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for entry in self._entries:
                f.write(json.dumps(entry) + "\n")
            tmp_path = f.name

        try:
            content = open(tmp_path, "rb").read()
            raw_hash = hashlib.sha256(content).hexdigest()[:16]
            file_size = len(content)
            total_parts = max(1, math.ceil(file_size / CHUNK_SIZE))

            # Multipart upload to BSS
            init = self._client.upload_init("text-tracks", namespace, space_slug, raw_hash, "application/x-jsonlines")
            upload_id, key = init["uploadId"], init["key"]

            part_urls = self._client.upload_parts("text-tracks", upload_id, key, list(range(1, total_parts + 1)))

            completed = []
            for pn in range(1, total_parts + 1):
                start = (pn - 1) * CHUNK_SIZE
                end = min(start + CHUNK_SIZE, file_size)
                chunk = content[start:end]
                r = httpx.put(part_urls[str(pn)], content=chunk, headers={"Content-Type": "application/x-jsonlines"}, timeout=120)
                r.raise_for_status()
                completed.append({"partNumber": pn, "etag": r.headers["etag"]})

            self._client.upload_complete("text-tracks", upload_id, key, completed)

            # Register in BSS
            filename = os.path.basename(self._prefix) or "track"
            bss_result = self._client.register_bss_asset("text-track", {
                "name": f"/{self._prefix}/{filename}.jsonl",
                "owner": namespace,
                "project": space_slug,
                "stagingHash": raw_hash,
                "format": "jsonl",
            })
            bss_id = bss_result.get("id")

            # Extract episode name from prefix
            prefix_parts = self._prefix.strip("/").split("/")
            episode_name = "/".join(prefix_parts[:3]) if len(prefix_parts) >= 3 else prefix_parts[0] if prefix_parts else ""

            # Register in dreamlake-server
            dl_result = self._client.register_dl_asset("text-track", {
                "namespace": namespace,
                "space": space_slug,
                "episodeName": episode_name,
                "name": f"/{self._prefix}",
                "bssTextTrackId": bss_id,
            })

            self._id = dl_result.get("id")
            self._entries.clear()
            return self._id

        finally:
            os.unlink(tmp_path)

    def __repr__(self) -> str:
        return f'TextTrack("{self._prefix}", space="{self._space}", count={self.count})'
