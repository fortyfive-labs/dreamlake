"""
TextTrack — time-aligned text entries (captions, subtitles, annotations).

Entries are buffered in memory until .flush() writes them as JSONL,
puts it into S3, and registers in dreamlake-server.
"""

from __future__ import annotations

import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import httpx

from ._client import DreamLakeClient, get_client
from .prefix import resolve_path, resolve_project
from .resource_id import encode_resource_id

if TYPE_CHECKING:
    from .video import Video

CHUNK_SIZE = 10 * 1024 * 1024


class TextTrack:
    """A buffered text track that collects entries and uploads on flush."""

    def __init__(
        self,
        prefix: str | None = None,
        project: str | None = None,
        *,
        path: str | None = None,
        client: DreamLakeClient | None = None,
    ):
        self._prefix = resolve_path(prefix or path or "")
        self._project = resolve_project(project)
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
    def project(self) -> str | None:
        return self._project

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
        """Write JSONL, put it into S3, register in dreamlake-server. Returns the node ID."""
        if not self._entries:
            return None
        if not self._project:
            raise ValueError("project is required for flush. Set via dl.Prefix or project= arg.")

        # Parse project into namespace + project slug
        parts = self._project.split("@")
        if len(parts) == 2:
            project_slug, namespace = parts[0], parts[1]
        else:
            project_slug = parts[0]
            # Try to resolve namespace from auth
            try:
                me = self._client.get_auth_me()
                namespace = me.get("namespace", {}).get("slug", "")
            except Exception:
                raise ValueError("Cannot resolve namespace. Use project='project@namespace' format.")

        # Write JSONL to temp file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for entry in self._entries:
                f.write(json.dumps(entry) + "\n")
            tmp_path = f.name

        try:
            from pathlib import Path

            from ._transfer import (
                SignedOperation,
                parse_multipart,
                put_single,
                run_multipart,
                sha256_file,
            )

            tmp = Path(tmp_path)
            # Full sha256, not the first 16 characters: the authorize endpoint
            # refuses anything else, because the hash is the only
            # client-controlled part of the object key.
            raw_hash = sha256_file(tmp)
            file_size = tmp.stat().st_size

            filename = os.path.basename(self._prefix) or "track"
            full_name = f"/{self._prefix}/{filename}.jsonl"

            authorization = self._client.authorize_upload(
                namespace,
                project_slug,
                [{
                    "name": full_name,
                    "size": file_size,
                    "hash": raw_hash,
                    "mediaType": "application/x-jsonlines",
                }],
            )
            objects = authorization.get("objects") or []
            if not objects or not isinstance(objects[0].get("objectKey"), str):
                raise RuntimeError("the server authorized no upload for this track")
            object_key = objects[0]["objectKey"]
            transfer = objects[0].get("transfer") or {}

            if transfer.get("kind") == "single-put":
                put_single(
                    httpx.Client(),
                    SignedOperation.parse(transfer.get("put"), "text-track.put"),
                    tmp,
                    file_size,
                )
            else:
                run_multipart(
                    httpx.Client(),
                    parse_multipart(transfer, file_size, "text-track"),
                    tmp,
                    file_size,
                )

            # Extract episode name from prefix
            prefix_parts = self._prefix.strip("/").split("/")
            episode_name = "/".join(prefix_parts[:3]) if len(prefix_parts) >= 3 else prefix_parts[0] if prefix_parts else ""

            # Register in dreamlake-server. One registration, in the shape
            # `POST /nodes` actually declares — the previous body had no
            # `kind` at all, which that endpoint requires.
            node_body = {
                "namespace": namespace,
                "kind": "text-track",
                "name": full_name,
                "project": project_slug,
                "metadata": {
                    "s3Key": object_key,
                    "hash": raw_hash,
                    "size": file_size,
                    "contentType": "application/x-jsonlines",
                    "format": "jsonl",
                },
            }
            if episode_name:
                node_body["episode"] = episode_name
            dl_result = self._client.register_node(node_body)

            self._id = dl_result.get("id")
            self._entries.clear()
            return self._id

        finally:
            os.unlink(tmp_path)

    def __repr__(self) -> str:
        return f'TextTrack("{self._prefix}", project="{self._project}", count={self.count})'
