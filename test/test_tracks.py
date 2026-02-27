"""Comprehensive tests for track (time-series) functionality in both local and remote modes."""
import json
import pytest
from pathlib import Path


class TestBasicTracks:
    """Tests for basic track operations."""

    def test_single_track_append_local(self, local_session, temp_workspace):
        """Test appending single data points to a track."""
        with local_session(prefix="test/track-test") as session:
            for i in range(5):
                session.track("loss").append(value=1.0 / (i + 1), epoch=i)

        track_file = temp_workspace / "test" / "track-test" / "tracks" / "loss" / "data.jsonl"
        assert track_file.exists()

        with open(track_file) as f:
            data_points = [json.loads(line) for line in f]

        assert len(data_points) == 5
        assert data_points[0]["data"]["value"] == 1.0
        assert data_points[0]["data"]["epoch"] == 0

    @pytest.mark.remote
    def test_single_track_append_remote(self, remote_session):
        """Test appending data points in remote mode."""
        with remote_session(prefix="test/track-test-remote") as session:
            for i in range(10):
                session.track("loss").append(value=0.5 - i * 0.05, epoch=i)

    def test_multiple_tracks_local(self, local_session, temp_workspace):
        """Test tracking multiple different metrics."""
        with local_session(prefix="test/multi-track") as session:
            for epoch in range(5):
                session.track("train_loss").append(value=0.5 - epoch * 0.1, epoch=epoch)
                session.track("val_loss").append(value=0.6 - epoch * 0.1, epoch=epoch)
                session.track("accuracy").append(value=0.7 + epoch * 0.05, epoch=epoch)

        tracks_dir = temp_workspace / "test" / "multi-track" / "tracks"
        assert (tracks_dir / "train_loss" / "data.jsonl").exists()
        assert (tracks_dir / "val_loss" / "data.jsonl").exists()
        assert (tracks_dir / "accuracy" / "data.jsonl").exists()

    @pytest.mark.remote
    def test_multiple_tracks_remote(self, remote_session):
        """Test tracking multiple metrics in remote mode."""
        with remote_session(prefix="test/multi-track-remote") as session:
            for epoch in range(3):
                session.track("train_loss").append(value=0.4 - epoch * 0.1, epoch=epoch)
                session.track("val_loss").append(value=0.5 - epoch * 0.1, epoch=epoch)


class TestBatchAppend:
    """Tests for batch appending track data."""

    def test_batch_append_local(self, local_session, temp_workspace, sample_data):
        """Test batch appending multiple data points at once."""
        with local_session(prefix="test/batch-track") as session:
            result = session.track("loss").append_batch(sample_data["track_data"])
            assert result["count"] == 5

        track_file = temp_workspace / "test" / "batch-track" / "tracks" / "loss" / "data.jsonl"
        with open(track_file) as f:
            data_points = [json.loads(line) for line in f]

        assert len(data_points) == 5
        assert data_points[0]["data"]["value"] == 0.5
        assert data_points[4]["data"]["value"] == 0.2

    @pytest.mark.remote
    def test_batch_append_remote(self, remote_session, sample_data):
        """Test batch appending in remote mode."""
        with remote_session(prefix="test/batch-track-remote") as session:
            result = session.track("metrics").append_batch(sample_data["track_data"])
            assert result["count"] == 5

    def test_large_batch_append_local(self, local_session, temp_workspace):
        """Test appending a large batch of data."""
        batch_data = [{"value": i * 0.01, "step": i} for i in range(1000)]

        with local_session(prefix="test/large-batch") as session:
            result = session.track("metric").append_batch(batch_data)
            assert result["count"] == 1000

        track_file = temp_workspace / "test" / "large-batch" / "tracks" / "metric" / "data.jsonl"
        with open(track_file) as f:
            data_points = [json.loads(line) for line in f]

        assert len(data_points) == 1000


class TestFlexibleSchema:
    """Tests for flexible track schema with multiple fields."""

    def test_multi_field_tracking_local(self, local_session, temp_workspace):
        """Test tracks with multiple fields per data point."""
        with local_session(prefix="test/multi-field") as session:
            session.track("all_metrics").append(
                epoch=5,
                train_loss=0.3,
                val_loss=0.35,
                train_acc=0.85,
                val_acc=0.82,
                learning_rate=0.001
            )

        track_file = temp_workspace / "test" / "multi-field" / "tracks" / "all_metrics" / "data.jsonl"
        with open(track_file) as f:
            data_point = json.loads(f.readline())

        assert data_point["data"]["epoch"] == 5
        assert data_point["data"]["train_loss"] == 0.3
        assert data_point["data"]["val_loss"] == 0.35
        assert data_point["data"]["train_acc"] == 0.85

    @pytest.mark.remote
    def test_multi_field_tracking_remote(self, remote_session, sample_data):
        """Test multi-field tracking in remote mode."""
        with remote_session(prefix="test/multi-field-remote") as session:
            for data in sample_data["multi_metric_data"]:
                session.track("combined").append(**data)

    def test_varying_schemas_local(self, local_session, temp_workspace):
        """Test that schema can vary between data points."""
        with local_session(prefix="test/varying-schema") as session:
            session.track("flexible").append(field_a=1, field_b=2)
            session.track("flexible").append(field_a=3, field_c=4)
            session.track("flexible").append(field_a=5, field_b=6, field_c=7)

        track_file = temp_workspace / "test" / "varying-schema" / "tracks" / "flexible" / "data.jsonl"
        with open(track_file) as f:
            data_points = [json.loads(line) for line in f]

        assert len(data_points) == 3
        assert "field_b" in data_points[0]["data"]
        assert "field_c" in data_points[1]["data"]
        assert "field_c" in data_points[2]["data"]


