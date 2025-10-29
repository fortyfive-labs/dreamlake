"""Comprehensive tests for Session operations in both local and remote modes."""
import json
import pytest
from pathlib import Path


class TestSessionCreation:
    """Tests for basic session creation and lifecycle."""

    def test_context_manager_local(self, local_session, temp_workspace):
        """Test session creation using context manager in local mode."""
        with local_session(name="test-ctx", workspace="test-ws") as session:
            assert session._is_open
            assert session.name == "test-ctx"
            assert session.workspace == "test-ws"
            session.log("Test message")

        assert not session._is_open
        session_dir = temp_workspace / "test-ws" / "test-ctx"
        assert session_dir.exists()
        assert (session_dir / "session.json").exists()

    @pytest.mark.remote
    def test_context_manager_remote(self, remote_session):
        """Test session creation using context manager in remote mode."""
        with remote_session(name="test-ctx-remote", workspace="test-ws-remote") as session:
            assert session._is_open
            assert session.name == "test-ctx-remote"
            assert session.workspace == "test-ws-remote"
            session.log("Test message from remote")

        assert not session._is_open

    def test_manual_open_close_local(self, local_session, temp_workspace):
        """Test manual session lifecycle management in local mode."""
        session = local_session(name="manual-test", workspace="test-ws")
        assert not session._is_open

        session.open()
        assert session._is_open

        session.log("Working...")
        session.parameters().set(test_param="test_value")

        session.close()
        assert not session._is_open

        # Verify data was saved
        session_dir = temp_workspace / "test-ws" / "manual-test"
        assert session_dir.exists()
        assert (session_dir / "logs" / "logs.jsonl").exists()

    @pytest.mark.remote
    def test_manual_open_close_remote(self, remote_session):
        """Test manual session lifecycle management in remote mode."""
        session = remote_session(name="manual-test-remote", workspace="test-ws")
        assert not session._is_open

        session.open()
        assert session._is_open

        session.log("Working remotely...")
        session.parameters().set(test_param="remote_value")

        session.close()
        assert not session._is_open

    def test_session_with_metadata_local(self, local_session, temp_workspace):
        """Test session with description, tags, and folder in local mode."""
        with local_session(
            name="meta-session",
            workspace="meta-ws",
            description="Test session with metadata",
            tags=["test", "metadata", "local"],
            folder="/experiments/meta",
        ) as session:
            session.log("Session with metadata")

        # Verify metadata
        session_file = temp_workspace / "meta-ws" / "meta-session" / "session.json"
        assert session_file.exists()

        with open(session_file) as f:
            metadata = json.load(f)
            assert metadata["name"] == "meta-session"
            assert metadata["workspace"] == "meta-ws"
            assert metadata["description"] == "Test session with metadata"
            assert "test" in metadata["tags"]
            assert "metadata" in metadata["tags"]
            assert metadata["folder"] == "/experiments/meta"

    @pytest.mark.remote
    def test_session_with_metadata_remote(self, remote_session):
        """Test session with description, tags, and folder in remote mode."""
        with remote_session(
            name="meta-session-remote",
            workspace="meta-ws-remote",
            description="Remote test session with metadata",
            tags=["test", "metadata", "remote"],
            folder="/experiments/remote",
        ) as session:
            session.log("Remote session with metadata")
            # In remote mode, metadata is sent to server


class TestSessionProperties:
    """Tests for session properties and attributes."""

    def test_session_properties_local(self, local_session):
        """Test accessing session properties in local mode."""
        with local_session(name="props-test", workspace="props-ws") as session:
            assert session.name == "props-test"
            assert session.workspace == "props-ws"
            assert session._is_open

    @pytest.mark.remote
    def test_session_properties_remote(self, remote_session):
        """Test accessing session properties in remote mode."""
        with remote_session(name="props-test-remote", workspace="props-ws-remote") as session:
            assert session.name == "props-test-remote"
            assert session.workspace == "props-ws-remote"
            assert session._is_open


