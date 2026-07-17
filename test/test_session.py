"""Comprehensive tests for Episode operations in both local and remote modes."""
import json
import pytest
from pathlib import Path


class TestEpisodeCreation:
    """Tests for basic episode creation and lifecycle."""

    def test_context_manager_local(self, local_episode, temp_workspace):
        """Test episode creation using context manager in local mode."""
        with local_episode(prefix="test-ws/test-ctx") as episode:
            assert episode._is_open
            assert episode.name == "test-ctx"
            assert episode.workspace == "test-ws"
            episode.log("Test message")

        assert not episode._is_open
        episode_dir = temp_workspace / "test-ws" / "test-ctx"
        assert episode_dir.exists()
        assert (episode_dir / "episode.json").exists()

    @pytest.mark.remote
    def test_context_manager_remote(self, remote_episode):
        """Test episode creation using context manager in remote mode."""
        with remote_episode(prefix="test-ws-remote/test-ctx-remote") as episode:
            assert episode._is_open
            assert episode.name == "test-ctx-remote"
            assert episode.workspace == "test-ws-remote"
            episode.log("Test message from remote")

        assert not episode._is_open

    def test_manual_open_close_local(self, local_episode, temp_workspace):
        """Test manual episode lifecycle management in local mode."""
        episode = local_episode(prefix="test-ws/manual-test")
        assert not episode._is_open

        episode.open()
        assert episode._is_open

        episode.log("Working...")
        episode.params.set(test_param="test_value")

        episode.close()
        assert not episode._is_open

        # Verify data was saved
        episode_dir = temp_workspace / "test-ws" / "manual-test"
        assert episode_dir.exists()
        assert (episode_dir / "logs" / "logs.jsonl").exists()

    @pytest.mark.remote
    def test_manual_open_close_remote(self, remote_episode):
        """Test manual episode lifecycle management in remote mode."""
        episode = remote_episode(prefix="test-ws/manual-test-remote")
        assert not episode._is_open

        episode.open()
        assert episode._is_open

        episode.log("Working remotely...")
        episode.params.set(test_param="remote_value")

        episode.close()
        assert not episode._is_open

    def test_episode_with_metadata_local(self, local_episode, temp_workspace):
        """Test episode with readme and tags in local mode (ML-Dash API)."""
        with local_episode(
            prefix="meta-ws/meta-episode",
            readme="Test episode with metadata",
            tags=["test", "metadata", "local"],
        ) as episode:
            episode.log("Episode with metadata")

        # Verify metadata
        episode_file = temp_workspace / "meta-ws" / "meta-episode" / "episode.json"
        assert episode_file.exists()

        with open(episode_file) as f:
            metadata = json.load(f)
            assert metadata["name"] == "meta-episode"
            assert metadata["workspace"] == "meta-ws"
            # Check for description field (mapped from readme)
            assert metadata.get("description") == "Test episode with metadata"
            assert "test" in metadata["tags"]
            assert "metadata" in metadata["tags"]

    @pytest.mark.remote
    def test_episode_with_metadata_remote(self, remote_episode):
        """Test episode with description, tags, and folder in remote mode."""
        with remote_episode(prefix="meta-ws-remote/meta-episode-remote",
            tags=["test", "metadata", "remote"],
        ) as episode:
            episode.log("Remote episode with metadata")
            # In remote mode, metadata is sent to server


class TestEpisodeProperties:
    """Tests for episode properties and attributes."""

    def test_episode_properties_local(self, local_episode):
        """Test accessing episode properties in local mode."""
        with local_episode(prefix="props-ws/props-test") as episode:
            assert episode.name == "props-test"
            assert episode.workspace == "props-ws"
            assert episode._is_open

    @pytest.mark.remote
    def test_episode_properties_remote(self, remote_episode):
        """Test accessing episode properties in remote mode."""
        with remote_episode(prefix="props-ws-remote/props-test-remote") as episode:
            assert episode.name == "props-test-remote"
            assert episode.workspace == "props-ws-remote"
            assert episode._is_open