class TestTrackMetadata:
    """Tests for track metadata."""

    def test_track_metadata_creation_local(self, local_session, temp_workspace):
        """Test that track metadata is created."""
        with local_session(prefix="test/track-meta") as session:
            for i in range(15):
                session.track("metric").append(value=i * 0.1, step=i)

        metadata_file = temp_workspace / "test" / "track-meta" / "tracks" / "metric" / "metadata.json"
        assert metadata_file.exists()

        with open(metadata_file) as f:
            metadata = json.load(f)

        assert metadata["name"] == "metric"
        assert metadata["totalDataPoints"] == 15

    def test_track_stats_local(self, local_session):
        """Test getting track statistics."""
        with local_session(prefix="test/track-stats") as session:
            for i in range(20):
                session.track("accuracy").append(value=0.5 + i * 0.02, step=i)

            stats = session.track("accuracy").stats()

        assert stats["name"] == "accuracy"
        assert int(stats["totalDataPoints"]) == 20

    @pytest.mark.remote
    def test_track_stats_remote(self, remote_session):
        """Test getting track stats in remote mode."""
        with remote_session(prefix="test/track-stats-remote") as session:
            for i in range(10):
                session.track("loss").append(value=1.0 / (i + 1), step=i)

            stats = session.track("loss").stats()
            assert stats["name"] == "loss"


class TestTrackRead:
    """Tests for reading track data."""

    def test_read_track_data_local(self, local_session):
        """Test reading track data."""
        with local_session(prefix="test/track-read") as session:
            # Write data
            for i in range(20):
                session.track("metric").append(value=i * 0.1, step=i)

            # Read data
            result = session.track("metric").read(start_index=0, limit=10)

        assert result["total"] >= 10
        assert len(result["data"]) == 10
        assert result["data"][0]["data"]["step"] == 0

    def test_read_with_pagination_local(self, local_session):
        """Test reading track data with pagination."""
        with local_session(prefix="test/track-page") as session:
            # Write 100 data points
            for i in range(100):
                session.track("metric").append(value=i, step=i)

            # Read first page
            page1 = session.track("metric").read(start_index=0, limit=25)
            assert len(page1["data"]) == 25

            # Read second page
            page2 = session.track("metric").read(start_index=25, limit=25)
            assert len(page2["data"]) == 25
            assert page2["data"][0]["data"]["step"] == 25

    @pytest.mark.remote
    def test_read_track_data_remote(self, remote_session):
        """Test reading track data in remote mode."""
        with remote_session(prefix="test/track-read-remote") as session:
            for i in range(15):
                session.track("metric").append(value=i * 0.05, step=i)

            result = session.track("metric").read(start_index=0, limit=5)
            assert len(result["data"]) <= 15


class TestListTracks:
    """Tests for listing all tracks."""

    def test_list_all_tracks_local(self, local_session):
        """Test listing all tracks in a session."""
        with local_session(prefix="test/track-list") as session:
            session.track("loss").append(value=0.5, step=0)
            session.track("accuracy").append(value=0.8, step=0)
            session.track("learning_rate").append(value=0.001, step=0)

            tracks = session.track("loss").list_all()

        assert len(tracks) == 3
        track_names = [t["name"] for t in tracks]
        assert "loss" in track_names
        assert "accuracy" in track_names
        assert "learning_rate" in track_names

    @pytest.mark.remote
    def test_list_all_tracks_remote(self, remote_session):
        """Test listing tracks in remote mode."""
        with remote_session(prefix="test/track-list-remote") as session:
            session.track("metric1").append(value=1.0, step=0)
            session.track("metric2").append(value=2.0, step=0)

            tracks = session.track("metric1").list_all()
            assert len(tracks) >= 2


