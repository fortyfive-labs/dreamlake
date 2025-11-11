"""
Files module for Dreamlake SDK.

Provides fluent API for file upload, download, list, and delete operations.
"""

import hashlib
import mimetypes
from typing import Dict, Any, List, Optional, TYPE_CHECKING
from pathlib import Path

if TYPE_CHECKING:
    from .session import Session


class FilesBuilder:
    """
    Fluent interface for files operations (plural).

    Usage:
        # Upload file
        session.files().upload("./model.pt", path="/models")

        # List files
        files = session.files().list()
    """

    def __init__(self, session: 'Session'):
        """
        Initialize files builder.

        Args:
            session: Parent session instance
        """
        self._session = session

    def upload(
        self,
        file_path: str,
        *,
        path: str = "/",
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Upload a file.

        Args:
            file_path: Path to file to upload (required positional argument)
            path: Logical path prefix (default: "/")
            description: Optional description
            tags: Optional list of tags
            metadata: Optional metadata dict

        Returns:
            File metadata dict with id, path, filename, checksum, etc.

        Examples:
            result = session.files().upload("./model.pt", path="/models")
            result = session.files().upload("./config.json", path="/config", tags=["config"])
        """
        if not self._session._is_open:
            raise RuntimeError("Session not open. Use session.open() or context manager.")

        if self._session.write_protected:
            raise RuntimeError("Session is write-protected and cannot be modified.")

        file_path_obj = Path(file_path)
        if not file_path_obj.exists():
            raise ValueError(f"File not found: {file_path}")

        if not file_path_obj.is_file():
            raise ValueError(f"Path is not a file: {file_path}")

        # Check file size (max 5GB)
        file_size = file_path_obj.stat().st_size
        MAX_FILE_SIZE = 5 * 1024 * 1024 * 1024  # 5GB in bytes
        if file_size > MAX_FILE_SIZE:
            raise ValueError(f"File size ({file_size} bytes) exceeds 5GB limit")

        # Compute checksum
        checksum = compute_sha256(file_path)

        # Detect MIME type
        content_type = get_mime_type(file_path)

        # Get filename
        filename = file_path_obj.name

        # Upload through session (use 'path' but internally it's still 'prefix')
        return self._session._upload_file(
            file_path=file_path,
            prefix=path,  # Map path -> prefix internally
            filename=filename,
            description=description,
            tags=tags or [],
            metadata=metadata,
            checksum=checksum,
            content_type=content_type,
            size_bytes=file_size
        )

    def list(self, *, path: Optional[str] = None, tags: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        List files with optional filters.

        Args:
            path: Filter by path prefix
            tags: Filter by tags

        Returns:
            List of file metadata dicts

        Examples:
            files = session.files().list()  # All files
            files = session.files().list(path="/models")  # Filter by path
        """
        if not self._session._is_open:
            raise RuntimeError("Session not open. Use session.open() or context manager.")

        return self._session._list_files(
            prefix=path,  # Map path -> prefix internally
            tags=tags
        )


class FileBuilder:
    """
    Fluent interface for file operations.

    Usage:
        # Upload file
        session.file(file_path="./model.pt", prefix="/models").save()

        # List files
        files = session.file().list()
        files = session.file(prefix="/models").list()

        # Download file
        session.file(file_id="123").download()
        session.file(file_id="123", dest_path="./model.pt").download()

        # Delete file
        session.file(file_id="123").delete()
    """

    def __init__(self, session: 'Session', **kwargs):
        """
        Initialize file builder.

        Args:
            session: Parent session instance
            **kwargs: File operation parameters
                - file_path: Path to file to upload
                - prefix: Logical path prefix (default: "/")
                - description: Optional description
                - tags: Optional list of tags
                - metadata: Optional metadata dict
                - file_id: File ID for download/delete/update operations
                - dest_path: Destination path for download
        """
        self._session = session
        self._file_path = kwargs.get('file_path')
        self._prefix = kwargs.get('prefix', '/')
        self._description = kwargs.get('description')
        self._tags = kwargs.get('tags', [])
        self._metadata = kwargs.get('metadata')
        self._file_id = kwargs.get('file_id')
        self._dest_path = kwargs.get('dest_path')

    def save(self) -> Dict[str, Any]:
        """
        Upload and save the file.

        Returns:
            File metadata dict with id, path, filename, checksum, etc.

        Raises:
            RuntimeError: If session is not open or write-protected
            ValueError: If file_path not provided or file doesn't exist
            ValueError: If file size exceeds 5GB limit

        Examples:
            result = session.file(file_path="./model.pt", prefix="/models").save()
            # Returns: {"id": "123", "path": "/models", "filename": "model.pt", ...}
        """
        if not self._session._is_open:
            raise RuntimeError("Session not open. Use session.open() or context manager.")

        if self._session.write_protected:
            raise RuntimeError("Session is write-protected and cannot be modified.")

        if not self._file_path:
            raise ValueError("file_path is required for save() operation")

        file_path = Path(self._file_path)
        if not file_path.exists():
            raise ValueError(f"File not found: {self._file_path}")

        if not file_path.is_file():
            raise ValueError(f"Path is not a file: {self._file_path}")

        # Check file size (max 5GB)
        file_size = file_path.stat().st_size
        MAX_FILE_SIZE = 5 * 1024 * 1024 * 1024  # 5GB in bytes
        if file_size > MAX_FILE_SIZE:
            raise ValueError(f"File size ({file_size} bytes) exceeds 5GB limit")

        # Compute checksum
        checksum = compute_sha256(str(file_path))

        # Detect MIME type
        content_type = get_mime_type(str(file_path))

        # Get filename
        filename = file_path.name

        # Upload through session
        return self._session._upload_file(
            file_path=str(file_path),
            prefix=self._prefix,
            filename=filename,
            description=self._description,
            tags=self._tags,
            metadata=self._metadata,
            checksum=checksum,
            content_type=content_type,
            size_bytes=file_size
        )

    def list(self) -> List[Dict[str, Any]]:
        """
        List files with optional filters.

        Returns:
            List of file metadata dicts

        Raises:
            RuntimeError: If session is not open

        Examples:
            files = session.file().list()  # All files
            files = session.file(prefix="/models").list()  # Filter by prefix
            files = session.file(tags=["checkpoint"]).list()  # Filter by tags
        """
        if not self._session._is_open:
            raise RuntimeError("Session not open. Use session.open() or context manager.")

        return self._session._list_files(
            prefix=self._prefix if self._prefix != '/' else None,
            tags=self._tags if self._tags else None
        )

    def download(self) -> str:
        """
        Download file with automatic checksum verification.

        If dest_path not provided, downloads to current directory with original filename.

        Returns:
            Path to downloaded file

        Raises:
            RuntimeError: If session is not open
            ValueError: If file_id not provided
            ValueError: If checksum verification fails

        Examples:
            # Download to current directory with original filename
            path = session.file(file_id="123").download()

            # Download to custom path
            path = session.file(file_id="123", dest_path="./model.pt").download()
        """
        if not self._session._is_open:
            raise RuntimeError("Session not open. Use session.open() or context manager.")

        if not self._file_id:
            raise ValueError("file_id is required for download() operation")

        return self._session._download_file(
            file_id=self._file_id,
            dest_path=self._dest_path
        )

    def delete(self) -> Dict[str, Any]:
        """
        Delete file (soft delete).

        Returns:
            Dict with id and deletedAt timestamp

        Raises:
            RuntimeError: If session is not open or write-protected
            ValueError: If file_id not provided

        Examples:
            result = session.file(file_id="123").delete()
        """
        if not self._session._is_open:
            raise RuntimeError("Session not open. Use session.open() or context manager.")

        if self._session.write_protected:
            raise RuntimeError("Session is write-protected and cannot be modified.")

        if not self._file_id:
            raise ValueError("file_id is required for delete() operation")

        return self._session._delete_file(file_id=self._file_id)

    def update(self) -> Dict[str, Any]:
        """
        Update file metadata (description, tags, metadata).

        Returns:
            Updated file metadata dict

        Raises:
            RuntimeError: If session is not open or write-protected
            ValueError: If file_id not provided

        Examples:
            result = session.file(
                file_id="123",
                description="Updated description",
                tags=["new", "tags"],
                metadata={"updated": True}
            ).update()
        """
        if not self._session._is_open:
            raise RuntimeError("Session not open. Use session.open() or context manager.")

        if self._session.write_protected:
            raise RuntimeError("Session is write-protected and cannot be modified.")

        if not self._file_id:
            raise ValueError("file_id is required for update() operation")

        return self._session._update_file(
            file_id=self._file_id,
            description=self._description,
            tags=self._tags,
            metadata=self._metadata
        )


def compute_sha256(file_path: str) -> str:
    """
    Compute SHA256 checksum of a file.

    Args:
        file_path: Path to file

    Returns:
        Hex-encoded SHA256 checksum

    Examples:
        checksum = compute_sha256("./model.pt")
        # Returns: "abc123def456..."
    """
    sha256_hash = hashlib.sha256()

    with open(file_path, "rb") as f:
        # Read file in chunks to handle large files
        for byte_block in iter(lambda: f.read(8192), b""):
            sha256_hash.update(byte_block)

    return sha256_hash.hexdigest()


def get_mime_type(file_path: str) -> str:
    """
    Detect MIME type of a file.

    Args:
        file_path: Path to file

    Returns:
        MIME type string (default: "application/octet-stream")

    Examples:
        mime_type = get_mime_type("./model.pt")
        # Returns: "application/octet-stream"

        mime_type = get_mime_type("./image.png")
        # Returns: "image/png"
    """
    mime_type, _ = mimetypes.guess_type(file_path)
    return mime_type or "application/octet-stream"


def verify_checksum(file_path: str, expected_checksum: str) -> bool:
    """
    Verify SHA256 checksum of a file.

    Args:
        file_path: Path to file
        expected_checksum: Expected SHA256 checksum (hex-encoded)

    Returns:
        True if checksum matches, False otherwise

    Examples:
        is_valid = verify_checksum("./model.pt", "abc123...")
    """
    actual_checksum = compute_sha256(file_path)
    return actual_checksum == expected_checksum


def generate_snowflake_id() -> str:
    """
    Generate a simple Snowflake-like ID for local mode.

    Not a true Snowflake ID, but provides unique IDs for local storage.

    Returns:
        String representation of generated ID
    """
    import time
    import random

    timestamp = int(time.time() * 1000)
    random_bits = random.randint(0, 4095)
    return str((timestamp << 12) | random_bits)
