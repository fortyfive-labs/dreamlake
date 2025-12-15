"""
Test concurrent file operations to verify thread-safety.
"""

import threading
import tempfile
import shutil
from pathlib import Path
import json
import time

from dreamlake import Session


def test_concurrent_file_uploads():
    """Test that concurrent file uploads don't lose metadata entries."""
    # Create temporary directory for test
    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = Path(tmpdir) / ".dreamlake"

        # Create test files
        test_files_dir = Path(tmpdir) / "test_files"
        test_files_dir.mkdir()

        num_files = 20
        test_files = []
        for i in range(num_files):
            test_file = test_files_dir / f"file_{i}.txt"
            test_file.write_text(f"Content of file {i}")
            test_files.append(test_file)

        # Create session
        session = Session(
            name="concurrent-test",
            workspace="test-workspace",
            local_path=str(local_path)
        )
        session.open()

        # Upload files concurrently
        results = [None] * num_files
        errors = [None] * num_files

        def upload_file(index):
            """Upload a single file."""
            try:
                result = session.file(
                    file_path=str(test_files[index]),
                    prefix="/test"
                ).save()
                results[index] = result
            except Exception as e:
                errors[index] = e

        # Create threads
        threads = []
        for i in range(num_files):
            thread = threading.Thread(target=upload_file, args=(i,))
            threads.append(thread)

        # Start all threads
        for thread in threads:
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Check for errors
        for i, error in enumerate(errors):
            if error:
                raise AssertionError(f"Thread {i} failed: {error}")

        # Verify all files were uploaded successfully
        assert all(r is not None for r in results), "Some uploads failed"

        # Verify all file IDs are unique
        file_ids = [r["id"] for r in results]
        assert len(file_ids) == len(set(file_ids)), "Duplicate file IDs detected"

        # List files from session
        listed_files = session.file().list()
        assert len(listed_files) == num_files, \
            f"Expected {num_files} files, but found {len(listed_files)}"

        # Verify metadata file integrity
        metadata_file = local_path / "test-workspace" / "concurrent-test" / "files" / ".files_metadata.json"
        assert metadata_file.exists(), "Metadata file not found"

        with open(metadata_file, "r") as f:
            metadata = json.load(f)

        assert len(metadata["files"]) == num_files, \
            f"Metadata contains {len(metadata['files'])} files, expected {num_files}"

        # Verify all uploaded file IDs are in metadata
        metadata_ids = {f["id"] for f in metadata["files"]}
        assert metadata_ids == set(file_ids), "Metadata file IDs don't match uploaded file IDs"

        session.close()