class TestTrackIndexing:
    """Tests for track data indexing."""

    def test_track_sequential_indices_local(self, local_session, temp_workspace):
        """Test that track data points have sequential indices."""
        with local_session(prefix="test/track-index") as session:
            for i in range(10):
                session.track("metric").append(value=i * 10)

        track_file = temp_workspace / "test" / "track-index" / "tracks" / "metric" / "data.jsonl"
        with open(track_file) as f:
            data_points = [json.loads(line) for line in f]

        for i, point in enumerate(data_points):
            assert point["index"] == i

    def test_track_indices_with_batch_local(self, local_session, temp_workspace):
        """Test indices with batch append."""
        with local_session(prefix="test/batch-index") as session:
            batch1 = [{"value": i} for i in range(5)]
            batch2 = [{"value": i + 5} for i in range(5)]

            session.track("metric").append_batch(batch1)
            session.track("metric").append_batch(batch2)

        track_file = temp_workspace / "test" / "batch-index" / "tracks" / "metric" / "data.jsonl"
        with open(track_file) as f:
            data_points = [json.loads(line) for line in f]

        assert len(data_points) == 10
        for i, point in enumerate(data_points):
            assert point["index"] == i


class TestTrackEdgeCases:
    """Tests for edge cases in track operations."""

    def test_empty_track_local(self, local_session, temp_workspace):
        """Test session with no tracks."""
        with local_session(prefix="test/no-tracks") as session:
            session.log("No tracks created")

        tracks_dir = temp_workspace / "test" / "no-tracks" / "tracks"
        assert tracks_dir.exists()
        subdirs = [d for d in tracks_dir.iterdir() if d.is_dir()]
        assert len(subdirs) == 0

    def test_track_with_null_values_local(self, local_session, temp_workspace):
        """Test tracking data with null values."""
        with local_session(prefix="test/null-track") as session:
            session.track("metric").append(value=None, step=0, status="pending")
            session.track("metric").append(value=0.5, step=1, status="complete")

        track_file = temp_workspace / "test" / "null-track" / "tracks" / "metric" / "data.jsonl"
        with open(track_file) as f:
            data_points = [json.loads(line) for line in f]

        assert data_points[0]["data"]["value"] is None
        assert data_points[1]["data"]["value"] == 0.5

    def test_track_with_special_characters_local(self, local_session, temp_workspace):
        """Test track names with special characters."""
        with local_session(prefix="test/special-track") as session:
            session.track("metric_1").append(value=1.0)
            session.track("metric-2").append(value=2.0)
            session.track("metric.3").append(value=3.0)

        tracks_dir = temp_workspace / "test" / "special-track" / "tracks"
        # Check that tracks were created (names may be sanitized)
        assert tracks_dir.exists()

    def test_very_frequent_tracking_local(self, local_session, temp_workspace):
        """Test rapid, frequent tracking."""
        with local_session(prefix="test/frequent-track") as session:
            for i in range(1000):
                session.track("metric").append(value=i * 0.001, step=i)

        track_file = temp_workspace / "test" / "frequent-track" / "tracks" / "metric" / "data.jsonl"
        with open(track_file) as f:
            data_points = [json.loads(line) for line in f]

        assert len(data_points) == 1000

    @pytest.mark.remote
    def test_frequent_tracking_remote(self, remote_session):
        """Test rapid tracking in remote mode."""
        with remote_session(prefix="test/frequent-track-remote") as session:
            for i in range(100):
                session.track("metric").append(value=i * 0.01, step=i)

    def test_track_with_large_values_local(self, local_session, temp_workspace):
        """Test tracking with very large numeric values."""
        with local_session(prefix="test/large-values") as session:
            session.track("metric").append(
                huge_int=999999999999999,
                huge_float=1.23e100,
                tiny_float=1.23e-100
            )

        track_file = temp_workspace / "test" / "large-values" / "tracks" / "metric" / "data.jsonl"
        with open(track_file) as f:
            data_point = json.loads(f.readline())

        assert data_point["data"]["huge_int"] == 999999999999999

    def test_track_with_nested_data_local(self, local_session, temp_workspace):
        """Test tracking with nested data structures."""
        with local_session(prefix="test/nested-track") as session:
            session.track("metric").append(
                epoch=1,
                metrics={
                    "train": {"loss": 0.5, "acc": 0.8},
                    "val": {"loss": 0.6, "acc": 0.75}
                }
            )

        track_file = temp_workspace / "test" / "nested-track" / "tracks" / "metric" / "data.jsonl"
        with open(track_file) as f:
            data_point = json.loads(f.readline())

        assert data_point["data"]["epoch"] == 1
        assert isinstance(data_point["data"]["metrics"], dict)

    def test_track_name_collision_local(self, local_session, temp_workspace):
        """Test multiple appends to same track name."""
        with local_session(prefix="test/collision") as session:
            session.track("loss").append(value=1.0, epoch=0)
            session.track("loss").append(value=0.9, epoch=1)
            session.track("loss").append(value=0.8, epoch=2)

        track_file = temp_workspace / "test" / "collision" / "tracks" / "loss" / "data.jsonl"
        with open(track_file) as f:
            data_points = [json.loads(line) for line in f]

        assert len(data_points) == 3
        assert data_points[0]["data"]["value"] == 1.0
        assert data_points[2]["data"]["value"] == 0.8
