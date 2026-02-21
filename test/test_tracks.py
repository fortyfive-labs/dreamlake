"""Tests for time-series metrics tracking."""
import json
from pathlib import Path


def test_single_value_append(local_session, temp_workspace):
    """Test appending single data points to a track."""
    with local_session(name="track-single", workspace="test") as session:
        for epoch in range(5):
            session.track("loss").append(value=1.0 / (epoch + 1), epoch=epoch)

    # Verify track data was saved
    track_data = temp_workspace / "test" / "track-single" / "tracks" / "loss" / "data.jsonl"
    assert track_data.exists()

    # Verify data points
    with open(track_data) as f:
        data_points = [json.loads(line) for line in f]

    assert len(data_points) == 5
    assert data_points[0]["data"]["value"] == 1.0
    assert data_points[0]["data"]["epoch"] == 0


def test_multiple_tracks(local_session, temp_workspace):
    """Test tracking multiple different metrics."""
    with local_session(name="track-multi", workspace="test") as session:
        for epoch in range(3):
            session.track("train_loss").append(value=0.5 - epoch * 0.1, epoch=epoch)
            session.track("val_loss").append(value=0.6 - epoch * 0.1, epoch=epoch)
            session.track("accuracy").append(value=0.7 + epoch * 0.1, epoch=epoch)

    # Verify all tracks exist
    tracks_dir = temp_workspace / "test" / "track-multi" / "tracks"
    assert (tracks_dir / "train_loss" / "data.jsonl").exists()
    assert (tracks_dir / "val_loss" / "data.jsonl").exists()
    assert (tracks_dir / "accuracy" / "data.jsonl").exists()


def test_batch_append(local_session, temp_workspace):
    """Test batch appending multiple data points at once."""
    with local_session(name="track-batch", workspace="test") as session:
        batch_data = [
            {"value": 0.45, "step": 100, "batch": 1},
            {"value": 0.42, "step": 200, "batch": 2},
            {"value": 0.40, "step": 300, "batch": 3},
            {"value": 0.38, "step": 400, "batch": 4},
        ]
        result = session.track("step_loss").append_batch(batch_data)

        assert result["count"] == 4

    # Verify all data points were saved
    track_data = temp_workspace / "test" / "track-batch" / "tracks" / "step_loss" / "data.jsonl"
    with open(track_data) as f:
        data_points = [json.loads(line) for line in f]

    assert len(data_points) == 4
    assert data_points[0]["data"]["value"] == 0.45
    assert data_points[0]["data"]["step"] == 100


def test_flexible_schema(local_session, temp_workspace):
    """Test that tracks support flexible schema with multiple fields."""
    with local_session(name="track-schema", workspace="test") as session:
        # Track multiple metrics in one data point
        session.track("all_metrics").append(
            epoch=5,
            train_loss=0.3,
            val_loss=0.35,
            train_acc=0.85,
            val_acc=0.82,
            learning_rate=0.001,
        )

    # Verify all fields were saved
    track_data = temp_workspace / "test" / "track-schema" / "tracks" / "all_metrics" / "data.jsonl"
    with open(track_data) as f:
        data_point = json.loads(f.readline())

    assert data_point["data"]["epoch"] == 5
    assert data_point["data"]["train_loss"] == 0.3
    assert data_point["data"]["val_loss"] == 0.35
    assert data_point["data"]["train_acc"] == 0.85
    assert data_point["data"]["val_acc"] == 0.82
    assert data_point["data"]["learning_rate"] == 0.001


def test_track_metadata(local_session, temp_workspace):
    """Test that track metadata is created."""
    with local_session(name="track-meta", workspace="test") as session:
        for i in range(10):
            session.track("loss").append(value=0.5 - i * 0.05, step=i)

    # Verify metadata file exists
    metadata_file = temp_workspace / "test" / "track-meta" / "tracks" / "loss" / "metadata.json"
    assert metadata_file.exists()

    # Verify metadata content
    with open(metadata_file) as f:
        metadata = json.load(f)

    assert metadata["name"] == "loss"
    assert metadata["totalDataPoints"] == 10


def test_read_track_data(local_session, temp_workspace):
    """Test reading track data."""
    with local_session(name="track-read", workspace="test") as session:
        # Write some data
        for i in range(10):
            session.track("loss").append(value=1.0 / (i + 1), step=i)

        # Read track data
        result = session.track("loss").read(start_index=0, limit=5)

        assert result["total"] >= 5
        assert len(result["data"]) == 5
        assert result["data"][0]["data"]["step"] == 0


def test_track_stats(local_session, temp_workspace):
    """Test getting track statistics."""
    with local_session(name="track-stats", workspace="test") as session:
        # Write some data
        for i in range(15):
            session.track("accuracy").append(value=0.5 + i * 0.03, step=i)

        # Get stats
        stats = session.track("accuracy").stats()

        assert stats["name"] == "accuracy"
        assert int(stats["totalDataPoints"]) == 15


def test_list_all_tracks(local_session, temp_workspace):
    """Test listing all tracks in a session."""
    with local_session(name="track-list", workspace="test") as session:
        # Create multiple tracks
        session.track("loss").append(value=0.5, step=0)
        session.track("accuracy").append(value=0.8, step=0)
        session.track("lr").append(value=0.001, step=0)

        # List all tracks
        tracks = session.track("loss").list_all()

        assert len(tracks) == 3
        track_names = [t["name"] for t in tracks]
        assert "loss" in track_names
        assert "accuracy" in track_names
        assert "lr" in track_names


def test_track_index_sequence(local_session, temp_workspace):
    """Test that track data points have sequential indices."""
    with local_session(name="track-index", workspace="test") as session:
        for i in range(5):
            session.track("metric").append(value=i * 10)

    # Verify indices are sequential
    track_data = temp_workspace / "test" / "track-index" / "tracks" / "metric" / "data.jsonl"
    with open(track_data) as f:
        data_points = [json.loads(line) for line in f]

    for i, point in enumerate(data_points):
        assert point["index"] == i


def test_empty_track(local_session, temp_workspace):
    """Test session with no tracks."""
    with local_session(name="no-tracks", workspace="test") as session:
        session.log("No tracks created")

    # Tracks directory should still exist but be empty
    tracks_dir = temp_workspace / "test" / "no-tracks" / "tracks"
    assert tracks_dir.exists()
    # Should have no subdirectories
    subdirs = [d for d in tracks_dir.iterdir() if d.is_dir()]
    assert len(subdirs) == 0
