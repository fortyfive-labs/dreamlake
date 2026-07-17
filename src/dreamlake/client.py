"""
Remote API client for dreamlake server.

Episodes are addressed through their namespace + project:
    POST /namespaces/{namespace}/projects/{project}/episodes
Episode-scoped resources (logs, parameters, files, tracks) are addressed
by episode id:
    POST /episodes/{episode_id}/logs
"""

from typing import Optional, Dict, Any, List
from urllib.parse import quote

import httpx

_TOKEN_KEY = "dreamlake-token"


def _seg(value: str) -> str:
    """URL-encode a single path segment (same as encodeURIComponent)."""
    return quote(value, safe="")


class RemoteClient:
    """Client for communicating with dreamlake server."""

    def __init__(self, base_url: str, api_key: Optional[str] = None):
        """
        Initialize remote client.

        Args:
            base_url: Base URL of dreamlake server (e.g., "http://localhost:3000")
            api_key: JWT token for authentication. If omitted, auto-loaded from
                     secure token storage (set via `dreamlake login`).
        """
        self.base_url = base_url.rstrip("/")

        if not api_key:
            from dreamlake.auth.token_storage import get_token_storage
            from dreamlake.auth.exceptions import NotAuthenticatedError
            token = get_token_storage().load(_TOKEN_KEY)
            if not token:
                raise NotAuthenticatedError(
                    "Not authenticated. Run 'dreamlake login' to log in."
                )
            api_key = token
        self._api_key = api_key
        self._default_namespace: Optional[str] = None
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                # Note: Don't set Content-Type here as default
                # It will be set per-request (json or multipart)
            },
            timeout=30.0,
        )

    @property
    def api_key(self) -> str:
        """The bearer token used for authentication."""
        return self._api_key

    @api_key.setter
    def api_key(self, value: str) -> None:
        """
        Swap the auth token.

        Rebuilds the Authorization header on the underlying HTTP client and
        drops the cached default namespace (it belongs to the old identity).
        """
        self._api_key = value
        self._client.headers["Authorization"] = f"Bearer {value}"
        self._default_namespace = None

    def get_me(self) -> Dict[str, Any]:
        """
        Get the authenticated user profile.

        On first call for a new token, the server registers the user and
        allocates their personal namespace.

        Returns:
            User profile dict with id, sub, email, name, and namespace
            ({"id": ..., "slug": ...}) fields

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        response = self._client.get(
            "/auth/me",
        )
        response.raise_for_status()
        return response.json()

    def resolve_namespace(self) -> str:
        """
        Resolve the authenticated user's default namespace slug (cached).

        Returns:
            Namespace slug string

        Raises:
            RuntimeError: If the server reports no namespace for the user
            httpx.HTTPStatusError: If request fails
        """
        if self._default_namespace:
            return self._default_namespace

        me = self.get_me()
        namespace = (me.get("namespace") or {}).get("slug")
        if not namespace:
            raise RuntimeError(
                "Server returned no namespace for the authenticated user. "
                "Pass a namespace explicitly (prefix='namespace/project/name')."
            )
        self._default_namespace = namespace
        return namespace

    def create_project(
        self,
        namespace: str,
        name: str,
        slug: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a project in a namespace.

        Args:
            namespace: Namespace slug
            name: Project display name
            slug: Optional URL-safe slug (derived from name if omitted)
            description: Optional description

        Returns:
            Created project dict (id, name, slug, ...)

        Raises:
            httpx.HTTPStatusError: If request fails (409 if it already exists)
        """
        payload: Dict[str, Any] = {
            "name": name,
        }
        if slug is not None:
            payload["slug"] = slug
        if description is not None:
            payload["description"] = description

        response = self._client.post(
            f"/namespaces/{_seg(namespace)}/projects",
            json=payload,
        )
        response.raise_for_status()
        return response.json()

    def create_or_update_episode(
        self,
        namespace: str,
        project: str,
        name: str,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        status: Optional[str] = None,
        write_protected: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create or update (upsert) an episode in a project.

        If the project does not exist yet, it is created automatically.

        Args:
            namespace: Namespace slug
            project: Project slug
            name: Episode name (unique within project)
            description: Optional description
            tags: Optional list of tags
            status: Optional status (e.g. "running", "completed", "failed")
            write_protected: If True, episode becomes immutable
            metadata: Optional metadata dict

        Returns:
            Episode dict: id, name, description, status, writeProtected,
            tags, projectNodeId, createdAt, updatedAt

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        payload: Dict[str, Any] = {
            "name": name,
        }

        if description is not None:
            payload["description"] = description
        if tags is not None:
            payload["tags"] = tags
        if status is not None:
            payload["status"] = status
        if write_protected:
            payload["writeProtected"] = write_protected
        if metadata is not None:
            payload["metadata"] = metadata

        path = f"/namespaces/{_seg(namespace)}/projects/{_seg(project)}/episodes"
        response = self._client.post(
            path,
            json=payload,
        )

        if response.status_code == 404:
            # Project (or namespace) not found — auto-create the project,
            # then retry the episode upsert once.
            try:
                self.create_project(
                    namespace=namespace,
                    name=project,
                    slug=project,
                )
            except httpx.HTTPStatusError as error:
                if error.response.status_code != 409:
                    raise
            response = self._client.post(
                path,
                json=payload,
            )

        response.raise_for_status()
        return response.json()

    def create_log_entries(
        self,
        episode_id: str,
        logs: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Create log entries in batch.

        Supports both single log and multiple logs via array.

        Args:
            episode_id: Episode ID (Snowflake ID)
            logs: List of log entries, each with fields:
                - timestamp: ISO 8601 string
                - level: "debug"|"info"|"warn"|"error" (the current server
                  rejects "fatal": its route schema only allows "critical",
                  while its service only allows "fatal")
                - message: Log message string
                - metadata: Optional dict

        Returns:
            Response dict:
            {
                "created": 1,
                "startSequence": 42,
                "endSequence": 42,
                "episodeId": "123456789"
            }

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        response = self._client.post(
            f"/episodes/{_seg(episode_id)}/logs",
            json={"logs": logs}
        )
        response.raise_for_status()
        return response.json()

    def set_parameters(
        self,
        episode_id: str,
        data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Set/merge parameters for a episode.

        Always merges with existing parameters (upsert behavior).

        Args:
            episode_id: Episode ID (Snowflake ID)
            data: Flattened parameter dict with dot notation
                Example: {"model.lr": 0.001, "model.batch_size": 32}

        Returns:
            Response dict:
            {
                "id": "snowflake_id",
                "episodeId": "episode_id",
                "data": {...},
                "version": 2,
                "createdAt": "...",
                "updatedAt": "..."
            }

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        response = self._client.post(
            f"/episodes/{_seg(episode_id)}/parameters",
            json={"data": data}
        )
        response.raise_for_status()
        return response.json()

    def get_parameters(self, episode_id: str) -> Dict[str, Any]:
        """
        Get parameters for a episode.

        Args:
            episode_id: Episode ID (Snowflake ID)

        Returns:
            Flattened parameter dict with dot notation
            Example: {"model.lr": 0.001, "model.batch_size": 32}

        Raises:
            httpx.HTTPStatusError: If request fails or parameters don't exist
        """
        response = self._client.get(f"/episodes/{_seg(episode_id)}/parameters")
        response.raise_for_status()
        result = response.json()
        return result.get("data", {})

    def upload_file(
        self,
        episode_id: str,
        file_path: str,
        prefix: str,
        filename: str,
        description: Optional[str],
        tags: Optional[List[str]],
        metadata: Optional[Dict[str, Any]],
        checksum: str,
        content_type: str,
        size_bytes: int
    ) -> Dict[str, Any]:
        """
        Upload a file to a episode.

        Args:
            episode_id: Episode ID (Snowflake ID)
            file_path: Local file path
            prefix: Logical path prefix
            filename: Original filename
            description: Optional description
            tags: Optional tags
            metadata: Optional metadata
            checksum: SHA256 checksum
            content_type: MIME type
            size_bytes: File size in bytes

        Returns:
            File metadata dict

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        # Prepare multipart form data
        # Read file content first (httpx needs content, not file handle)
        with open(file_path, "rb") as f:
            file_content = f.read()

        files = {"file": (filename, file_content, content_type)}
        data = {
            "prefix": prefix,
            "checksum": checksum,
            "sizeBytes": str(size_bytes),
        }
        if description:
            data["description"] = description
        if tags:
            data["tags"] = ",".join(tags)
        if metadata:
            import json
            data["metadata"] = json.dumps(metadata)

        # httpx will automatically set multipart/form-data content-type
        response = self._client.post(
            f"/episodes/{_seg(episode_id)}/files",
            files=files,
            data=data
        )

        response.raise_for_status()
        return response.json()

    def list_files(
        self,
        episode_id: str,
        prefix: Optional[str] = None,
        tags: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        List files in a episode.

        Args:
            episode_id: Episode ID (Snowflake ID)
            prefix: Optional prefix filter
            tags: Optional tags filter

        Returns:
            List of file metadata dicts

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        params = {}
        if prefix:
            params["prefix"] = prefix
        if tags:
            params["tags"] = ",".join(tags)

        response = self._client.get(
            f"/episodes/{_seg(episode_id)}/files",
            params=params
        )
        response.raise_for_status()
        result = response.json()
        return result.get("files", [])

    def get_file(self, episode_id: str, file_id: str) -> Dict[str, Any]:
        """
        Get file metadata.

        Args:
            episode_id: Episode ID (Snowflake ID)
            file_id: File ID (Snowflake ID)

        Returns:
            File metadata dict

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        response = self._client.get(f"/episodes/{_seg(episode_id)}/files/{_seg(file_id)}")
        response.raise_for_status()
        return response.json()

    def download_file(
        self,
        episode_id: str,
        file_id: str,
        dest_path: Optional[str] = None
    ) -> str:
        """
        Download a file from a episode.

        Args:
            episode_id: Episode ID (Snowflake ID)
            file_id: File ID (Snowflake ID)
            dest_path: Optional destination path (defaults to original filename)

        Returns:
            Path to downloaded file

        Raises:
            httpx.HTTPStatusError: If request fails
            ValueError: If checksum verification fails
        """
        # Get file metadata first to get filename and checksum
        file_metadata = self.get_file(episode_id, file_id)
        filename = file_metadata["filename"]
        expected_checksum = file_metadata["checksum"]

        # Determine destination path
        if dest_path is None:
            dest_path = filename

        # Download file
        response = self._client.get(
            f"/episodes/{_seg(episode_id)}/files/{_seg(file_id)}/download"
        )
        response.raise_for_status()

        # Write to file
        with open(dest_path, "wb") as f:
            f.write(response.content)

        # Verify checksum
        from .files import verify_checksum
        if not verify_checksum(dest_path, expected_checksum):
            # Delete corrupted file
            import os
            os.remove(dest_path)
            raise ValueError(f"Checksum verification failed for file {file_id}")

        return dest_path

    def delete_file(self, episode_id: str, file_id: str) -> Dict[str, Any]:
        """
        Delete a file (soft delete).

        Args:
            episode_id: Episode ID (Snowflake ID)
            file_id: File ID (Snowflake ID)

        Returns:
            Dict with id and deletedAt

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        response = self._client.delete(f"/episodes/{_seg(episode_id)}/files/{_seg(file_id)}")
        response.raise_for_status()
        return response.json()

    def update_file(
        self,
        episode_id: str,
        file_id: str,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Update file metadata.

        Args:
            episode_id: Episode ID (Snowflake ID)
            file_id: File ID (Snowflake ID)
            description: Optional description
            tags: Optional tags
            metadata: Optional metadata

        Returns:
            Updated file metadata dict

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        payload = {}
        if description is not None:
            payload["description"] = description
        if tags is not None:
            payload["tags"] = tags
        if metadata is not None:
            payload["metadata"] = metadata

        response = self._client.patch(
            f"/episodes/{_seg(episode_id)}/files/{_seg(file_id)}",
            json=payload
        )
        response.raise_for_status()
        return response.json()

    def append_to_track(
        self,
        episode_id: str,
        track_name: str,
        data: Dict[str, Any],
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Append a single data point to a track.

        Args:
            episode_id: Episode ID (Snowflake ID)
            track_name: Track name (unique within episode)
            data: Data point (flexible schema)
            description: Optional track description
            tags: Optional tags
            metadata: Optional metadata

        Returns:
            Dict with trackId, index, bufferedDataPoints, chunkSize

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        payload = {"data": data}
        if description:
            payload["description"] = description
        if tags:
            payload["tags"] = tags
        if metadata:
            payload["metadata"] = metadata

        response = self._client.post(
            f"/episodes/{_seg(episode_id)}/tracks/{_seg(track_name)}/append",
            json=payload
        )
        response.raise_for_status()
        return response.json()

    def append_batch_to_track(
        self,
        episode_id: str,
        track_name: str,
        data_points: List[Dict[str, Any]],
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Append multiple data points to a track in batch.

        Args:
            episode_id: Episode ID (Snowflake ID)
            track_name: Track name (unique within episode)
            data_points: List of data points
            description: Optional track description
            tags: Optional tags
            metadata: Optional metadata

        Returns:
            Dict with trackId, startIndex, endIndex, count, bufferedDataPoints, chunkSize

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        payload = {"dataPoints": data_points}
        if description:
            payload["description"] = description
        if tags:
            payload["tags"] = tags
        if metadata:
            payload["metadata"] = metadata

        response = self._client.post(
            f"/episodes/{_seg(episode_id)}/tracks/{_seg(track_name)}/append-batch",
            json=payload
        )
        response.raise_for_status()
        return response.json()

    def read_track_data(
        self,
        episode_id: str,
        track_name: str,
        start_index: int = 0,
        limit: int = 1000
    ) -> Dict[str, Any]:
        """
        Read data points from a track.

        Args:
            episode_id: Episode ID (Snowflake ID)
            track_name: Track name
            start_index: Starting index (default 0)
            limit: Max points to read (default 1000, max 10000)

        Returns:
            Dict with data, startIndex, endIndex, total, hasMore — the same
            shape LocalStorage.read_track_data returns, so callers see one
            schema in every mode. (The server's raw response is
            {trackName, points, startIndex, count}; it is normalized here,
            at the client boundary. hasMore is best-effort: the server does
            not report the track total, so a full page implies more data.)

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        response = self._client.get(
            f"/episodes/{_seg(episode_id)}/tracks/{_seg(track_name)}/data",
            params={
                "startIndex": start_index,
                "limit": limit,
            },
        )
        response.raise_for_status()
        payload = response.json()

        points = payload.get("points", payload.get("data", []))
        response_start = payload.get("startIndex", start_index)
        count = payload.get("count", len(points))
        return {
            "data": points,
            "startIndex": response_start,
            "endIndex": response_start + count - 1 if count else response_start - 1,
            "total": count,
            "hasMore": bool(count) and count >= limit,
        }

    def get_track_stats(
        self,
        episode_id: str,
        track_name: str
    ) -> Dict[str, Any]:
        """
        Get track statistics and metadata.

        Args:
            episode_id: Episode ID (Snowflake ID)
            track_name: Track name

        Returns:
            Dict with track stats (totalDataPoints, bufferedDataPoints, etc.)

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        response = self._client.get(
            f"/episodes/{_seg(episode_id)}/tracks/{_seg(track_name)}/stats"
        )
        response.raise_for_status()
        return response.json()

    def list_tracks(
        self,
        episode_id: str
    ) -> List[Dict[str, Any]]:
        """
        List all tracks in a episode.

        Args:
            episode_id: Episode ID (Snowflake ID)

        Returns:
            List of track summaries

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        response = self._client.get(f"/episodes/{_seg(episode_id)}/tracks")
        response.raise_for_status()
        return response.json()["tracks"]

    # ── Vector Search ──────────────────────────────────────────────

    def search_vectors(
        self,
        space_id: str,
        query: list,
        model_id: Optional[str] = None,
        mod: Optional[str] = None,
        sid: Optional[str] = None,
        rid: Optional[str] = None,
        st: Optional[float] = None,
        et: Optional[float] = None,
        limit: int = 10,
        min_score: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Search for similar vectors within a project."""
        payload: Dict[str, Any] = {"query": query, "limit": limit}
        if model_id:
            payload["modelId"] = model_id
        if mod:
            payload["mod"] = mod
        if sid:
            payload["sid"] = sid
        if rid:
            payload["rid"] = rid
        if st is not None:
            payload["st"] = st
        if et is not None:
            payload["et"] = et
        if min_score is not None:
            payload["minScore"] = min_score

        response = self._client.post(f"/projects/{_seg(space_id)}/search", json=payload)
        response.raise_for_status()
        return response.json()

    def upsert_vectors(
        self,
        space_id: str,
        points: list,
        vector_size: int,
        model_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upsert pre-computed vectors into Qdrant."""
        payload: Dict[str, Any] = {"points": points, "vectorSize": vector_size}
        if model_id:
            payload["modelId"] = model_id

        response = self._client.post(f"/projects/{_seg(space_id)}/vectors", json=payload)
        response.raise_for_status()
        return response.json()

    def list_vector_indexes(self, space_id: str) -> Dict[str, Any]:
        """List vector indexes for a project."""
        response = self._client.get(f"/projects/{_seg(space_id)}/vector-indexes")
        response.raise_for_status()
        return response.json()

    def close(self):
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
        return False
