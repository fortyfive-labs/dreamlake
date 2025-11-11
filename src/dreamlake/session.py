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


class Session:
    """
    Dreamlake session for tracking experiments.

    Usage examples:

    # Remote mode
    session = Session(
        name="my-experiment",
        workspace="my-workspace",
        remote="http://localhost:3000",
        api_key="your-jwt-token"
    )

    # Local mode
    session = Session(
        name="my-experiment",
        workspace="my-workspace",
        local_path=".dreamlake"
    )

    # Context manager
    with Session(...) as sess:
        sess.log(...)

    # Decorator
    @dreamlake_session(name="exp", workspace="ws", remote="...")
    def train():
        ...
    """

    def __init__(
        self,
        name: str,
        workspace: str,
        *,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        folder: Optional[str] = None,
        write_protected: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
        # Mode configuration
        remote: Optional[str] = None,
        api_key: Optional[str] = None,
        user_name: Optional[str] = None,
        local_path: Optional[str] = None,
    ):
        """
        Initialize a dreamlake session.

        Args:
            name: Session name (unique within workspace)
            workspace: Workspace name
            description: Optional session description
            tags: Optional list of tags
            folder: Optional folder path (e.g., "/experiments/baseline")
            write_protected: If True, session becomes immutable after creation
            metadata: Optional metadata dict
            remote: Remote API URL (e.g., "http://localhost:3000")
            api_key: JWT token for authentication (if not provided, will be generated from user_name)
            user_name: Username for authentication (generates API key if api_key not provided)
            local_path: Local storage root path (for local mode)
        """
        self.name = name
        self.workspace = workspace
        self.description = description
        self.tags = tags
        self.folder = folder
        self.write_protected = write_protected
        self.metadata = metadata

        # Generate API key from username if not provided
        if remote and not api_key and user_name:
            api_key = self._generate_api_key_from_username(user_name)

        # Determine operation mode
        if remote and local_path:
            self.mode = OperationMode.HYBRID
        elif remote:
            self.mode = OperationMode.REMOTE
        elif local_path:
            self.mode = OperationMode.LOCAL
        else:
            raise ValueError(
                "Must specify either 'remote' (with api_key/user_name) or 'local_path'"
            )

        # Initialize backend
        self._client: Optional[RemoteClient] = None
        self._storage: Optional[LocalStorage] = None
        self._session_id: Optional[str] = None
        self._session_data: Optional[Dict[str, Any]] = None
        self._is_open = False

        if self.mode in (OperationMode.REMOTE, OperationMode.HYBRID):
            if not api_key:
                raise ValueError("Either api_key or user_name is required for remote mode")
            self._client = RemoteClient(base_url=remote, api_key=api_key)

        if self.mode in (OperationMode.LOCAL, OperationMode.HYBRID):
            if not local_path:
                raise ValueError("local_path is required for local mode")
            self._storage = LocalStorage(root_path=Path(local_path))

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
            response = self._client.create_or_update_session(
                workspace=self.workspace,
                name=self.name,
                description=self.description,
                tags=self.tags,
                folder=self.folder,
                write_protected=self.write_protected,
                metadata=self.metadata,
            )
            self._session_data = response
            self._session_id = response["session"]["id"]

        if self._storage:
            # Local mode: create session directory structure
            self._storage.create_session(
                workspace=self.workspace,
                name=self.name,
                description=self.description,
                tags=self.tags,
                folder=self.folder,
                metadata=self.metadata,
            )

        self._is_open = True
        return self

    def close(self):
        """Close the session."""
        if not self._is_open:
            return

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

    def files(self) -> FilesBuilder:
        """
        Get a FilesBuilder for fluent file operations (plural API).

        Returns:
            FilesBuilder instance for chaining

        Raises:
            RuntimeError: If session is not open

        Examples:
            # Upload file
            session.files().upload("./model.pt", path="/models")

            # List files
            files = session.files().list()
            files = session.files().list(path="/models")
        """
        if not self._is_open:
            raise RuntimeError("Session not open. Use session.open() or context manager.")

        return FilesBuilder(self)

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

    def track(self, name: str, description: Optional[str] = None,
              tags: Optional[List[str]] = None, metadata: Optional[Dict[str, Any]] = None) -> 'TrackBuilder':
        """
        Get a TrackBuilder for fluent track operations.

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
            session.track(name="train_loss").append(value=0.5, step=100)

            # Append batch
            session.track(name="metrics").append_batch([
                {"loss": 0.5, "acc": 0.8, "step": 1},
                {"loss": 0.4, "acc": 0.85, "step": 2}
            ])

            # Read data
            data = session.track(name="train_loss").read(start_index=0, limit=100)

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

    def _append_to_track(
        self,
        name: str,
        data: Dict[str, Any],
        description: Optional[str],
        tags: Optional[List[str]],
        metadata: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Internal method to append a single data point to a track.

        Args:
            name: Track name
            data: Data point (flexible schema)
            description: Optional track description
            tags: Optional tags
            metadata: Optional metadata

        Returns:
            Dict with trackId, index, bufferedDataPoints, chunkSize
        """
        result = None

        if self._client:
            # Remote mode: append via API
            result = self._client.append_to_track(
                session_id=self._session_id,
                track_name=name,
                data=data,
                description=description,
                tags=tags,
                metadata=metadata
            )

        if self._storage:
            # Local mode: append to local storage
            result = self._storage.append_to_track(
                workspace=self.workspace,
                session=self.name,
                track_name=name,
                data=data,
                description=description,
                tags=tags,
                metadata=metadata
            )

        return result

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
