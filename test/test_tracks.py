"""Comprehensive tests for track (time-series) functionality in both local and remote modes."""
import json
import pytest
from pathlib import Path
from conftest import read_msgpack_track_file


class TestBasicTracks:
    """Tests for basic track operations."""

    def test_single_track_append_local(self, local_session, temp_workspace):
        """Test appending single data points to a track."""
        with local_session(prefix="test/track-test") as session:
            for i in range(5):
                session.track("loss").append(value=1.0 / (i + 1), epoch=i)

        track_file = temp_workspace / "test" / "track-test" / "tracks" / "loss" / "data.msgpack"
        assert track_file.exists()

        import msgpack
        with open(track_file, "rb") as f:
            unpacker = msgpack.Unpacker(f, raw=False)
            data_points = []
            for obj in unpacker:
                if isinstance(obj, dict):
                    # Check if columnar format (all values are lists of same length)
                    if all(isinstance(v, list) for v in obj.values()) and obj:
                        lengths = [len(v) for v in obj.values()]
                        if len(set(lengths)) == 1 and lengths[0] > 0:
                            # Columnar format - expand to rows
                            num_rows = lengths[0]
                            for i in range(num_rows):
                                row = {key: values[i] for key, values in obj.items()}
                                data_points.append({"data": row})
                        else:
                            # Single row
                            data_points.append({"data": obj})
                    else:
                        # Single row
                        data_points.append({"data": obj})

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
        assert (tracks_dir / "train_loss" / "data.msgpack").exists()
        assert (tracks_dir / "val_loss" / "data.msgpack").exists()
        assert (tracks_dir / "accuracy" / "data.msgpack").exists()

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

        track_file = temp_workspace / "test" / "batch-track" / "tracks" / "loss" / "data.msgpack"
        import msgpack
        with open(track_file, "rb") as f:
            unpacker = msgpack.Unpacker(f, raw=False)
            columnar_batch = next(unpacker)  # Should be one columnar batch

        # Expand columnar to rows
        num_rows = len(columnar_batch["value"])
        data_points = []
        for i in range(num_rows):
            row = {key: values[i] for key, values in columnar_batch.items()}
            data_points.append({"data": row})

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

        track_file = temp_workspace / "test" / "large-batch" / "tracks" / "metric" / "data.msgpack"
        data_points = read_msgpack_track_file(track_file)

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

        track_file = temp_workspace / "test" / "multi-field" / "tracks" / "all_metrics" / "data.msgpack"
        data_points = read_msgpack_track_file(track_file)

        assert data_points[0]["data"]["epoch"] == 5
        assert data_points[0]["data"]["train_loss"] == 0.3
        assert data_points[0]["data"]["val_loss"] == 0.35
        assert data_points[0]["data"]["train_acc"] == 0.85

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

        track_file = temp_workspace / "test" / "varying-schema" / "tracks" / "flexible" / "data.msgpack"
        data_points = read_msgpack_track_file(track_file)

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

        track_file = temp_workspace / "test" / "track-index" / "tracks" / "metric" / "data.msgpack"
        data_points = read_msgpack_track_file(track_file)

        for i, point in enumerate(data_points):
            assert point["index"] == i

    def test_track_indices_with_batch_local(self, local_session, temp_workspace):
        """Test indices with batch append."""
        with local_session(prefix="test/batch-index") as session:
            batch1 = [{"value": i} for i in range(5)]
            batch2 = [{"value": i + 5} for i in range(5)]

            session.track("metric").append_batch(batch1)
            session.track("metric").append_batch(batch2)

        track_file = temp_workspace / "test" / "batch-index" / "tracks" / "metric" / "data.msgpack"
        data_points = read_msgpack_track_file(track_file)

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

        track_file = temp_workspace / "test" / "null-track" / "tracks" / "metric" / "data.msgpack"
        data_points = read_msgpack_track_file(track_file)

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

        track_file = temp_workspace / "test" / "frequent-track" / "tracks" / "metric" / "data.msgpack"
        data_points = read_msgpack_track_file(track_file)

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

        track_file = temp_workspace / "test" / "large-values" / "tracks" / "metric" / "data.msgpack"
        data_points = read_msgpack_track_file(track_file)

        assert data_points[0]["data"]["huge_int"] == 999999999999999

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

        track_file = temp_workspace / "test" / "nested-track" / "tracks" / "metric" / "data.msgpack"
        data_points = read_msgpack_track_file(track_file)

        assert data_points[0]["data"]["epoch"] == 1
        assert isinstance(data_points[0]["data"]["metrics"], dict)

    def test_track_name_collision_local(self, local_session, temp_workspace):
        """Test multiple appends to same track name."""
        with local_session(prefix="test/collision") as session:
            session.track("loss").append(value=1.0, epoch=0)
            session.track("loss").append(value=0.9, epoch=1)
            session.track("loss").append(value=0.8, epoch=2)

        track_file = temp_workspace / "test" / "collision" / "tracks" / "loss" / "data.msgpack"
        data_points = read_msgpack_track_file(track_file)

        assert len(data_points) == 3
        assert data_points[0]["data"]["value"] == 1.0
        assert data_points[2]["data"]["value"] == 0.8