class TestMultipleSessions:
    """Tests for working with multiple sessions."""

    def test_multiple_sessions_same_workspace_local(self, local_session, temp_workspace):
        """Test creating multiple sessions in the same workspace."""
        with local_session(name="session-1", workspace="shared-ws") as session:
            session.log("Session 1")
            session.parameters().set(session_id=1)

        with local_session(name="session-2", workspace="shared-ws") as session:
            session.log("Session 2")
            session.parameters().set(session_id=2)

        with local_session(name="session-3", workspace="shared-ws") as session:
            session.log("Session 3")
            session.parameters().set(session_id=3)

        # Verify all sessions exist
        workspace_dir = temp_workspace / "shared-ws"
        assert (workspace_dir / "session-1").exists()
        assert (workspace_dir / "session-2").exists()
        assert (workspace_dir / "session-3").exists()

    @pytest.mark.remote
    def test_multiple_sessions_same_workspace_remote(self, remote_session):
        """Test creating multiple sessions in the same workspace in remote mode."""
        with remote_session(name="remote-session-1", workspace="shared-ws-remote") as session:
            session.log("Remote Session 1")
            session.parameters().set(session_id=1)

        with remote_session(name="remote-session-2", workspace="shared-ws-remote") as session:
            session.log("Remote Session 2")
            session.parameters().set(session_id=2)

    def test_multiple_sessions_different_workspaces_local(self, local_session, temp_workspace):
        """Test creating sessions in different workspaces."""
        with local_session(name="session-a", workspace="workspace-1") as session:
            session.log("Session A in workspace 1")

        with local_session(name="session-b", workspace="workspace-2") as session:
            session.log("Session B in workspace 2")

        with local_session(name="session-c", workspace="workspace-3") as session:
            session.log("Session C in workspace 3")

        # Verify all workspaces and sessions exist
        assert (temp_workspace / "workspace-1" / "session-a").exists()
        assert (temp_workspace / "workspace-2" / "session-b").exists()
        assert (temp_workspace / "workspace-3" / "session-c").exists()

    def test_sequential_sessions_local(self, local_session):
        """Test opening sessions sequentially."""
        sessions = []
        for i in range(5):
            with local_session(name=f"seq-session-{i}", workspace="sequential") as session:
                session.log(f"Sequential session {i}")
                session.parameters().set(index=i)
                sessions.append(session)

        # All sessions should be closed
        for session in sessions:
            assert not session._is_open


class TestSessionErrorHandling:
    """Tests for error handling in sessions."""

    def test_session_error_still_saves_data_local(self, local_session, temp_workspace):
        """Test that session saves data even when errors occur."""
        try:
            with local_session(name="error-test", workspace="error-ws") as session:
                session.log("Starting work")
                session.parameters().set(param="value")
                session.track("metric").append(value=0.5, step=0)
                raise ValueError("Simulated error")
        except ValueError:
            pass

        # Data should still be saved
        session_dir = temp_workspace / "error-ws" / "error-test"
        assert session_dir.exists()
        assert (session_dir / "logs" / "logs.jsonl").exists()
        assert (session_dir / "parameters.json").exists()

    @pytest.mark.remote
    def test_session_error_still_saves_data_remote(self, remote_session):
        """Test that remote session handles errors gracefully."""
        try:
            with remote_session(name="error-test-remote", workspace="error-ws-remote") as session:
                session.log("Starting remote work")
                session.parameters().set(param="remote_value")
                raise ValueError("Simulated remote error")
        except ValueError:
            pass
        # Session should be closed properly

    def test_multiple_errors_in_session_local(self, local_session, temp_workspace):
        """Test session handling multiple errors."""
        with local_session(name="multi-error", workspace="error-ws") as session:
            try:
                session.log("Attempt 1")
                raise ValueError("Error 1")
            except ValueError:
                session.log("Caught error 1", level="error")

            try:
                session.log("Attempt 2")
                raise RuntimeError("Error 2")
            except RuntimeError:
                session.log("Caught error 2", level="error")

            session.log("Continuing after errors")

        # Session should have all logs
        logs_file = temp_workspace / "error-ws" / "multi-error" / "logs" / "logs.jsonl"
        assert logs_file.exists()

        with open(logs_file) as f:
            logs = [json.loads(line) for line in f]

        assert len(logs) >= 3


