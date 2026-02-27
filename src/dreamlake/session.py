"""
Session class for dreamlake SDK.

Supports three usage styles:
1. Decorator: @dreamlake_session(...)
2. Context manager: with Session(...) as sess:
3. Direct instantiation: sess = Session(...)
"""

from typing import Optional, Dict, Any, List, Callable
from enum import Enum
import functools
from pathlib import Path
from datetime import datetime
import time
import threading

from .client import RemoteClient
from .storage import LocalStorage
from .log import LogLevel, LogBuilder
from .params import ParametersBuilder
from .files import FileBuilder, FilesBuilder


class OperationMode(Enum):
    """Operation mode for the session."""
    LOCAL = "local"
    REMOTE = "remote"
    HYBRID = "hybrid"  # Future: sync local to remote


class RunManager:
    """
    Lifecycle manager for sessions (ML-Dash compatible).

    Supports context manager pattern for automatic session open/close:
        with Session(...).run as sess:
            sess.params.set(...)
    """

    def __init__(self, session: 'Session'):
        self._session = session

    def __enter__(self) -> 'Session':
        """Context manager entry - opens the session."""
        return self._session.open()

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - closes the session."""
        self._session.close()
        return False

    def start(self) -> 'Session':
        """Explicitly start the session (alternative to context manager)."""
        return self._session.open()

    def complete(self):
        """Mark session as complete and close it."""
        # TODO: Add status tracking (complete vs failed)
        self._session.close()

    def fail(self):
        """Mark session as failed and close it."""
        # TODO: Add status tracking (complete vs failed)
        self._session.close()


class MetricsManager:
    """
    Manager for metrics operations (ML-Dash compatible).

    Metrics are time-series data indexed by sequential integers.
    Usage:
        session.metrics("train/loss").log(value=0.5, epoch=1)
        session.metrics.flush()  # Flush all metrics
    """

    def __init__(self, session: 'Session'):
        self._session = session

    def __call__(self, name: str) -> 'TrackBuilder':
        """Get a TrackBuilder for the named metric."""
        from .track import TrackBuilder
        return self._session.track(name)

    def flush(self):
        """Flush all buffered metrics."""
        return self._session._flush_all_tracks()


class TracksManager:
    """
    Manager for tracks operations (ML-Dash compatible).

    Tracks are timestamped data indexed by float timestamps.
    Usage:
        # Named track
        session.tracks("robot/position").append(q=[0.1, 0.2], _ts=1.0)

        # Default track (uses track name "default")
        session.tracks.append(loss=0.5, epoch=1)

        # Flush all tracks
        session.tracks.flush()
    """

    def __init__(self, session: 'Session'):
        self._session = session
        self._default_track_name = "default"

    def __call__(self, topic: str) -> 'TrackBuilder':
        """Get a TrackBuilder for the named track topic."""
        from .track import TrackBuilder
        return self._session._track(topic)

    def append(self, _ts=None, **kwargs) -> 'TrackBuilder':
        """Append to default track."""
        return self._session._track(self._default_track_name).append(_ts=_ts, **kwargs)

    def log(self, **kwargs) -> 'TrackBuilder':
        """Log to default track (alias for append)."""
        return self._session._track(self._default_track_name).log(**kwargs)

    def flush(self):
        """Flush all buffered tracks."""
        return self._session._flush_all_tracks()

    def list(self) -> List['Dict[str, Any]']:
        """
        List all tracks in the session.

        Automatically flushes all buffered tracks before listing.

        Returns:
            List of track summaries

        Example:
            tracks = session.tracks.list()
            for track in tracks:
                print(f"{track['name']}: {track['totalDataPoints']} points")
        """
        # Auto-flush all tracks before listing
        self._session._flush_all_tracks()
        return self._session._list_tracks()


class Session:
    """
    DreamLake session for tracking ML experiments (ML-Dash compatible API).

    Usage examples:

    # Local mode (default)
    session = Session(prefix="my-workspace/my-experiment")

    # Custom local storage directory
    session = Session(
        prefix="my-workspace/my-experiment",
        root=".dreamlake"
    )

    # Remote mode (requires DREAMLAKE_API_KEY env var)
    session = Session(
        prefix="my-workspace/my-experiment",
        url="http://localhost:3000"
    )

    # Context manager (recommended)
    with Session(prefix="workspace/experiment") as sess:
        sess.params.set(lr=0.001)
        sess.logs.info("Training started")
        sess.metrics("train/loss").log(value=0.5)
    """

    def __init__(
        self,
        prefix: str,
        *,
        readme: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        # Mode configuration
        url: Optional[str] = None,
        root: Optional[str] = ".dreamlake",
        # Internal
        _write_protected: bool = False,
    ):
        """
        Initialize a DreamLake session (ML-Dash compatible API).

        Args:
            prefix: Experiment path like "workspace/name" or "owner/workspace/name"
            readme: Optional experiment description/readme
            tags: Optional list of tags
            metadata: Optional metadata dict
            url: Remote API URL (e.g., "http://localhost:3000"). None = local-only mode
            root: Local storage root path (defaults to ".dreamlake")
            _write_protected: Internal - if True, session becomes immutable after creation

        Prefix Format:
            - "workspace/name" → workspace="workspace", name="name"
            - "owner/workspace/name" → workspace="workspace", name="name"

        Mode Selection:
            - url=None: Local-only mode (writes to root)
            - url + root: Hybrid mode (local + remote)
            - url + root=None: Remote-only mode
        """
        # Parse prefix into components
        if not prefix:
            raise ValueError("prefix is required (format: 'workspace/name')")

        parts = prefix.strip("/").split("/")
        if len(parts) < 2:
            raise ValueError(f"prefix must have at least 2 segments (workspace/name), got: {prefix}")

        # Extract workspace (second-to-last or second segment) and name (last segment)
        self.workspace = parts[-2] if len(parts) >= 2 else parts[0]
        self.name = parts[-1]
        self.prefix = prefix

        self.readme = readme
        self.tags = tags
        self.write_protected = _write_protected
        self.metadata = metadata

        # Determine operation mode
        if url and root:
            self.mode = OperationMode.HYBRID
        elif url:
            self.mode = OperationMode.REMOTE
        elif root:
            self.mode = OperationMode.LOCAL
        else:
            raise ValueError(
                "Must specify either 'url' (remote) or 'root' (local)"
            )

        # Initialize backend
        self._client: Optional[RemoteClient] = None
        self._storage: Optional[LocalStorage] = None
        self._session_id: Optional[str] = None
        self._session_data: Optional[Dict[str, Any]] = None
        self._is_open = False

        # Track buffering for timestamp-based merging
        self._track_buffers: Dict[str, List[Dict[str, Any]]] = {}
        self._track_buffer_lock = threading.Lock()
        self._last_timestamp: Optional[float] = None  # Global last timestamp (for _ts=-1)
        self._track_last_auto_timestamp: float = 0.0  # Ensure unique auto-generated timestamps

        if self.mode in (OperationMode.REMOTE, OperationMode.HYBRID):
            # TODO: Auto-load API key from ~/.dreamlake/token (like ML-Dash does)
            # For now, require environment variable or fail gracefully
            import os
            api_key = os.environ.get("DREAMLAKE_API_KEY")
            if not api_key:
                raise ValueError(
                    "DREAMLAKE_API_KEY environment variable required for remote mode. "
                    "Set it or use root for local-only mode."
                )
            self._client = RemoteClient(base_url=url, api_key=api_key)

        if self.mode in (OperationMode.LOCAL, OperationMode.HYBRID):
            self._storage = LocalStorage(root_path=Path(root))

    @staticmethod
    def _generate_api_key_from_username(user_name: str) -> str:
        """
        Generate a deterministic API key (JWT) from username.

        This is a temporary solution until proper user authentication is implemented.
        Generates a unique user ID from the username and creates a JWT token.

        Args:
            user_name: Username to generate API key from

        Returns:
            JWT token string
        """
        import hashlib
        import time
        import jwt

        # Generate deterministic user ID from username (first 10 digits of SHA256 hash)
        user_id = str(int(hashlib.sha256(user_name.encode()).hexdigest()[:16], 16))[:10]

        # JWT payload
        payload = {
            "userId": user_id,
            "userName": user_name,
            "iat": int(time.time()),
            "exp": int(time.time()) + (30 * 24 * 60 * 60)  # 30 days expiration
        }

        # Secret key for signing (should match server's JWT_SECRET)
        secret = "your-secret-key-change-this-in-production"

        # Generate JWT
        token = jwt.encode(payload, secret, algorithm="HS256")

        return token

    def open(self) -> "Session":
        """
        Open the session (create or update on server/filesystem).

        Returns:
            self for chaining
        """
        if self._is_open:
            return self

        if self._client:
            # Remote mode: create/update session via API
            # TODO: Update client API to use readme instead of description
            response = self._client.create_or_update_session(
                workspace=self.workspace,
                name=self.name,
                description=self.readme,  # Map readme → description for now
                tags=self.tags,
                folder=None,  # Removed from ML-Dash API
                write_protected=self.write_protected,
                metadata=self.metadata,
            )
            self._session_data = response
            self._session_id = response["session"]["id"]

        if self._storage:
            # Local mode: create session directory structure
            # TODO: Update storage API to use readme instead of description
            self._storage.create_session(
                workspace=self.workspace,
                name=self.name,
                description=self.readme,  # Map readme → description for now
                tags=self.tags,
                folder=None,  # Removed from ML-Dash API
                metadata=self.metadata,
            )

        self._is_open = True
        return self

    def close(self):
        """Close the session and flush all buffered tracks."""
        if not self._is_open:
            return

        # Flush all buffered tracks
        self._flush_all_tracks()

        # Flush any pending writes
        if self._storage:
            self._storage.flush()

        self._is_open = False

    def __enter__(self) -> "Session":
        """Context manager entry."""
        return self.open()

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
        return False

    # ===== ML-Dash Compatible Property Aliases =====

    @property
    def run(self) -> RunManager:
        """
        Get run manager for lifecycle management (ML-Dash compatible property).

        Returns:
            RunManager instance supporting context manager and explicit start/complete

        Examples:
            # Context manager (recommended)
            with Session(prefix="workspace/experiment").run as sess:
                sess.params.set(lr=0.001)
                sess.logs.info("Training started")

            # Explicit lifecycle
            sess = Session(prefix="workspace/experiment")
            sess.run.start()
            # ... do work ...
            sess.run.complete()
        """
        return RunManager(self)

    @property
    def params(self) -> ParametersBuilder:
        """
        Get parameters builder (ML-Dash compatible property).

        Returns:
            ParametersBuilder instance for parameter operations

        Examples:
            session.params.set(lr=0.001, batch_size=32)
            params = session.params.get()
        """
        return self.parameters()

    @property
    def logs(self) -> LogBuilder:
        """
        Get log builder for fluent-style logging (ML-Dash compatible property).

        Returns:
            LogBuilder instance for fluent logging

        Examples:
            session.logs.info("Training started")
            session.logs.error("Failed", error_code=500)
        """
        if not self._is_open:
            raise RuntimeError("Session not open. Use session.open() or context manager.")
        return LogBuilder(self, None)

    @property
    def files(self) -> FilesBuilder:
        """
        Get files builder for file operations (ML-Dash compatible property).

        Returns:
            FilesBuilder instance for file operations

        Examples:
            session.files.upload("./model.pt", path="/models")
            files = session.files.list()
        """
        if not self._is_open:
            raise RuntimeError("Session not open. Use session.open() or context manager.")
        return FilesBuilder(self)

    @property
    def metrics(self) -> MetricsManager:
        """
        Get metrics manager for time-series data (ML-Dash compatible property).

        Returns:
            MetricsManager instance supporting both named and direct operations

        Examples:
            # Named metric
            session.metrics("train/loss").log(value=0.5, epoch=1)

            # Flush all metrics
            session.metrics.flush()
        """
        if not self._is_open:
            raise RuntimeError("Session not open. Use session.open() or context manager.")
        return MetricsManager(self)

    @property
    def tracks(self) -> TracksManager:
        """
        Get tracks manager for timestamped data (ML-Dash compatible property).

        Returns:
            TracksManager instance supporting both named and direct operations

        Examples:
            # Named track
            session.tracks("robot/position").append(q=[0.1, 0.2], _ts=1.0)

            # Flush all tracks
            session.tracks.flush()
        """
        if not self._is_open:
            raise RuntimeError("Session not open. Use session.open() or context manager.")
        return TracksManager(self)

    @property
    def track(self) -> TracksManager:
        """
        Get track manager (alias for tracks, supports both named and default patterns).

        Returns:
            TracksManager instance

        Examples:
            # Named track
            session.track("robot/position").append(x=1.0, y=2.0)

            # Default track
            session.track.append(loss=0.5, epoch=1)
        """
        return self.tracks

    # ===== End ML-Dash Property Aliases =====

    def log(
        self,
        message: Optional[str] = None,
        level: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **extra_metadata
    ) -> Optional[LogBuilder]:
        """
        Create a log entry or return a LogBuilder for fluent API.

        This method supports two styles:

        1. Fluent style (no message provided):
           Returns a LogBuilder that allows chaining with level methods.

           Examples:
               session.log(metadata={"epoch": 1}).info("Training started")
               session.log().error("Failed", error_code=500)

        2. Traditional style (message provided):
           Writes the log immediately and returns None.

           Examples:
               session.log("Training started", level="info", epoch=1)
               session.log("Training started")  # Defaults to "info"

        Args:
            message: Optional log message (for traditional style)
            level: Optional log level (for traditional style, defaults to "info")
            metadata: Optional metadata dict
            **extra_metadata: Additional metadata as keyword arguments

        Returns:
            LogBuilder if no message provided (fluent mode)
            None if log was written directly (traditional mode)

        Raises:
            RuntimeError: If session is not open
            ValueError: If log level is invalid
        """
        if not self._is_open:
            raise RuntimeError("Session not open. Use session.open() or context manager.")

        # Fluent mode: return LogBuilder
        if message is None:
            combined_metadata = {**(metadata or {}), **extra_metadata}
            return LogBuilder(self, combined_metadata if combined_metadata else None)

        # Traditional mode: write immediately
        level = level or LogLevel.INFO.value  # Default to "info"
        level = LogLevel.validate(level)  # Validate level

        combined_metadata = {**(metadata or {}), **extra_metadata}
        self._write_log(
            message=message,
            level=level,
            metadata=combined_metadata if combined_metadata else None,
            timestamp=None
        )
        return None

    def _write_log(
        self,
        message: str,
        level: str,
        metadata: Optional[Dict[str, Any]],
        timestamp: Optional[datetime]
    ) -> None:
        """
        Internal method to write a log entry immediately.
        No buffering - writes directly to storage/remote.

        Args:
            message: Log message
            level: Log level (already validated)
            metadata: Optional metadata dict
            timestamp: Optional custom timestamp (defaults to now)
        """
        log_entry = {
            "timestamp": (timestamp or datetime.utcnow()).isoformat() + "Z",
            "level": level,
            "message": message,
        }

        if metadata:
            log_entry["metadata"] = metadata

        # Write immediately (no buffering)
        if self._client:
            # Remote mode: send to API (wrapped in array for batch API)
            self._client.create_log_entries(
                session_id=self._session_id,
                logs=[log_entry]  # Single log in array
            )

        if self._storage:
            # Local mode: write to file immediately
            self._storage.write_log(
                workspace=self.workspace,
                session=self.name,
                message=log_entry["message"],
                level=log_entry["level"],
                metadata=log_entry.get("metadata"),
                timestamp=log_entry["timestamp"]
            )

    def file(self, **kwargs) -> FileBuilder:
        """
        Get a FileBuilder for fluent file operations.

        Returns:
            FileBuilder instance for chaining

        Raises:
            RuntimeError: If session is not open

        Examples:
            # Upload file
            session.file(file_path="./model.pt", prefix="/models").save()

            # List files
            files = session.file().list()
            files = session.file(prefix="/models").list()

            # Download file
            session.file(file_id="123").download()

            # Delete file
            session.file(file_id="123").delete()
        """
        if not self._is_open:
            raise RuntimeError("Session not open. Use session.open() or context manager.")

        return FileBuilder(self, **kwargs)

    def _upload_file(
        self,
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
        Internal method to upload a file.

        Args:
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
        """
        result = None

        if self._client:
            # Remote mode: upload to API
            result = self._client.upload_file(
                session_id=self._session_id,
                file_path=file_path,
                prefix=prefix,
                filename=filename,
                description=description,
                tags=tags,
                metadata=metadata,
                checksum=checksum,
                content_type=content_type,
                size_bytes=size_bytes
            )

        if self._storage:
            # Local mode: copy to local storage
            result = self._storage.write_file(
                workspace=self.workspace,
                session=self.name,
                file_path=file_path,
                prefix=prefix,
                filename=filename,
                description=description,
                tags=tags,
                metadata=metadata,
                checksum=checksum,
                content_type=content_type,
                size_bytes=size_bytes
            )

        return result

    def _list_files(
        self,
        prefix: Optional[str] = None,
        tags: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Internal method to list files.

        Args:
            prefix: Optional prefix filter
            tags: Optional tags filter

        Returns:
            List of file metadata dicts
        """
        files = []

        if self._client:
            # Remote mode: fetch from API
            files = self._client.list_files(
                session_id=self._session_id,
                prefix=prefix,
                tags=tags
            )

        if self._storage:
            # Local mode: read from metadata file
            files = self._storage.list_files(
                workspace=self.workspace,
                session=self.name,
                prefix=prefix,
                tags=tags
            )

        return files

    def _download_file(
        self,
        file_id: str,
        dest_path: Optional[str] = None
    ) -> str:
        """
        Internal method to download a file.

        Args:
            file_id: File ID
            dest_path: Optional destination path (defaults to original filename)

        Returns:
            Path to downloaded file
        """
        if self._client:
            # Remote mode: download from API
            return self._client.download_file(
                session_id=self._session_id,
                file_id=file_id,
                dest_path=dest_path
            )

        if self._storage:
            # Local mode: copy from local storage
            return self._storage.read_file(
                workspace=self.workspace,
                session=self.name,
                file_id=file_id,
                dest_path=dest_path
            )

        raise RuntimeError("No client or storage configured")

    def _delete_file(self, file_id: str) -> Dict[str, Any]:
        """
        Internal method to delete a file.

        Args:
            file_id: File ID

        Returns:
            Dict with id and deletedAt
        """
        result = None

        if self._client:
            # Remote mode: delete via API
            result = self._client.delete_file(
                session_id=self._session_id,
                file_id=file_id
            )

        if self._storage:
            # Local mode: soft delete in metadata
            result = self._storage.delete_file(
                workspace=self.workspace,
                session=self.name,
                file_id=file_id
            )

        return result

    def _update_file(
        self,
        file_id: str,
        description: Optional[str],
        tags: Optional[List[str]],
        metadata: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Internal method to update file metadata.

        Args:
            file_id: File ID
            description: Optional description
            tags: Optional tags
            metadata: Optional metadata

        Returns:
            Updated file metadata dict
        """
        result = None

        if self._client:
            # Remote mode: update via API
            result = self._client.update_file(
                session_id=self._session_id,
                file_id=file_id,
                description=description,
                tags=tags,
                metadata=metadata
            )

        if self._storage:
            # Local mode: update in metadata file
            result = self._storage.update_file_metadata(
                workspace=self.workspace,
                session=self.name,
                file_id=file_id,
                description=description,
                tags=tags,
                metadata=metadata
            )

        return result

    def parameters(self) -> ParametersBuilder:
        """
        Get a ParametersBuilder for fluent parameter operations.

        Returns:
            ParametersBuilder instance for chaining

        Raises:
            RuntimeError: If session is not open

        Examples:
            # Set parameters
            session.parameters().set(
                model={"lr": 0.001, "batch_size": 32},
                optimizer="adam"
            )

            # Get parameters
            params = session.parameters().get()  # Flattened
            params = session.parameters().get(flatten=False)  # Nested
        """
        if not self._is_open:
            raise RuntimeError("Session not open. Use session.open() or context manager.")

        return ParametersBuilder(self)

    def _write_params(self, flattened_params: Dict[str, Any]) -> None:
        """
        Internal method to write/merge parameters.

        Args:
            flattened_params: Already-flattened parameter dict with dot notation
        """
        if self._client:
            # Remote mode: send to API
            self._client.set_parameters(
                session_id=self._session_id,
                data=flattened_params
            )

        if self._storage:
            # Local mode: write to file
            self._storage.write_parameters(
                workspace=self.workspace,
                session=self.name,
                data=flattened_params
            )

    def _read_params(self) -> Optional[Dict[str, Any]]:
        """
        Internal method to read parameters.

        Returns:
            Flattened parameters dict, or None if no parameters exist
        """
        params = None

        if self._client:
            # Remote mode: fetch from API
            try:
                params = self._client.get_parameters(session_id=self._session_id)
            except Exception:
                # Parameters don't exist yet
                params = None

        if self._storage:
            # Local mode: read from file
            params = self._storage.read_parameters(
                workspace=self.workspace,
                session=self.name
            )

        return params

    def _track(self, name: str, description: Optional[str] = None,
              tags: Optional[List[str]] = None, metadata: Optional[Dict[str, Any]] = None) -> 'TrackBuilder':
        """
        Internal method to get a TrackBuilder for fluent track operations.

        Use session.track("name") or session.tracks("name") instead.

        Args:
            name: Track name (unique within session)
            description: Optional track description
            tags: Optional tags for categorization
            metadata: Optional structured metadata

        Returns:
            TrackBuilder instance for chaining

        Raises:
            RuntimeError: If session is not open

        Examples:
            # Append single data point
            session.track("train_loss").append(loss=0.5, step=100)

            # Append batch
            session.track("metrics").append_batch([
                {"loss": 0.5, "acc": 0.8, "step": 1},
                {"loss": 0.4, "acc": 0.85, "step": 2}
            ])

            # Read data
            data = session.track("train_loss").read(start_index=0, limit=100)

            # Get statistics
            stats = session.track(name="train_loss").stats()
        """
        from .track import TrackBuilder

        if not self._is_open:
            raise RuntimeError(
                "Cannot use track on closed session. "
                "Use 'with Session(...) as session:' or call session.open() first."
            )

        return TrackBuilder(self, name, description, tags, metadata)

    def _merge_by_timestamp(self, data_points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Merge data points with same _ts value.

        Args:
            data_points: List of data points (each with _ts field)

        Returns:
            List of merged data points, sorted by _ts
        """
        merged = {}
        for point in data_points:
            ts = point['_ts']
            if ts in merged:
                # Merge fields (later fields override earlier ones)
                merged[ts].update(point)
            else:
                merged[ts] = point.copy()

        # Sort by timestamp
        return [merged[ts] for ts in sorted(merged.keys())]

    def _flush_track(
        self,
        name: str,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Flush buffered data for a specific track.

        Args:
            name: Track name
            description: Optional track description
            tags: Optional tags
            metadata: Optional metadata

        Returns:
            Result from backend (trackId, startIndex, endIndex, count, ...) or None if no data
        """
        with self._track_buffer_lock:
            buffer = self._track_buffers.get(name, [])
            if not buffer:
                return None

            # Merge by timestamp
            merged = self._merge_by_timestamp(buffer)

            # Clear buffer before sending (avoid re-sending on retry)
            self._track_buffers[name] = []

        # Send to backend (outside lock to allow concurrent appends)
        result = None
        if self._client:
            result = self._client.append_batch_to_track(
                session_id=self._session_id,
                track_name=name,
                data_points=merged,
                description=description,
                tags=tags,
                metadata=metadata
            )

        if self._storage:
            result = self._storage.append_batch_to_track(
                workspace=self.workspace,
                session=self.name,
                track_name=name,
                data_points=merged,
                description=description,
                tags=tags,
                metadata=metadata
            )

        return result

    def _flush_all_tracks(self):
        """Flush all buffered tracks."""
        # Get snapshot of track names to avoid holding lock during flush
        with self._track_buffer_lock:
            track_names = [name for name, buffer in self._track_buffers.items() if buffer]

        # Flush each track
        for name in track_names:
            self._flush_track(name)

    def _append_to_track(
        self,
        name: str,
        data: Dict[str, Any],
        description: Optional[str],
        tags: Optional[List[str]],
        metadata: Optional[Dict[str, Any]]
    ) -> 'TrackBuilder':
        """
        Internal method to append a single data point to a track (buffered).

        Args:
            name: Track name
            data: Data point (flexible schema, _ts will be added if missing)
            description: Optional track description
            tags: Optional tags
            metadata: Optional metadata

        Returns:
            TrackBuilder for chaining
        """
        # Handle _ts field
        if '_ts' not in data:
            # Auto-generate unique timestamp
            ts = time.time()
            # Ensure monotonically increasing (avoid collisions in rapid succession)
            if ts <= self._track_last_auto_timestamp:
                ts = self._track_last_auto_timestamp + 0.000001
            self._track_last_auto_timestamp = ts
            data['_ts'] = ts
        elif data['_ts'] == -1:
            # Inherit global last timestamp (works across all tracks)
            if self._last_timestamp is not None:
                data['_ts'] = self._last_timestamp
            else:
                # No previous timestamp, use current time
                ts = time.time()
                if ts <= self._track_last_auto_timestamp:
                    ts = self._track_last_auto_timestamp + 0.000001
                self._track_last_auto_timestamp = ts
                data['_ts'] = ts

        # Validate _ts is numeric
        if not isinstance(data['_ts'], (int, float)):
            raise ValueError("_ts must be a number (seconds since epoch)")

        # Store global last timestamp (for _ts=-1 inheritance across tracks)
        self._last_timestamp = data['_ts']

        # Add to buffer
        with self._track_buffer_lock:
            if name not in self._track_buffers:
                self._track_buffers[name] = []
            self._track_buffers[name].append(data)

        # Return TrackBuilder for chaining
        from .track import TrackBuilder
        return TrackBuilder(self, name, description, tags, metadata)

    def _append_batch_to_track(
        self,
        name: str,
        data_points: List[Dict[str, Any]],
        description: Optional[str],
        tags: Optional[List[str]],
        metadata: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Internal method to append multiple data points to a track.

        Args:
            name: Track name
            data_points: List of data points
            description: Optional track description
            tags: Optional tags
            metadata: Optional metadata

        Returns:
            Dict with trackId, startIndex, endIndex, count
        """
        result = None

        if self._client:
            # Remote mode: append batch via API
            result = self._client.append_batch_to_track(
                session_id=self._session_id,
                track_name=name,
                data_points=data_points,
                description=description,
                tags=tags,
                metadata=metadata
            )

        if self._storage:
            # Local mode: append batch to local storage
            result = self._storage.append_batch_to_track(
                workspace=self.workspace,
                session=self.name,
                track_name=name,
                data_points=data_points,
                description=description,
                tags=tags,
                metadata=metadata
            )

        return result

    def _read_track_data(
        self,
        name: str,
        start_index: int,
        limit: int
    ) -> Dict[str, Any]:
        """
        Internal method to read data points from a track.

        Args:
            name: Track name
            start_index: Starting index
            limit: Max points to read

        Returns:
            Dict with data, startIndex, endIndex, total, hasMore
        """
        result = None

        if self._client:
            # Remote mode: read via API
            result = self._client.read_track_data(
                session_id=self._session_id,
                track_name=name,
                start_index=start_index,
                limit=limit
            )

        if self._storage:
            # Local mode: read from local storage
            result = self._storage.read_track_data(
                workspace=self.workspace,
                session=self.name,
                track_name=name,
                start_index=start_index,
                limit=limit
            )

        return result

    def _get_track_stats(self, name: str) -> Dict[str, Any]:
        """
        Internal method to get track statistics.

        Args:
            name: Track name

        Returns:
            Dict with track stats
        """
        result = None

        if self._client:
            # Remote mode: get stats via API
            result = self._client.get_track_stats(
                session_id=self._session_id,
                track_name=name
            )

        if self._storage:
            # Local mode: get stats from local storage
            result = self._storage.get_track_stats(
                workspace=self.workspace,
                session=self.name,
                track_name=name
            )

        return result

    def _list_tracks(self) -> List[Dict[str, Any]]:
        """
        Internal method to list all tracks in session.

        Returns:
            List of track summaries
        """
        result = None

        if self._client:
            # Remote mode: list via API
            result = self._client.list_tracks(session_id=self._session_id)

        if self._storage:
            # Local mode: list from local storage
            result = self._storage.list_tracks(
                workspace=self.workspace,
                session=self.name
            )

        return result or []

    @property
    def id(self) -> Optional[str]:
        """Get the session ID (only available after open in remote mode)."""
        return self._session_id

    @property
    def data(self) -> Optional[Dict[str, Any]]:
        """Get the full session data (only available after open in remote mode)."""
        return self._session_data



def dreamlake_session(
    name: str,
    workspace: str,
    **kwargs
) -> Callable:
    """
    Decorator for wrapping functions with a dreamlake session.

    Usage:
        @dreamlake_session(
            name="my-experiment",
            workspace="my-workspace",
            remote="http://localhost:3000",
            api_key="your-token"
        )
        def train_model():
            # Function code here
            pass

    The decorated function will receive a 'session' keyword argument
    with the active Session instance.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **func_kwargs):
            with Session(name=name, workspace=workspace, **kwargs) as session:
                # Inject session into function kwargs
                func_kwargs['session'] = session
                return func(*args, **func_kwargs)
        return wrapper
    return decorator
