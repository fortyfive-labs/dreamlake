"""
Remote API client for dreamlake server.
"""

from typing import Optional, Dict, Any, List
import httpx


class RemoteClient:
    """Client for communicating with dreamlake server."""

    def __init__(self, base_url: str, api_key: str):
        """
        Initialize remote client.

        Args:
            base_url: Base URL of dreamlake server (e.g., "http://localhost:3000")
            api_key: JWT token for authentication
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                # Note: Don't set Content-Type here as default
                # It will be set per-request (json or multipart)
            },
            timeout=30.0,
        )

    def create_or_update_session(
        self,
        workspace: str,
        name: str,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        folder: Optional[str] = None,
        write_protected: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create or update a session.

        Args:
            workspace: Workspace name
            name: Session name
            description: Optional description
            tags: Optional list of tags
            folder: Optional folder path
            write_protected: If True, session becomes immutable
            metadata: Optional metadata dict

        Returns:
            Response dict with session, workspace, folder, and namespace data

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        payload = {
            "name": name,
        }

        if description is not None:
            payload["description"] = description
        if tags is not None:
            payload["tags"] = tags
        if folder is not None:
            payload["folder"] = folder
        if write_protected:
            payload["writeProtected"] = write_protected
        if metadata is not None:
            payload["metadata"] = metadata

        response = self._client.post(
            f"/workspaces/{workspace}/sessions",
            json=payload,
        )
        response.raise_for_status()
        return response.json()

    def create_log_entries(
        self,
        session_id: str,
        logs: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Create log entries in batch.

        Supports both single log and multiple logs via array.

        Args:
            session_id: Session ID (Snowflake ID)
            logs: List of log entries, each with fields:
                - timestamp: ISO 8601 string
                - level: "info"|"warn"|"error"|"debug"|"fatal"
                - message: Log message string
                - metadata: Optional dict

        Returns:
            Response dict:
            {
                "created": 1,
                "startSequence": 42,
                "endSequence": 42,
                "sessionId": "123456789"
            }

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        response = self._client.post(
            f"/sessions/{session_id}/logs",
            json={"logs": logs}
        )
        response.raise_for_status()
        return response.json()

    def set_parameters(
        self,
        session_id: str,
        data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Set/merge parameters for a session.

        Always merges with existing parameters (upsert behavior).

        Args:
            session_id: Session ID (Snowflake ID)
            data: Flattened parameter dict with dot notation
                Example: {"model.lr": 0.001, "model.batch_size": 32}

        Returns:
            Response dict:
            {
                "id": "snowflake_id",
                "sessionId": "session_id",
                "data": {...},
                "version": 2,
                "createdAt": "...",
                "updatedAt": "..."
            }

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        response = self._client.post(
            f"/sessions/{session_id}/parameters",
            json={"data": data}
        )
        response.raise_for_status()
        return response.json()

    def get_parameters(self, session_id: str) -> Dict[str, Any]:
        """
        Get parameters for a session.

        Args:
            session_id: Session ID (Snowflake ID)

        Returns:
            Flattened parameter dict with dot notation
            Example: {"model.lr": 0.001, "model.batch_size": 32}

        Raises:
            httpx.HTTPStatusError: If request fails or parameters don't exist
        """
        response = self._client.get(f"/sessions/{session_id}/parameters")
        response.raise_for_status()
        result = response.json()
        return result.get("data", {})

    def upload_file(
        self,
        session_id: str,
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
        Upload a file to a session.

        Args:
            session_id: Session ID (Snowflake ID)
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
            f"/sessions/{session_id}/files",
            files=files,
            data=data
        )

        response.raise_for_status()
        return response.json()

    def list_files(
        self,
        session_id: str,
        prefix: Optional[str] = None,
        tags: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        List files in a session.

        Args:
            session_id: Session ID (Snowflake ID)
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
            f"/sessions/{session_id}/files",
            params=params
        )
        response.raise_for_status()
        result = response.json()
        return result.get("files", [])

    def get_file(self, session_id: str, file_id: str) -> Dict[str, Any]:
        """
        Get file metadata.

        Args:
            session_id: Session ID (Snowflake ID)
            file_id: File ID (Snowflake ID)

        Returns:
            File metadata dict

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        response = self._client.get(f"/sessions/{session_id}/files/{file_id}")
        response.raise_for_status()
        return response.json()

    def download_file(
        self,
        session_id: str,
        file_id: str,
        dest_path: Optional[str] = None
    ) -> str:
        """
        Download a file from a session.

        Args:
            session_id: Session ID (Snowflake ID)
            file_id: File ID (Snowflake ID)
            dest_path: Optional destination path (defaults to original filename)

        Returns:
            Path to downloaded file

        Raises:
            httpx.HTTPStatusError: If request fails
            ValueError: If checksum verification fails
        """
        # Get file metadata first to get filename and checksum
        file_metadata = self.get_file(session_id, file_id)
        filename = file_metadata["filename"]
        expected_checksum = file_metadata["checksum"]

        # Determine destination path
        if dest_path is None:
            dest_path = filename

        # Download file
        response = self._client.get(
            f"/sessions/{session_id}/files/{file_id}/download"
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

    def delete_file(self, session_id: str, file_id: str) -> Dict[str, Any]:
        """
        Delete a file (soft delete).

        Args:
            session_id: Session ID (Snowflake ID)
            file_id: File ID (Snowflake ID)

        Returns:
            Dict with id and deletedAt

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        response = self._client.delete(f"/sessions/{session_id}/files/{file_id}")
        response.raise_for_status()
        return response.json()

    def update_file(
        self,
        session_id: str,
        file_id: str,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Update file metadata.

        Args:
            session_id: Session ID (Snowflake ID)
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
            f"/sessions/{session_id}/files/{file_id}",
            json=payload
        )
        response.raise_for_status()
        return response.json()

    def append_to_track(
        self,
        session_id: str,
        track_name: str,
        data: Dict[str, Any],
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Append a single data point to a track.

        Args:
            session_id: Session ID (Snowflake ID)
            track_name: Track name (unique within session)
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
            f"/sessions/{session_id}/tracks/{track_name}/append",
            json=payload
        )
        response.raise_for_status()
        return response.json()

    def append_batch_to_track(
        self,
        session_id: str,
        track_name: str,
        data_points: List[Dict[str, Any]],
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Append multiple data points to a track in batch.

        Args:
            session_id: Session ID (Snowflake ID)
            track_name: Track name (unique within session)
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
            f"/sessions/{session_id}/tracks/{track_name}/append-batch",
            json=payload
        )
        response.raise_for_status()
        return response.json()

    def read_track_data(
        self,
        session_id: str,
        track_name: str,
        start_index: int = 0,
        limit: int = 1000
    ) -> Dict[str, Any]:
        """
        Read data points from a track.

        Args:
            session_id: Session ID (Snowflake ID)
            track_name: Track name
            start_index: Starting index (default 0)
            limit: Max points to read (default 1000, max 10000)

        Returns:
            Dict with data, startIndex, endIndex, total, hasMore

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        response = self._client.get(
            f"/sessions/{session_id}/tracks/{track_name}/data",
            params={"startIndex": start_index, "limit": limit}
        )
        response.raise_for_status()
        return response.json()

    def get_track_stats(
        self,
        session_id: str,
        track_name: str
    ) -> Dict[str, Any]:
        """
        Get track statistics and metadata.

        Args:
            session_id: Session ID (Snowflake ID)
            track_name: Track name

        Returns:
            Dict with track stats (totalDataPoints, bufferedDataPoints, etc.)

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        response = self._client.get(
            f"/sessions/{session_id}/tracks/{track_name}/stats"
        )
        response.raise_for_status()
        return response.json()

    def list_tracks(
        self,
        session_id: str
    ) -> List[Dict[str, Any]]:
        """
        List all tracks in a session.

        Args:
            session_id: Session ID (Snowflake ID)

        Returns:
            List of track summaries

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        response = self._client.get(f"/sessions/{session_id}/tracks")
        response.raise_for_status()
        return response.json()["tracks"]

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