def test_concurrent_file_operations_mixed():
    """Test concurrent uploads, updates, and deletes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = Path(tmpdir) / ".dreamlake"

        # Create test files
        test_files_dir = Path(tmpdir) / "test_files"
        test_files_dir.mkdir()

        num_files = 10
        test_files = []
        for i in range(num_files):
            test_file = test_files_dir / f"file_{i}.txt"
            test_file.write_text(f"Content of file {i}")
            test_files.append(test_file)

        # Create session
        session = Session(
            name="mixed-ops-test",
            workspace="test-workspace",
            local_path=str(local_path)
        )
        session.open()

        # First, upload some files sequentially
        uploaded_ids = []
        for i in range(5):
            result = session.file(
                file_path=str(test_files[i]),
                prefix="/test"
            ).save()
            uploaded_ids.append(result["id"])

        errors = []

        # Define operations
        def upload_new_file(index):
            """Upload a new file."""
            try:
                session.file(
                    file_path=str(test_files[index]),
                    prefix="/test"
                ).save()
            except Exception as e:
                errors.append(f"Upload {index} failed: {e}")

        def update_file(file_id, index):
            """Update file metadata."""
            try:
                session.file(
                    file_id=file_id,
                    description=f"Updated description {index}"
                ).update()
            except Exception as e:
                errors.append(f"Update {file_id} failed: {e}")

        def delete_file(file_id):
            """Delete a file."""
            try:
                session.file(file_id=file_id).delete()
            except Exception as e:
                errors.append(f"Delete {file_id} failed: {e}")

        # Create mixed operations
        threads = []

        # Upload new files (5-9)
        for i in range(5, num_files):
            thread = threading.Thread(target=upload_new_file, args=(i,))
            threads.append(thread)

        # Update existing files (0-2)
        for i in range(3):
            thread = threading.Thread(target=update_file, args=(uploaded_ids[i], i))
            threads.append(thread)

        # Delete some files (3-4)
        for i in range(3, 5):
            thread = threading.Thread(target=delete_file, args=(uploaded_ids[i],))
            threads.append(thread)

        # Start all threads
        for thread in threads:
            thread.start()

        # Wait for completion
        for thread in threads:
            thread.join()

        # Check for errors
        assert len(errors) == 0, f"Operations failed: {errors}"

        # Verify final state
        listed_files = session.file().list()
        # Should have 5 new uploads + 3 updated files = 8 active files
        # (2 were deleted)
        assert len(listed_files) == 8, \
            f"Expected 8 active files, found {len(listed_files)}"

        # Verify metadata file integrity
        metadata_file = local_path / "test-workspace" / "mixed-ops-test" / "files" / ".files_metadata.json"
        with open(metadata_file, "r") as f:
            metadata = json.load(f)

        # Total entries should be 10 (5 original + 5 new)
        assert len(metadata["files"]) == 10, \
            f"Expected 10 total entries in metadata, found {len(metadata['files'])}"

        # Active files (not deleted) should be 8
        active_files = [f for f in metadata["files"] if f.get("deletedAt") is None]
        assert len(active_files) == 8, \
            f"Expected 8 active files in metadata, found {len(active_files)}"

        session.close()


def test_concurrent_file_uploads_new_api():
    """Test concurrent uploads using the new files().upload() API."""
    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = Path(tmpdir) / ".dreamlake"

        # Create test files
        test_files_dir = Path(tmpdir) / "test_files"
        test_files_dir.mkdir()

        num_files = 15
        test_files = []
        for i in range(num_files):
            test_file = test_files_dir / f"newapi_{i}.txt"
            test_file.write_text(f"New API content {i}")
            test_files.append(test_file)

        # Create session
        with Session(
            name="newapi-test",
            workspace="test-workspace",
            local_path=str(local_path)
        ) as session:
            results = [None] * num_files
            errors = [None] * num_files

            def upload_file(index):
                """Upload using new API."""
                try:
                    result = session.files().upload(
                        str(test_files[index]),
                        path="/newapi",
                        tags=[f"tag_{index}"]
                    )
                    results[index] = result
                except Exception as e:
                    errors[index] = e

            # Create and start threads
            threads = [threading.Thread(target=upload_file, args=(i,)) for i in range(num_files)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            # Check for errors
            for i, error in enumerate(errors):
                if error:
                    raise AssertionError(f"Upload {i} failed: {error}")

            # Verify all uploads succeeded
            assert all(r is not None for r in results), "Some uploads failed"

            # Verify file count
            listed_files = session.files().list()
            assert len(listed_files) == num_files, \
                f"Expected {num_files} files, found {len(listed_files)}"

            # Verify no duplicates
            file_ids = [r["id"] for r in results]
            assert len(file_ids) == len(set(file_ids)), "Duplicate file IDs"


def test_file_lock_timeout():
    """Test that file lock properly handles timeout scenarios."""
    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = Path(tmpdir) / ".dreamlake"
        files_dir = local_path / "test-workspace" / "lock-test" / "files"
        files_dir.mkdir(parents=True, exist_ok=True)

        lock_file = files_dir / ".files_metadata.lock"

        # Import FileLock
        from dreamlake.storage import FileLock

        # Acquire lock in main thread
        with FileLock(lock_file):
            # Try to acquire same lock in another thread (should timeout quickly)
            error_holder = [None]

            def try_acquire():
                try:
                    # This should timeout
                    with FileLock(lock_file):
                        pass
                except TimeoutError as e:
                    error_holder[0] = e

            thread = threading.Thread(target=try_acquire)
            thread.start()
            thread.join(timeout=2)  # Wait max 2 seconds

            # Verify timeout occurred
            assert error_holder[0] is not None, "Expected TimeoutError but lock was acquired"
            assert "Could not acquire lock" in str(error_holder[0])


if __name__ == "__main__":
    test_concurrent_file_uploads()
    test_concurrent_file_operations_mixed()
    test_concurrent_file_uploads_new_api()
    test_file_lock_timeout()
    print("All concurrent file tests passed!")