class TestTrackTimeQueries:
    """Tests for time-based track queries (MCAP-like API)."""

    def test_read_by_time_range_local(self, local_session):
        """Test reading track data by time range."""
        import time

        with local_session(prefix="test/track-time-range") as session:
            # Write data with explicit timestamps
            base_time = time.time()
            for i in range(10):
                session.track("robot/pose").append(
                    position=[i * 0.1, i * 0.2, i * 0.3],
                    _ts=base_time + i * 0.1
                )

            # Read data in time range
            result = session.track("robot/pose").read_by_time(
                start_time=base_time + 0.2,  # From 3rd point
                end_time=base_time + 0.7,    # To 7th point (exclusive)
                limit=100
            )

        assert result["total"] == 5  # Points at 0.2, 0.3, 0.4, 0.5, 0.6
        assert result["startTime"] == base_time + 0.2
        assert result["endTime"] == base_time + 0.7

    def test_read_by_time_reverse_local(self, local_session):
        """Test reading track data in reverse order."""
        import time

        with local_session(prefix="test/track-time-reverse") as session:
            base_time = time.time()
            for i in range(10):
                session.track("metric").append(value=i, _ts=base_time + i)

            # Read in reverse
            result = session.track("metric").read_by_time(reverse=True, limit=3)

        assert result["total"] == 3
        # Check that newest comes first
        assert result["data"][0]["data"]["value"] == 9
        assert result["data"][1]["data"]["value"] == 8
        assert result["data"][2]["data"]["value"] == 7

    def test_read_by_time_no_range_local(self, local_session):
        """Test reading all data without time range."""
        import time

        with local_session(prefix="test/track-time-all") as session:
            base_time = time.time()
            for i in range(5):
                session.track("metric").append(value=i * 10, _ts=base_time + i)

            # Read all data
            result = session.track("metric").read_by_time(limit=100)

        assert result["total"] == 5

    def test_read_by_time_with_limit_local(self, local_session):
        """Test reading with limit applied."""
        import time

        with local_session(prefix="test/track-time-limit") as session:
            base_time = time.time()
            for i in range(100):
                session.track("metric").append(value=i, _ts=base_time + i * 0.01)

            # Read with limit
            result = session.track("metric").read_by_time(
                start_time=base_time,
                limit=10
            )

        assert result["total"] == 10
        assert result["hasMore"] is True

    def test_read_by_time_start_only_local(self, local_session):
        """Test reading from start_time to end."""
        import time

        with local_session(prefix="test/track-time-start") as session:
            base_time = time.time()
            for i in range(10):
                session.track("metric").append(value=i, _ts=base_time + i)

            # Read from time 5 to end
            result = session.track("metric").read_by_time(
                start_time=base_time + 5,
                limit=100
            )

        assert result["total"] == 5  # Points 5, 6, 7, 8, 9

    def test_read_by_time_end_only_local(self, local_session):
        """Test reading from beginning to end_time."""
        import time

        with local_session(prefix="test/track-time-end") as session:
            base_time = time.time()
            for i in range(10):
                session.track("metric").append(value=i, _ts=base_time + i)

            # Read from beginning to time 5 (exclusive)
            result = session.track("metric").read_by_time(
                end_time=base_time + 5,
                limit=100
            )

        assert result["total"] == 5  # Points 0, 1, 2, 3, 4

    @pytest.mark.remote
    def test_read_by_time_remote(self, remote_session):
        """Test time-based queries in remote mode."""
        import time

        with remote_session(prefix="test/track-time-remote") as session:
            base_time = time.time()
            for i in range(20):
                session.track("robot/pose").append(
                    position=[i, i * 2, i * 3],
                    _ts=base_time + i * 0.1
                )

            # Query time range
            result = session.track("robot/pose").read_by_time(
                start_time=base_time + 0.5,
                end_time=base_time + 1.5,
                limit=100
            )

            # Should get points from 0.5 to 1.4 (10 points)
            assert result["total"] >= 5

    def test_timestamp_inheritance_across_tracks_local(self, local_session):
        """Test _ts=-1 for timestamp inheritance across tracks."""
        with local_session(prefix="test/timestamp-inherit") as session:
            # First append - auto-generates timestamp
            session.track("robot/pose").append(position=[1.0, 2.0, 3.0])

            # Following appends - inherit same timestamp using _ts=-1
            session.track("camera/left").append(width=640, height=480, _ts=-1)
            session.track("robot/velocity").append(linear=[0.1, 0.2, 0.3], _ts=-1)

            # Read back from all tracks
            pose_data = session.track("robot/pose").read_by_time(limit=10)
            image_data = session.track("camera/left").read_by_time(limit=10)
            velocity_data = session.track("robot/velocity").read_by_time(limit=10)

        # All three tracks should have same timestamp
        pose_ts = pose_data["data"][0]["data"]["_ts"]
        image_ts = image_data["data"][0]["data"]["_ts"]
        velocity_ts = velocity_data["data"][0]["data"]["_ts"]

        assert pose_ts == image_ts == velocity_ts
