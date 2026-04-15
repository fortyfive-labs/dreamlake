"""Tests for basic episode operations."""
import json
from pathlib import Path


def test_episode_creation_with_context_manager(local_episode, temp_workspace):
    """Test basic episode creation using context manager (ML-Dash API)."""
    with local_episode(prefix="tutorials/hello-dreamlake") as episode:
        episode.log("Hello from Dreamlake!", level="info")
        episode.params.set(message="Hello World")

    # Verify episode directory was created
    episode_dir = temp_workspace / "tutorials" / "hello-dreamlake"
    assert episode_dir.exists()
    assert (episode_dir / "episode.json").exists()


def test_episode_with_metadata(local_episode, temp_workspace):
    """Test episode creation with readme and tags (ML-Dash API)."""
    with local_episode(
        prefix="computer-vision/mnist-baseline",
        readme="Baseline CNN for MNIST classification",
        tags=["mnist", "cnn", "baseline"],
    ) as episode:
        episode.log("Episode created with metadata")

    # Verify metadata was saved
    episode_dir = temp_workspace / "computer-vision" / "mnist-baseline"
    episode_file = episode_dir / "episode.json"

    assert episode_file.exists()
    with open(episode_file) as f:
        metadata = json.load(f)
        assert metadata["name"] == "mnist-baseline"
        assert metadata["workspace"] == "computer-vision"
        # Note: 'description' field may still exist in storage for now (mapped from readme)
        assert metadata.get("description") == "Baseline CNN for MNIST classification" or metadata.get("readme") == "Baseline CNN for MNIST classification"
        assert "mnist" in metadata["tags"]
        assert "cnn" in metadata["tags"]


def test_episode_manual_open_close(local_episode, temp_workspace):
    """Test manual episode lifecycle management (ML-Dash API)."""
    episode = local_episode(prefix="test/manual-episode")

    # The episode is not initially open
    assert not episode._is_open

    # Open episode
    episode.open()
    assert episode._is_open

    # Do work
    episode.log("Working...")

    # Close episode
    episode.close()
    assert not episode._is_open

    # Verify data was saved
    episode_dir = temp_workspace / "test" / "manual-episode"
    assert episode_dir.exists()


def test_episode_auto_close_on_context_exit(local_episode):
    """Test that episode is automatically closed when exiting context manager (ML-Dash API)."""
    with local_episode(prefix="test/auto-close") as episode:
        assert episode._is_open
        episode.log("Working...")

    # After exiting context, the episode should be closed
    assert not episode._is_open


def test_multiple_episodes_same_workspace(local_episode, temp_workspace):
    """Test creating multiple episodes in the same workspace (ML-Dash API)."""
    # Create first episode
    with local_episode(prefix="shared/episode-1") as episode:
        episode.log("Episode 1")

    # Create second episode
    with local_episode(prefix="shared/episode-2") as episode:
        episode.log("Episode 2")

    # Verify both episodes exist
    workspace_dir = temp_workspace / "shared"
    assert (workspace_dir / "episode-1").exists()
    assert (workspace_dir / "episode-2").exists()


def test_episode_name_and_workspace_properties(local_episode):
    """Test that episode properties are accessible (ML-Dash API)."""
    with local_episode(prefix="my-workspace/my-episode") as episode:
        assert episode.name == "my-episode"
        assert episode.workspace == "my-workspace"


def test_episode_error_handling(local_episode, temp_workspace):
    """Test that episode handles errors gracefully and still saves data (ML-Dash API)."""
    try:
        with local_episode(prefix="test/error-test") as episode:
            episode.log("Starting work")
            episode.params.set(param="value")
            raise ValueError("Simulated error")
    except ValueError:
        pass

    # Episode should still be closed and data saved
    episode_dir = temp_workspace / "test" / "error-test"
    assert episode_dir.exists()
    assert (episode_dir / "logs" / "logs.jsonl").exists()
    assert (episode_dir / "parameters.json").exists()
