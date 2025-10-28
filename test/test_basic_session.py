"""Tests for basic session operations."""
import json
from pathlib import Path


def test_session_creation_with_context_manager(local_session, temp_workspace):
    """Test basic session creation using context manager."""
    with local_session(name="hello-dreamlake", workspace="tutorials") as session:
        session.log("Hello from Dreamlake!", level="info")
        session.parameters().set(message="Hello World")

    # Verify session directory was created
    session_dir = temp_workspace / "tutorials" / "hello-dreamlake"
    assert session_dir.exists()
    assert (session_dir / "session.json").exists()


def test_session_with_metadata(local_session, temp_workspace):
    """Test session creation with description, tags, and folder."""
    with local_session(
        name="mnist-baseline",
        workspace="computer-vision",
        description="Baseline CNN for MNIST classification",
        tags=["mnist", "cnn", "baseline"],
        folder="/experiments/mnist",
    ) as session:
        session.log("Session created with metadata")

    # Verify metadata was saved
    session_dir = temp_workspace / "computer-vision" / "mnist-baseline"
    session_file = session_dir / "session.json"

    assert session_file.exists()
    with open(session_file) as f:
        metadata = json.load(f)
        assert metadata["name"] == "mnist-baseline"
        assert metadata["workspace"] == "computer-vision"
        assert metadata["description"] == "Baseline CNN for MNIST classification"
        assert "mnist" in metadata["tags"]
        assert "cnn" in metadata["tags"]
        assert metadata["folder"] == "/experiments/mnist"


def test_session_manual_open_close(local_session, temp_workspace):
    """Test manual session lifecycle management."""
    session = local_session(name="manual-session", workspace="test")

    # The session is not initially open
    assert not session._is_open

    # Open session
    session.open()
    assert session._is_open

    # Do work
    session.log("Working...")

    # Close session
    session.close()
    assert not session._is_open

    # Verify data was saved
    session_dir = temp_workspace / "test" / "manual-session"
    assert session_dir.exists()


def test_session_auto_close_on_context_exit(local_session):
    """Test that session is automatically closed when exiting context manager."""
    with local_session(name="auto-close", workspace="test") as session:
        assert session._is_open
        session.log("Working...")

    # After exiting context, the session should be closed
    assert not session._is_open


def test_multiple_sessions_same_workspace(local_session, temp_workspace):
    """Test creating multiple sessions in the same workspace."""
    # Create first session
    with local_session(name="session-1", workspace="shared") as session:
        session.log("Session 1")

    # Create second session
    with local_session(name="session-2", workspace="shared") as session:
        session.log("Session 2")

    # Verify both sessions exist
    workspace_dir = temp_workspace / "shared"
    assert (workspace_dir / "session-1").exists()
    assert (workspace_dir / "session-2").exists()


def test_session_name_and_workspace_properties(local_session):
    """Test that session properties are accessible."""
    with local_session(name="my-session", workspace="my-workspace") as session:
        assert session.name == "my-session"
        assert session.workspace == "my-workspace"


def test_session_error_handling(local_session, temp_workspace):
    """Test that session handles errors gracefully and still saves data."""
    try:
        with local_session(name="error-test", workspace="test") as session:
            session.log("Starting work")
            session.parameters().set(param="value")
            raise ValueError("Simulated error")
    except ValueError:
        pass

    # Session should still be closed and data saved
    session_dir = temp_workspace / "test" / "error-test"
    assert session_dir.exists()
    assert (session_dir / "logs" / "logs.jsonl").exists()
    assert (session_dir / "parameters.json").exists()
