"""
Local filesystem storage for dreamlake.
"""

from typing import Optional, Dict, Any, List
from pathlib import Path
import json
from datetime import datetime
import threading
import time
import os


class FileLock:
    """
    Cross-platform file locking context manager.

    Provides both thread-level (threading.Lock) and process-level (file-based) locking
    to prevent race conditions when multiple threads/processes access the same file.
    """

    def __init__(self, lock_file_path: Path):
        """
        Initialize file lock.

        Args:
            lock_file_path: Path to the lock file
        """
        self.lock_file_path = Path(lock_file_path)
        self._thread_lock = threading.Lock()
        self._file_handle = None

    def __enter__(self):
        """Acquire lock."""
        # First acquire thread-level lock
        self._thread_lock.acquire()

        # Then acquire file-level lock for cross-process safety
        self.lock_file_path.parent.mkdir(parents=True, exist_ok=True)

        # Try to acquire lock with retries
        max_retries = 50
        retry_delay = 0.01  # 10ms

        for attempt in range(max_retries):
            try:
                # Try to acquire exclusive lock using platform-specific methods
                self._file_handle = open(self.lock_file_path, 'a')

                # Use fcntl on Unix-like systems, msvcrt on Windows
                try:
                    import fcntl
                    fcntl.flock(self._file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return self
                except ImportError:
                    # Windows
                    try:
                        import msvcrt
                        msvcrt.locking(self._file_handle.fileno(), msvcrt.LK_NBLCK, 1)
                        return self
                    except ImportError:
                        # Fallback: no file locking available, rely on thread lock only
                        return self
                except (IOError, OSError):
                    # Lock is held by another process, retry
                    if self._file_handle:
                        self._file_handle.close()
                        self._file_handle = None
                    time.sleep(retry_delay)
                    continue

            except Exception:
                if self._file_handle:
                    self._file_handle.close()
                    self._file_handle = None
                time.sleep(retry_delay)
                continue

        # Failed to acquire lock
        self._thread_lock.release()
        raise TimeoutError(f"Could not acquire lock on {self.lock_file_path} after {max_retries} attempts")

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Release lock."""
        # Release file lock
        if self._file_handle:
            try:
                # Release platform-specific lock
                try:
                    import fcntl
                    fcntl.flock(self._file_handle.fileno(), fcntl.LOCK_UN)
                except ImportError:
                    try:
                        import msvcrt
                        msvcrt.locking(self._file_handle.fileno(), msvcrt.LK_UNLCK, 1)
                    except ImportError:
                        pass

                self._file_handle.close()
            except Exception:
                pass
            finally:
                self._file_handle = None

        # Release thread lock
        self._thread_lock.release()

        return False


class LocalStorage:
    """
    Local filesystem storage backend.

    Directory structure:
    <root>/
      <workspace>/
        <session_name>/
          session.json        # Session metadata
          logs/
            logs.jsonl        # Log entries
            .log_sequence     # Sequence counter
          tracks/
            <track_name>.jsonl
          files/
            <uploaded_files>
          parameters.json     # Flattened parameters
    """

    def __init__(self, root_path: Path):
        """
        Initialize local storage.

        Args:
            root_path: Root directory for local storage
        """
        self.root_path = Path(root_path)
        self.root_path.mkdir(parents=True, exist_ok=True)
        # Cache for lock objects per file to ensure same lock instance per file path
        self._locks: Dict[str, FileLock] = {}
        self._locks_lock = threading.Lock()

    def _get_lock(self, lock_file_path: Path) -> FileLock:
        """
        Get or create a FileLock instance for a given path.
        Ensures the same lock object is reused for the same file.
        """
        lock_key = str(lock_file_path)
        with self._locks_lock:
            if lock_key not in self._locks:
                self._locks[lock_key] = FileLock(lock_file_path)
            return self._locks[lock_key]

    def create_session(
        self,
        workspace: str,
        name: str,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        folder: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Path:
        """
        Create a session directory structure.

        Args:
            workspace: Workspace name
            name: Session name
            description: Optional description
            tags: Optional tags
            folder: Optional folder path (used for organization)
            metadata: Optional metadata

        Returns:
            Path to session directory
        """
        # Create workspace directory
        workspace_dir = self.root_path / workspace
        workspace_dir.mkdir(parents=True, exist_ok=True)

        # Create session directory
        session_dir = workspace_dir / name
        session_dir.mkdir(parents=True, exist_ok=True)

        # Create subdirectories
        (session_dir / "logs").mkdir(exist_ok=True)
        (session_dir / "tracks").mkdir(exist_ok=True)
        (session_dir / "files").mkdir(exist_ok=True)

        # Write session metadata with locking to prevent race conditions
        session_file = session_dir / "session.json"
        lock_file = session_dir / ".session.lock"

        session_metadata = {
            "name": name,
            "workspace": workspace,
            "description": description,
            "tags": tags or [],
            "folder": folder,
            "metadata": metadata,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "write_protected": False,
        }

        with self._get_lock(lock_file):
            if not session_file.exists():
                # Only create if doesn't exist (don't overwrite)
                with open(session_file, "w") as f:
                    json.dump(session_metadata, f, indent=2)
            else:
                # Update existing session
                with open(session_file, "r") as f:
                    existing = json.load(f)
                # Merge updates
                if description is not None:
                    existing["description"] = description
                if tags is not None:
                    existing["tags"] = tags
                if folder is not None:
                    existing["folder"] = folder
                if metadata is not None:
                    existing["metadata"] = metadata
                existing["updated_at"] = datetime.utcnow().isoformat() + "Z"
                with open(session_file, "w") as f:
                    json.dump(existing, f, indent=2)

        return session_dir

    def flush(self):
        """Flush any pending writes (no-op for now)."""
        pass

    def write_log(
        self,
        workspace: str,
        session: str,
        message: str,
        level: str,
        timestamp: str,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """
        Write a single log entry immediately to JSONL file.

        Args:
            workspace: Workspace name
            session: Session name
            message: Log message
            level: Log level
            timestamp: ISO timestamp string
            metadata: Optional metadata
        """
        session_dir = self.root_path / workspace / session
        logs_dir = session_dir / "logs"
        logs_file = logs_dir / "logs.jsonl"
        seq_file = logs_dir / ".log_sequence"
        lock_file = logs_dir / ".log_sequence.lock"

        # Use locking to prevent race condition on sequence number
        with self._get_lock(lock_file):
            # Read and increment sequence counter
            sequence_number = 0
            if seq_file.exists():
                try:
                    sequence_number = int(seq_file.read_text().strip())
                except (ValueError, IOError):
                    sequence_number = 0

            log_entry = {
                "sequenceNumber": sequence_number,
                "timestamp": timestamp,
                "level": level,
                "message": message,
            }

            if metadata:
                log_entry["metadata"] = metadata

            # Write log immediately
            with open(logs_file, "a") as f:
                f.write(json.dumps(log_entry) + "\n")

            # Update sequence counter
            seq_file.write_text(str(sequence_number + 1))

    def write_track_data(
        self,
        workspace: str,
        session: str,
        track_name: str,
        data: Any,
    ):
        """
        Write track data point.

        Args:
            workspace: Workspace name
            session: Session name
            track_name: Track name
            data: Data point
        """
        session_dir = self.root_path / workspace / session
        track_file = session_dir / "tracks" / f"{track_name}.jsonl"

        data_point = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "data": data,
        }

        with open(track_file, "a") as f:
            f.write(json.dumps(data_point) + "\n")

    def write_parameters(
        self,
        workspace: str,
        session: str,
        data: Dict[str, Any],
    ):
        """
        Write/merge parameters. Always merges with existing parameters.

        File format:
        {
          "version": 2,
          "data": {"model.lr": 0.001, "model.batch_size": 32},
          "updatedAt": "2024-01-15T10:30:00Z"
        }

        Args:
            workspace: Workspace name
            session: Session name
            data: Flattened parameter dict with dot notation (already flattened)
        """
        session_dir = self.root_path / workspace / session
        params_file = session_dir / "parameters.json"
        lock_file = session_dir / ".parameters.lock"

        # Use locking to prevent race condition on parameter merge
        with self._get_lock(lock_file):
            # Read existing if present
            if params_file.exists():
                with open(params_file, "r") as f:
                    existing_doc = json.load(f)

                # Merge with existing data
                existing_data = existing_doc.get("data", {})
                existing_data.update(data)

                # Increment version
                version = existing_doc.get("version", 1) + 1

                params_doc = {
                    "version": version,
                    "data": existing_data,
                    "updatedAt": datetime.utcnow().isoformat() + "Z"
                }
            else:
                # Create new parameters document
                params_doc = {
                    "version": 1,
                    "data": data,
                    "createdAt": datetime.utcnow().isoformat() + "Z",
                    "updatedAt": datetime.utcnow().isoformat() + "Z"
                }

            with open(params_file, "w") as f:
                json.dump(params_doc, f, indent=2)

    def read_parameters(
        self,
        workspace: str,
        session: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Read parameters from local file.

        Args:
            workspace: Workspace name
            session: Session name

        Returns:
            Flattened parameter dict, or None if file doesn't exist
        """
        session_dir = self.root_path / workspace / session
        params_file = session_dir / "parameters.json"

        if not params_file.exists():
            return None

        try:
            with open(params_file, "r") as f:
                params_doc = json.load(f)
            return params_doc.get("data", {})
        except (json.JSONDecodeError, IOError):
            return None

    def write_file(
        self,
        workspace: str,
        session: str,
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
        Write file to local storage.

        Copies file to: files/<prefix>/<file_id>/<filename>
        Updates .files_metadata.json with file metadata

        Args:
            workspace: Workspace name
            session: Session name
            file_path: Source file path
            prefix: Logical path prefix (e.g., "/models", "/config")
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
        import shutil
        from .files import generate_snowflake_id

        session_dir = self.root_path / workspace / session
        files_dir = session_dir / "files"
        metadata_file = files_dir / ".files_metadata.json"
        lock_file = files_dir / ".files_metadata.lock"

        # Generate Snowflake ID for file
        file_id = generate_snowflake_id()

        # Normalize prefix: strip leading slash
        prefix_folder = prefix.lstrip("/") if prefix else ""

        # Create file directory: files/{prefix}/{file_id}/
        file_dir = files_dir / prefix_folder / file_id if prefix_folder else files_dir / file_id
        file_dir.mkdir(parents=True, exist_ok=True)

        # Copy file
        dest_file = file_dir / filename
        shutil.copy2(file_path, dest_file)

        # Create file metadata
        file_metadata = {
            "id": file_id,
            "sessionId": f"{workspace}/{session}",  # Local mode doesn't have real session ID
            "path": prefix,
            "filename": filename,
            "description": description,
            "tags": tags or [],
            "contentType": content_type,
            "sizeBytes": size_bytes,
            "checksum": checksum,
            "metadata": metadata,
            "uploadedAt": datetime.utcnow().isoformat() + "Z",
            "updatedAt": datetime.utcnow().isoformat() + "Z",
            "deletedAt": None
        }

        # Use file lock to prevent concurrent modification
        with self._get_lock(lock_file):
            # Read existing metadata
            files_metadata = {"files": []}
            if metadata_file.exists():
                try:
                    with open(metadata_file, "r") as f:
                        files_metadata = json.load(f)
                except (json.JSONDecodeError, IOError):
                    files_metadata = {"files": []}

            # Check if file with same prefix+filename exists (overwrite behavior)
            existing_index = None
            for i, existing_file in enumerate(files_metadata["files"]):
                if (existing_file["path"] == prefix and
                    existing_file["filename"] == filename and
                    existing_file["deletedAt"] is None):
                    existing_index = i
                    break

            if existing_index is not None:
                # Overwrite: remove old file and update metadata
                old_file = files_metadata["files"][existing_index]
                old_prefix_folder = old_file["path"].lstrip("/") if old_file["path"] else ""
                old_file_dir = files_dir / old_prefix_folder / old_file["id"] if old_prefix_folder else files_dir / old_file["id"]
                if old_file_dir.exists():
                    shutil.rmtree(old_file_dir)
                files_metadata["files"][existing_index] = file_metadata
            else:
                # New file: append to list
                files_metadata["files"].append(file_metadata)

            # Write updated metadata atomically
            with open(metadata_file, "w") as f:
                json.dump(files_metadata, f, indent=2)

        return file_metadata

    def list_files(
        self,
        workspace: str,
        session: str,
        prefix: Optional[str] = None,
        tags: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        List files from local storage.

        Args:
            workspace: Workspace name
            session: Session name
            prefix: Optional prefix filter
            tags: Optional tags filter

        Returns:
            List of file metadata dicts (only non-deleted files)
        """
        session_dir = self.root_path / workspace / session
        metadata_file = session_dir / "files" / ".files_metadata.json"

        if not metadata_file.exists():
            return []

        try:
            with open(metadata_file, "r") as f:
                files_metadata = json.load(f)
        except (json.JSONDecodeError, IOError):
            return []

        files = files_metadata.get("files", [])

        # Filter out deleted files
        files = [f for f in files if f.get("deletedAt") is None]

        # Apply prefix filter
        if prefix:
            files = [f for f in files if f["path"].startswith(prefix)]

        # Apply tags filter
        if tags:
            files = [f for f in files if any(tag in f.get("tags", []) for tag in tags)]

        return files

    def read_file(
        self,
        workspace: str,
        session: str,
        file_id: str,
        dest_path: Optional[str] = None
    ) -> str:
        """
        Read/copy file from local storage.

        Args:
            workspace: Workspace name
            session: Session name
            file_id: File ID
            dest_path: Optional destination path (defaults to original filename)

        Returns:
            Path to copied file

        Raises:
            FileNotFoundError: If file not found
            ValueError: If checksum verification fails
        """
        import shutil
        from .files import verify_checksum

        session_dir = self.root_path / workspace / session
        files_dir = session_dir / "files"
        metadata_file = files_dir / ".files_metadata.json"

        if not metadata_file.exists():
            raise FileNotFoundError(f"File {file_id} not found")

        # Find file metadata
        with open(metadata_file, "r") as f:
            files_metadata = json.load(f)

        file_metadata = None
        for f in files_metadata.get("files", []):
            if f["id"] == file_id and f.get("deletedAt") is None:
                file_metadata = f
                break

        if not file_metadata:
            raise FileNotFoundError(f"File {file_id} not found")

        # Get source file with prefix structure
        prefix_folder = file_metadata["path"].lstrip("/") if file_metadata["path"] else ""
        source_file = files_dir / prefix_folder / file_id / file_metadata["filename"] if prefix_folder else files_dir / file_id / file_metadata["filename"]
        if not source_file.exists():
            raise FileNotFoundError(f"File {file_id} not found on disk")

        # Determine destination
        if dest_path is None:
            dest_path = file_metadata["filename"]

        # Copy file
        shutil.copy2(source_file, dest_path)

        # Verify checksum
        expected_checksum = file_metadata["checksum"]
        if not verify_checksum(dest_path, expected_checksum):
            import os
            os.remove(dest_path)
            raise ValueError(f"Checksum verification failed for file {file_id}")

        return dest_path

    def delete_file(
        self,
        workspace: str,
        session: str,
        file_id: str
    ) -> Dict[str, Any]:
        """
        Delete file from local storage (soft delete in metadata).

        Args:
            workspace: Workspace name
            session: Session name
            file_id: File ID

        Returns:
            Dict with id and deletedAt

        Raises:
            FileNotFoundError: If file not found
        """
        session_dir = self.root_path / workspace / session
        files_dir = session_dir / "files"
        metadata_file = files_dir / ".files_metadata.json"
        lock_file = files_dir / ".files_metadata.lock"

        if not metadata_file.exists():
            raise FileNotFoundError(f"File {file_id} not found")

        # Use file lock to prevent concurrent modification
        with self._get_lock(lock_file):
            # Read metadata
            with open(metadata_file, "r") as f:
                files_metadata = json.load(f)

            # Find and soft delete file
            file_found = False
            for file_meta in files_metadata.get("files", []):
                if file_meta["id"] == file_id:
                    if file_meta.get("deletedAt") is not None:
                        raise FileNotFoundError(f"File {file_id} already deleted")
                    file_meta["deletedAt"] = datetime.utcnow().isoformat() + "Z"
                    file_meta["updatedAt"] = file_meta["deletedAt"]
                    file_found = True
                    break

            if not file_found:
                raise FileNotFoundError(f"File {file_id} not found")

            # Write updated metadata
            with open(metadata_file, "w") as f:
                json.dump(files_metadata, f, indent=2)

        return {
            "id": file_id,
            "deletedAt": file_meta["deletedAt"]
        }

    def update_file_metadata(
        self,
        workspace: str,
        session: str,
        file_id: str,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Update file metadata in local storage.

        Args:
            workspace: Workspace name
            session: Session name
            file_id: File ID
            description: Optional description
            tags: Optional tags
            metadata: Optional metadata

        Returns:
            Updated file metadata dict

        Raises:
            FileNotFoundError: If file not found
        """
        session_dir = self.root_path / workspace / session
        files_dir = session_dir / "files"
        metadata_file = files_dir / ".files_metadata.json"
        lock_file = files_dir / ".files_metadata.lock"

        if not metadata_file.exists():
            raise FileNotFoundError(f"File {file_id} not found")

        # Use file lock to prevent concurrent modification
        with self._get_lock(lock_file):
            # Read metadata
            with open(metadata_file, "r") as f:
                files_metadata = json.load(f)

            # Find and update file
            file_found = False
            updated_file = None
            for file_meta in files_metadata.get("files", []):
                if file_meta["id"] == file_id:
                    if file_meta.get("deletedAt") is not None:
                        raise FileNotFoundError(f"File {file_id} has been deleted")

                    # Update fields
                    if description is not None:
                        file_meta["description"] = description
                    if tags is not None:
                        file_meta["tags"] = tags
                    if metadata is not None:
                        file_meta["metadata"] = metadata

                    file_meta["updatedAt"] = datetime.utcnow().isoformat() + "Z"
                    file_found = True
                    updated_file = file_meta
                    break

            if not file_found:
                raise FileNotFoundError(f"File {file_id} not found")

            # Write updated metadata
            with open(metadata_file, "w") as f:
                json.dump(files_metadata, f, indent=2)

        return updated_file

    def _get_session_dir(self, workspace: str, session: str) -> Path:
        """Get session directory path."""
        return self.root_path / workspace / session

    def append_to_track(
        self,
        workspace: str,
        session: str,
        track_name: str,
        data: Dict[str, Any],
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Append a single data point to a track in local storage.

        Storage format:
        .dreamlake/{workspace}/{session}/tracks/{track_name}/
            data.jsonl  # Data points (one JSON object per line)
            metadata.json  # Track metadata (name, description, tags, stats)

        Args:
            workspace: Workspace name
            session: Session name
            track_name: Track name
            data: Data point (flexible schema)
            description: Optional track description
            tags: Optional tags
            metadata: Optional track metadata

        Returns:
            Dict with trackId, index, bufferedDataPoints, chunkSize
        """
        session_dir = self._get_session_dir(workspace, session)
        tracks_dir = session_dir / "tracks"
        tracks_dir.mkdir(parents=True, exist_ok=True)

        track_dir = tracks_dir / track_name
        track_dir.mkdir(exist_ok=True)

        data_file = track_dir / "data.jsonl"
        metadata_file = track_dir / "metadata.json"
        lock_file = track_dir / ".track.lock"

        # Use locking to prevent race condition on track index allocation
        with self._get_lock(lock_file):
            # Load or initialize metadata
            if metadata_file.exists():
                with open(metadata_file, "r") as f:
                    track_meta = json.load(f)
            else:
                track_meta = {
                    "trackId": f"local-track-{track_name}",
                    "name": track_name,
                    "description": description,
                    "tags": tags or [],
                    "metadata": metadata,
                    "totalDataPoints": 0,
                    "nextIndex": 0,
                    "createdAt": datetime.utcnow().isoformat() + "Z"
                }

            # Get next index
            index = track_meta["nextIndex"]

            # Append data point to JSONL file
            data_entry = {
                "index": index,
                "data": data,
                "createdAt": datetime.utcnow().isoformat() + "Z"
            }

            with open(data_file, "a") as f:
                f.write(json.dumps(data_entry) + "\n")

            # Update metadata
            track_meta["nextIndex"] = index + 1
            track_meta["totalDataPoints"] = track_meta["totalDataPoints"] + 1
            track_meta["updatedAt"] = datetime.utcnow().isoformat() + "Z"

            with open(metadata_file, "w") as f:
                json.dump(track_meta, f, indent=2)

        return {
            "trackId": track_meta["trackId"],
            "index": str(index),
            "bufferedDataPoints": str(track_meta["totalDataPoints"]),
            "chunkSize": 10000  # Default chunk size for local mode
        }

    def append_batch_to_track(
        self,
        workspace: str,
        session: str,
        track_name: str,
        data_points: List[Dict[str, Any]],
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Append multiple data points to a track in local storage (batch).

        Args:
            workspace: Workspace name
            session: Session name
            track_name: Track name
            data_points: List of data points
            description: Optional track description
            tags: Optional tags
            metadata: Optional track metadata

        Returns:
            Dict with trackId, startIndex, endIndex, count
        """
        session_dir = self._get_session_dir(workspace, session)
        tracks_dir = session_dir / "tracks"
        tracks_dir.mkdir(parents=True, exist_ok=True)

        track_dir = tracks_dir / track_name
        track_dir.mkdir(exist_ok=True)

        data_file = track_dir / "data.jsonl"
        metadata_file = track_dir / "metadata.json"
        lock_file = track_dir / ".track.lock"

        # Use locking to prevent race condition on track index allocation
        with self._get_lock(lock_file):
            # Load or initialize metadata
            if metadata_file.exists():
                with open(metadata_file, "r") as f:
                    track_meta = json.load(f)
            else:
                track_meta = {
                    "trackId": f"local-track-{track_name}",
                    "name": track_name,
                    "description": description,
                    "tags": tags or [],
                    "metadata": metadata,
                    "totalDataPoints": 0,
                    "nextIndex": 0,
                    "createdAt": datetime.utcnow().isoformat() + "Z"
                }

            start_index = track_meta["nextIndex"]
            end_index = start_index + len(data_points) - 1

            # Append data points to JSONL file
            with open(data_file, "a") as f:
                for i, data in enumerate(data_points):
                    data_entry = {
                        "index": start_index + i,
                        "data": data,
                        "createdAt": datetime.utcnow().isoformat() + "Z"
                    }
                    f.write(json.dumps(data_entry) + "\n")

            # Update metadata
            track_meta["nextIndex"] = end_index + 1
            track_meta["totalDataPoints"] = track_meta["totalDataPoints"] + len(data_points)
            track_meta["updatedAt"] = datetime.utcnow().isoformat() + "Z"

            with open(metadata_file, "w") as f:
                json.dump(track_meta, f, indent=2)

        return {
            "trackId": track_meta["trackId"],
            "startIndex": str(start_index),
            "endIndex": str(end_index),
            "count": len(data_points),
            "bufferedDataPoints": str(track_meta["totalDataPoints"]),
            "chunkSize": 10000
        }

    def read_track_data(
        self,
        workspace: str,
        session: str,
        track_name: str,
        start_index: int = 0,
        limit: int = 1000
    ) -> Dict[str, Any]:
        """
        Read data points from a track in local storage.

        Args:
            workspace: Workspace name
            session: Session name
            track_name: Track name
            start_index: Starting index
            limit: Max points to read

        Returns:
            Dict with data, startIndex, endIndex, total, hasMore
        """
        session_dir = self._get_session_dir(workspace, session)
        track_dir = session_dir / "tracks" / track_name
        data_file = track_dir / "data.jsonl"

        if not data_file.exists():
            return {
                "data": [],
                "startIndex": start_index,
                "endIndex": start_index - 1,
                "total": 0,
                "hasMore": False
            }

        # Read all data points from JSONL file
        data_points = []
        with open(data_file, "r") as f:
            for line in f:
                if line.strip():
                    entry = json.loads(line)
                    # Filter by index range
                    if start_index <= entry["index"] < start_index + limit:
                        data_points.append(entry)

        # Get total count
        metadata_file = track_dir / "metadata.json"
        total_count = 0
        if metadata_file.exists():
            with open(metadata_file, "r") as f:
                track_meta = json.load(f)
                total_count = track_meta["totalDataPoints"]

        return {
            "data": data_points,
            "startIndex": start_index,
            "endIndex": start_index + len(data_points) - 1 if data_points else start_index - 1,
            "total": len(data_points),
            "hasMore": start_index + len(data_points) < total_count
        }

    def get_track_stats(
        self,
        workspace: str,
        session: str,
        track_name: str
    ) -> Dict[str, Any]:
        """
        Get track statistics from local storage.

        Args:
            workspace: Workspace name
            session: Session name
            track_name: Track name

        Returns:
            Dict with track stats
        """
        session_dir = self._get_session_dir(workspace, session)
        track_dir = session_dir / "tracks" / track_name
        metadata_file = track_dir / "metadata.json"

        if not metadata_file.exists():
            raise FileNotFoundError(f"Track {track_name} not found")

        with open(metadata_file, "r") as f:
            track_meta = json.load(f)

        return {
            "trackId": track_meta["trackId"],
            "name": track_meta["name"],
            "description": track_meta.get("description"),
            "tags": track_meta.get("tags", []),
            "metadata": track_meta.get("metadata"),
            "totalDataPoints": str(track_meta["totalDataPoints"]),
            "bufferedDataPoints": str(track_meta["totalDataPoints"]),  # All buffered in local mode
            "chunkedDataPoints": "0",  # No chunking in local mode
            "totalChunks": 0,
            "chunkSize": 10000,
            "firstDataAt": track_meta.get("createdAt"),
            "lastDataAt": track_meta.get("updatedAt"),
            "createdAt": track_meta.get("createdAt"),
            "updatedAt": track_meta.get("updatedAt", track_meta.get("createdAt"))
        }

    def list_tracks(
        self,
        workspace: str,
        session: str
    ) -> List[Dict[str, Any]]:
        """
        List all tracks in a session from local storage.

        Args:
            workspace: Workspace name
            session: Session name

        Returns:
            List of track summaries
        """
        session_dir = self._get_session_dir(workspace, session)
        tracks_dir = session_dir / "tracks"

        if not tracks_dir.exists():
            return []

        tracks = []
        for track_dir in tracks_dir.iterdir():
            if track_dir.is_dir():
                metadata_file = track_dir / "metadata.json"
                if metadata_file.exists():
                    with open(metadata_file, "r") as f:
                        track_meta = json.load(f)
                        tracks.append({
                            "trackId": track_meta["trackId"],
                            "name": track_meta["name"],
                            "description": track_meta.get("description"),
                            "tags": track_meta.get("tags", []),
                            "totalDataPoints": str(track_meta["totalDataPoints"]),
                            "createdAt": track_meta.get("createdAt")
                        })

        return tracks
