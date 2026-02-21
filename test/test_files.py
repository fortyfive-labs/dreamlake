"""Tests for file upload functionality."""
import json
from pathlib import Path
import hashlib


def test_upload_single_file(local_session, sample_files, temp_workspace):
    """Test uploading a single file."""
    with local_session(name="file-single", workspace="test") as session:
        result = session.file(
            file_path=sample_files["model"],
            prefix="/models",
            description="Model weights",
            tags=["model"],
        ).save()

        assert result["filename"] == "model.txt"
        assert result["sizeBytes"] > 0
        assert "checksum" in result

    # Verify file was saved
    files_dir = temp_workspace / "test" / "file-single" / "files"
    assert files_dir.exists()
    # File should be in a subdirectory (by ID)
    saved_files = list(files_dir.glob("*/model.txt"))
    assert len(saved_files) == 1


def test_upload_multiple_files(local_session, sample_files, temp_workspace):
    """Test uploading multiple files."""
    with local_session(name="file-multi", workspace="test") as session:
        # Upload model
        session.file(
            file_path=sample_files["model"],
            prefix="/models",
            tags=["model"],
        ).save()

        # Upload config
        session.file(
            file_path=sample_files["config"],
            prefix="/config",
            tags=["config"],
        ).save()

        # Upload results
        session.file(
            file_path=sample_files["results"],
            prefix="/results",
            tags=["results"],
        ).save()

    # Verify all files were saved
    files_dir = temp_workspace / "test" / "file-multi" / "files"
    assert (files_dir / ".files_metadata.json").exists()

    # Should have 3 file subdirectories
    file_dirs = [d for d in files_dir.iterdir() if d.is_dir()]
    assert len(file_dirs) == 3


def test_file_with_metadata(local_session, sample_files, temp_workspace):
    """Test uploading file with custom metadata."""
    with local_session(name="file-meta", workspace="test") as session:
        result = session.file(
            file_path=sample_files["results"],
            prefix="/results",
            description="Training results per epoch",
            tags=["results", "metrics"],
            metadata={"epochs": 10, "format": "csv"},
        ).save()

        assert "uploadedAt" in result
        assert result["tags"] == ["results", "metrics"]

    # Verify metadata was saved
    metadata_file = temp_workspace / "test" / "file-meta" / "files" / ".files_metadata.json"
    assert metadata_file.exists()

    with open(metadata_file) as f:
        files_data = json.load(f)

    # Get files array from metadata
    files_meta = files_data["files"]

    # Find our file in the metadata
    our_file = None
    for file_info in files_meta:
        if file_info["filename"] == "results.txt":
            our_file = file_info
            break

    assert our_file is not None
    assert our_file["description"] == "Training results per epoch"
    assert "results" in our_file["tags"]
    assert our_file["metadata"]["epochs"] == 10


def test_file_checksum(local_session, sample_files, temp_workspace):
    """Test that file checksum is correctly calculated."""
    # Calculate expected checksum
    with open(sample_files["model"], "rb") as f:
        expected_checksum = hashlib.sha256(f.read()).hexdigest()

    with local_session(name="file-checksum", workspace="test") as session:
        result = session.file(
            file_path=sample_files["model"],
            prefix="/models",
        ).save()

        assert result["checksum"] == expected_checksum


def test_list_files(local_session, sample_files, temp_workspace):
    """Test listing all files in a session."""
    with local_session(name="file-list", workspace="test") as session:
        # Upload files
        session.file(file_path=sample_files["model"], prefix="/models").save()
        session.file(file_path=sample_files["config"], prefix="/config").save()

        # List files
        files = session.file().list()

        assert len(files) == 2
        filenames = [f["filename"] for f in files]
        assert "model.txt" in filenames
        assert "config.json" in filenames


def test_file_paths_and_prefixes(local_session, sample_files, temp_workspace):
    """Test that file prefixes are correctly stored."""
    with local_session(name="file-prefix", workspace="test") as session:
        session.file(file_path=sample_files["model"], prefix="/models/v1").save()
        session.file(file_path=sample_files["config"], prefix="/configs/prod").save()

        files = session.file().list()

    assert len(files) == 2
    # Check that paths are correct
    paths = [f["path"] for f in files]
    assert "/models/v1" in paths
    assert "/configs/prod" in paths


def test_file_tags(local_session, sample_files, temp_workspace):
    """Test file tagging."""
    with local_session(name="file-tags", workspace="test") as session:
        session.file(
            file_path=sample_files["model"],
            prefix="/models",
            tags=["best", "final", "v1.0"],
        ).save()

        files = session.file().list()

    assert len(files) == 1
    assert "best" in files[0]["tags"]
    assert "final" in files[0]["tags"]
    assert "v1.0" in files[0]["tags"]


def test_no_files_uploaded(local_session, temp_workspace):
    """Test session with no files uploaded."""
    with local_session(name="no-files", workspace="test") as session:
        session.log("No files uploaded")

    # Files directory should exist but be empty
    files_dir = temp_workspace / "test" / "no-files" / "files"
    assert files_dir.exists()

    # Should have no file subdirectories (except maybe .files_metadata.json)
    file_dirs = [d for d in files_dir.iterdir() if d.is_dir()]
    assert len(file_dirs) == 0


def test_file_size_tracking(local_session, sample_files, temp_workspace):
    """Test that file sizes are correctly tracked."""
    # Get actual file size
    model_size = Path(sample_files["model"]).stat().st_size

    with local_session(name="file-size", workspace="test") as session:
        result = session.file(file_path=sample_files["model"], prefix="/models").save()

        assert result["sizeBytes"] == model_size


def test_upload_same_filename_different_prefix(local_session, sample_files, temp_workspace):
    """Test uploading files with same name but different prefixes."""
    with local_session(name="file-duplicate", workspace="test") as session:
        # Upload same file to different locations
        session.file(file_path=sample_files["model"], prefix="/models/v1").save()
        session.file(file_path=sample_files["model"], prefix="/models/v2").save()

        files = session.file().list()

    # Should have 2 files (same filename, different paths)
    assert len(files) == 2
    assert all(f["filename"] == "model.txt" for f in files)
    paths = [f["path"] for f in files]
    assert "/models/v1" in paths
    assert "/models/v2" in paths