class TestMultipleEpisodes:
    """Tests for working with multiple episodes."""

    def test_multiple_episodes_same_workspace_local(self, local_episode, temp_workspace):
        """Test creating multiple episodes in the same workspace."""
        with local_episode(prefix="shared-ws/episode-1") as episode:
            episode.log("Episode 1")
            episode.params.set(episode_id=1)

        with local_episode(prefix="shared-ws/episode-2") as episode:
            episode.log("Episode 2")
            episode.params.set(episode_id=2)

        with local_episode(prefix="shared-ws/episode-3") as episode:
            episode.log("Episode 3")
            episode.params.set(episode_id=3)

        # Verify all episodes exist
        workspace_dir = temp_workspace / "shared-ws"
        assert (workspace_dir / "episode-1").exists()
        assert (workspace_dir / "episode-2").exists()
        assert (workspace_dir / "episode-3").exists()

    @pytest.mark.remote
    def test_multiple_episodes_same_workspace_remote(self, remote_episode):
        """Test creating multiple episodes in the same workspace in remote mode."""
        with remote_episode(prefix="shared-ws-remote/remote-episode-1") as episode:
            episode.log("Remote Episode 1")
            episode.params.set(episode_id=1)

        with remote_episode(prefix="shared-ws-remote/remote-episode-2") as episode:
            episode.log("Remote Episode 2")
            episode.params.set(episode_id=2)

    def test_multiple_episodes_different_workspaces_local(self, local_episode, temp_workspace):
        """Test creating episodes in different workspaces."""
        with local_episode(prefix="workspace-1/episode-a") as episode:
            episode.log("Episode A in workspace 1")

        with local_episode(prefix="workspace-2/episode-b") as episode:
            episode.log("Episode B in workspace 2")

        with local_episode(prefix="workspace-3/episode-c") as episode:
            episode.log("Episode C in workspace 3")

        # Verify all workspaces and episodes exist
        assert (temp_workspace / "workspace-1" / "episode-a").exists()
        assert (temp_workspace / "workspace-2" / "episode-b").exists()
        assert (temp_workspace / "workspace-3" / "episode-c").exists()

    def test_sequential_episodes_local(self, local_episode):
        """Test opening episodes sequentially (ML-Dash API)."""
        episodes = []
        for i in range(5):
            with local_episode(prefix=f"sequential/seq-episode-{i}") as episode:
                episode.log(f"Sequential episode {i}")
                episode.params.set(index=i)
                episodes.append(episode)

        # All episodes should be closed
        for episode in episodes:
            assert not episode._is_open


class TestEpisodeErrorHandling:
    """Tests for error handling in episodes."""

    def test_episode_error_still_saves_data_local(self, local_episode, temp_workspace):
        """Test that episode saves data even when errors occur."""
        try:
            with local_episode(prefix="error-ws/error-test") as episode:
                episode.log("Starting work")
                episode.params.set(param="value")
                episode.track("metric").append(value=0.5, step=0)
                raise ValueError("Simulated error")
        except ValueError:
            pass

        # Data should still be saved
        episode_dir = temp_workspace / "error-ws" / "error-test"
        assert episode_dir.exists()
        assert (episode_dir / "logs" / "logs.jsonl").exists()
        assert (episode_dir / "parameters.json").exists()

    @pytest.mark.remote
    def test_episode_error_still_saves_data_remote(self, remote_episode):
        """Test that remote episode handles errors gracefully."""
        try:
            with remote_episode(prefix="error-ws-remote/error-test-remote") as episode:
                episode.log("Starting remote work")
                episode.params.set(param="remote_value")
                raise ValueError("Simulated remote error")
        except ValueError:
            pass
        # Episode should be closed properly

    def test_multiple_errors_in_episode_local(self, local_episode, temp_workspace):
        """Test episode handling multiple errors."""
        with local_episode(prefix="error-ws/multi-error") as episode:
            try:
                episode.log("Attempt 1")
                raise ValueError("Error 1")
            except ValueError:
                episode.log("Caught error 1", level="error")

            try:
                episode.log("Attempt 2")
                raise RuntimeError("Error 2")
            except RuntimeError:
                episode.log("Caught error 2", level="error")

            episode.log("Continuing after errors")

        # Episode should have all logs
        logs_file = temp_workspace / "error-ws" / "multi-error" / "logs" / "logs.jsonl"
        assert logs_file.exists()

        with open(logs_file) as f:
            logs = [json.loads(line) for line in f]

        assert len(logs) >= 3