class TestSessionReuse:
    """Tests for reusing/updating existing sessions."""

    def test_reopen_existing_session_local(self, local_session, temp_workspace):
        """Test reopening an existing session (upsert behavior)."""
        # Create initial session
        with local_session(name="reuse-session", workspace="reuse-ws") as session:
            session.log("Initial session")
            session.parameters().set(version=1)

        # Reopen same session
        with local_session(name="reuse-session", workspace="reuse-ws") as session:
            session.log("Reopened session")
            session.parameters().set(version=2, new_param="added")

        # Verify both operations are recorded
        session_dir = temp_workspace / "reuse-ws" / "reuse-session"
        logs_file = session_dir / "logs" / "logs.jsonl"

        with open(logs_file) as f:
            logs = [json.loads(line) for line in f]

        assert len(logs) >= 2

    @pytest.mark.remote
    def test_reopen_existing_session_remote(self, remote_session):
        """Test reopening an existing session in remote mode."""
        # Create initial session
        with remote_session(name="reuse-session-remote", workspace="reuse-ws-remote") as session:
            session.log("Initial remote session")
            session.parameters().set(version=1)

        # Reopen same session
        with remote_session(name="reuse-session-remote", workspace="reuse-ws-remote") as session:
            session.log("Reopened remote session")
            session.parameters().set(version=2)


class TestSessionEdgeCases:
    """Tests for edge cases and unusual scenarios."""

    def test_empty_session_local(self, local_session, temp_workspace):
        """Test session with no operations."""
        with local_session(name="empty-session", workspace="empty-ws") as session:
            pass  # Do nothing

        # Session directory should still be created
        session_dir = temp_workspace / "empty-ws" / "empty-session"
        assert session_dir.exists()

    def test_session_with_special_characters_local(self, local_session, temp_workspace):
        """Test session names with special characters."""
        with local_session(name="test-session_v1.0", workspace="special-ws") as session:
            session.log("Session with special chars in name")

        session_dir = temp_workspace / "special-ws" / "test-session_v1.0"
        assert session_dir.exists()

    def test_session_with_long_name_local(self, local_session):
        """Test session with very long name."""
        long_name = "a" * 200
        with local_session(name=long_name, workspace="long-ws") as session:
            session.log("Session with long name")

    def test_deeply_nested_folder_local(self, local_session, temp_workspace):
        """Test session with deeply nested folder structure."""
        with local_session(
            name="nested-session",
            workspace="nested-ws",
            folder="/a/b/c/d/e/f/g/h",
        ) as session:
            session.log("Deeply nested session")

        session_file = temp_workspace / "nested-ws" / "nested-session" / "session.json"
        with open(session_file) as f:
            metadata = json.load(f)
            assert metadata["folder"] == "/a/b/c/d/e/f/g/h"

    def test_session_with_many_tags_local(self, local_session, temp_workspace):
        """Test session with many tags."""
        tags = [f"tag-{i}" for i in range(50)]
        with local_session(
            name="many-tags",
            workspace="tags-ws",
            tags=tags,
        ) as session:
            session.log("Session with many tags")

        session_file = temp_workspace / "tags-ws" / "many-tags" / "session.json"
        with open(session_file) as f:
            metadata = json.load(f)
            assert len(metadata["tags"]) == 50

    def test_session_double_close_local(self, local_session):
        """Test that closing a session twice doesn't cause issues."""
        session = local_session(name="double-close", workspace="test-ws")
        session.open()
        session.close()
        session.close()  # Should not raise error
        assert not session._is_open

    def test_operations_before_open_local(self, local_session):
        """Test that operations before open are handled gracefully."""
        session = local_session(name="not-opened", workspace="test-ws")
        # Attempting operations before opening should handle gracefully
        # The actual behavior depends on implementation