class TestEpisodeReuse:
    """Tests for reusing/updating existing episodes."""

    def test_reopen_existing_episode_local(self, local_episode, temp_workspace):
        """Test reopening an existing episode (upsert behavior)."""
        # Create initial episode
        with local_episode(prefix="reuse-ws/reuse-episode") as episode:
            episode.log("Initial episode")
            episode.params.set(version=1)

        # Reopen same episode
        with local_episode(prefix="reuse-ws/reuse-episode") as episode:
            episode.log("Reopened episode")
            episode.params.set(version=2, new_param="added")

        # Verify both operations are recorded
        episode_dir = temp_workspace / "reuse-ws" / "reuse-episode"
        logs_file = episode_dir / "logs" / "logs.jsonl"

        with open(logs_file) as f:
            logs = [json.loads(line) for line in f]

        assert len(logs) >= 2

    @pytest.mark.remote
    def test_reopen_existing_episode_remote(self, remote_episode):
        """Test reopening an existing episode in remote mode."""
        # Create initial episode
        with remote_episode(prefix="reuse-ws-remote/reuse-episode-remote") as episode:
            episode.log("Initial remote episode")
            episode.params.set(version=1)

        # Reopen same episode
        with remote_episode(prefix="reuse-ws-remote/reuse-episode-remote") as episode:
            episode.log("Reopened remote episode")
            episode.params.set(version=2)


class TestEpisodeEdgeCases:
    """Tests for edge cases and unusual scenarios."""

    def test_empty_episode_local(self, local_episode, temp_workspace):
        """Test episode with no operations."""
        with local_episode(prefix="empty-ws/empty-episode") as episode:
            pass  # Do nothing

        # Episode directory should still be created
        episode_dir = temp_workspace / "empty-ws" / "empty-episode"
        assert episode_dir.exists()

    def test_episode_with_special_characters_local(self, local_episode, temp_workspace):
        """Test episode names with special characters."""
        with local_episode(prefix="special-ws/test-episode_v1.0") as episode:
            episode.log("Episode with special chars in name")

        episode_dir = temp_workspace / "special-ws" / "test-episode_v1.0"
        assert episode_dir.exists()

    def test_episode_with_long_name_local(self, local_episode):
        """Test episode with very long name (ML-Dash API)."""
        long_name = "a" * 200
        with local_episode(prefix=f"long-ws/{long_name}") as episode:
            episode.log("Episode with long name")

    def test_deeply_nested_folder_local(self, local_episode, temp_workspace):
        """Test episode with deeply nested prefix structure (ML-Dash API)."""
        # Note: folder parameter removed in ML-Dash API
        with local_episode(prefix="nested-ws/nested-episode") as episode:
            episode.log("Deeply nested episode")

        episode_file = temp_workspace / "nested-ws" / "nested-episode" / "episode.json"
        with open(episode_file) as f:
            metadata = json.load(f)
            # Just verify the episode was created successfully
            assert metadata["name"] == "nested-episode"
            assert metadata["workspace"] == "nested-ws"

    def test_episode_with_many_tags_local(self, local_episode, temp_workspace):
        """Test episode with many tags."""
        tags = [f"tag-{i}" for i in range(50)]
        with local_episode(prefix="tags-ws/many-tags",
            tags=tags,
        ) as episode:
            episode.log("Episode with many tags")

        episode_file = temp_workspace / "tags-ws" / "many-tags" / "episode.json"
        with open(episode_file) as f:
            metadata = json.load(f)
            assert len(metadata["tags"]) == 50

    def test_episode_double_close_local(self, local_episode):
        """Test that closing a episode twice doesn't cause issues."""
        episode = local_episode(prefix="test-ws/double-close")
        episode.open()
        episode.close()
        episode.close()  # Should not raise error
        assert not episode._is_open

    def test_operations_before_open_local(self, local_episode):
        """Test that operations before open are handled gracefully."""
        episode = local_episode(prefix="test-ws/not-opened")
        # Attempting operations before opening should handle gracefully
        # The actual behavior depends on implementation
