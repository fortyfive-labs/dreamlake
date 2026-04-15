"""Test new timestamp-based tracking features."""

import tempfile
from pathlib import Path
import time

from dreamlake import Episode


def test_auto_generated_timestamps():
    """Test that timestamps are auto-generated when not provided."""
    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = Path(tmpdir) / ".dreamlake"

        with Episode(
            prefix="test/auto-ts-test",
            root=str(local_path)
        ) as episode:
            # Append without _ts (should auto-generate)
            episode.track("metric").append(value=0.5, step=1)
            episode.track("metric").append(value=0.6, step=2)

            # Read back
            data = episode.track("metric").read(start_index=0, limit=10)

        # Verify _ts was added
        assert len(data["data"]) == 2
        assert "_ts" in data["data"][0]["data"]
        assert "_ts" in data["data"][1]["data"]

        # Verify timestamps are different (unique)
        ts1 = data["data"][0]["data"]["_ts"]
        ts2 = data["data"][1]["data"]["_ts"]
        assert ts1 != ts2, "Auto-generated timestamps should be unique"


def test_explicit_timestamps():
    """Test using explicit timestamps."""
    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = Path(tmpdir) / ".dreamlake"

        with Episode(
            prefix="test/explicit-ts-test",
            root=str(local_path)
        ) as episode:
            # Append with explicit timestamps
            episode.track("robot/position").append(q=[0.1, 0.2], _ts=1.0)
            episode.track("robot/position").append(q=[0.2, 0.3], _ts=2.0)

            # Read back
            data = episode.track("robot/position").read(start_index=0, limit=10)

        # Verify explicit timestamps were used
        assert data["data"][0]["data"]["_ts"] == 1.0
        assert data["data"][1]["data"]["_ts"] == 2.0


def test_timestamp_inheritance():
    """Test timestamp inheritance with _ts=-1 (same track)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = Path(tmpdir) / ".dreamlake"

        with Episode(
            prefix="test/inherit-ts-test",
            root=str(local_path)
        ) as episode:
            # First append with explicit timestamp
            episode.track("robot/state").append(q=[0.1, 0.2], _ts=1.5)

            # Second append inherits timestamp (will merge with first)
            episode.track("robot/state").append(v=[0.01, 0.02], _ts=-1)

            # Read back
            data = episode.track("robot/state").read(start_index=0, limit=10)

        # Should be merged into single point with both fields
        assert len(data["data"]) == 1, "Data with inherited _ts should merge"
        merged = data["data"][0]["data"]
        assert merged["_ts"] == 1.5
        assert merged["q"] == [0.1, 0.2]
        assert merged["v"] == [0.01, 0.02]


def test_timestamp_inheritance_across_tracks():
    """Test timestamp inheritance with _ts=-1 across different tracks."""
    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = Path(tmpdir) / ".dreamlake"

        with Episode(
            prefix="test/cross-track-ts-test",
            root=str(local_path)
        ) as episode:
            # First append - auto-generates timestamp
            episode.track("robot/pose").append(position=[1.0, 2.0, 3.0])

            # Second append on different track - inherits same timestamp
            episode.track("camera/left/image").append(width=640, height=480, _ts=-1)

            # Third append on another track - also inherits same timestamp
            episode.track("robot/velocity").append(linear=[0.1, 0.2, 0.3], _ts=-1)

            # Read back from all tracks
            pose_data = episode.track("robot/pose").read(start_index=0, limit=10)
            image_data = episode.track("camera/left/image").read(start_index=0, limit=10)
            velocity_data = episode.track("robot/velocity").read(start_index=0, limit=10)

        # All three tracks should have same timestamp
        pose_ts = pose_data["data"][0]["data"]["_ts"]
        image_ts = image_data["data"][0]["data"]["_ts"]
        velocity_ts = velocity_data["data"][0]["data"]["_ts"]

        assert pose_ts == image_ts == velocity_ts, \
            "All tracks with _ts=-1 should share the same timestamp"

        # Verify data is correct
        assert pose_data["data"][0]["data"]["position"] == [1.0, 2.0, 3.0]
        assert image_data["data"][0]["data"]["width"] == 640
        assert velocity_data["data"][0]["data"]["linear"] == [0.1, 0.2, 0.3]


def test_timestamp_merging():
    """Test that data points with same _ts are merged."""
    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = Path(tmpdir) / ".dreamlake"

        with Episode(
            prefix="test/merge-ts-test",
            root=str(local_path)
        ) as episode:
            # Append multiple fields with same timestamp
            episode.track("robot/state").append(q=[0.1, 0.2], _ts=1.0)
            episode.track("robot/state").append(v=[0.01, 0.02], _ts=1.0)
            episode.track("robot/state").append(e=[0.5, 0.6, 0.7], _ts=1.0)

            # Read back
            data = episode.track("robot/state").read(start_index=0, limit=10)

        # Should be merged into single data point
        assert len(data["data"]) == 1, "Data points with same _ts should merge"

        merged_point = data["data"][0]["data"]
        assert merged_point["_ts"] == 1.0
        assert merged_point["q"] == [0.1, 0.2]
        assert merged_point["v"] == [0.01, 0.02]
        assert merged_point["e"] == [0.5, 0.6, 0.7]


def test_timestamp_merging_with_inheritance():
    """Test merging when using _ts=-1 to inherit timestamp."""
    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = Path(tmpdir) / ".dreamlake"

        with Episode(
            prefix="test/merge-inherit-test",
            root=str(local_path)
        ) as episode:
            # First point with explicit timestamp
            episode.track("robot/state").append(q=[0.1, 0.2], _ts=2.0)

            # Subsequent points inherit and merge
            episode.track("robot/state").append(v=[0.01, 0.02], _ts=-1)
            episode.track("robot/state").append(e=[0.5, 0.6, 0.7], _ts=-1)

            # Different timestamp - new point
            episode.track("robot/state").append(q=[0.3, 0.4], _ts=3.0)

            # Read back
            data = episode.track("robot/state").read(start_index=0, limit=10)

        # Should have 2 data points
        assert len(data["data"]) == 2

        # First point has all merged fields
        point1 = data["data"][0]["data"]
        assert point1["_ts"] == 2.0
        assert point1["q"] == [0.1, 0.2]
        assert point1["v"] == [0.01, 0.02]
        assert point1["e"] == [0.5, 0.6, 0.7]

        # Second point is separate
        point2 = data["data"][1]["data"]
        assert point2["_ts"] == 3.0
        assert point2["q"] == [0.3, 0.4]


def test_flush_and_chaining():
    """Test flush() method and method chaining."""
    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = Path(tmpdir) / ".dreamlake"

        with Episode(
            prefix="test/flush-test",
            root=str(local_path)
        ) as episode:
            # Method chaining
            episode.track("loss").append(value=0.5, epoch=1, _ts=1.0).append(value=0.4, epoch=2, _ts=2.0)

            # Explicit flush
            result = episode.track("loss").flush()

            # Verify flush result
            assert result is not None
            assert "count" in result or "trackId" in result

            # Global flush
            episode.tracks.flush()


def test_hierarchical_track_names():
    """Test hierarchical track naming like 'robot/position/left-camera'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = Path(tmpdir) / ".dreamlake"

        with Episode(
            prefix="test/hierarchical-test",
            root=str(local_path)
        ) as episode:
            # Use hierarchical names
            episode.track("robot/position/left-camera").append(x=1.0, y=2.0, _ts=1.0)
            episode.track("robot/position/right-camera").append(x=1.1, y=2.1, _ts=1.0)
            episode.track("robot/velocity").append(vx=0.5, vy=0.6, _ts=1.0)

            # List all tracks
            tracks = episode.tracks.list()

        # Verify all tracks exist
        track_names = [t["name"] for t in tracks]
        assert "robot/position/left-camera" in track_names
        assert "robot/position/right-camera" in track_names
        assert "robot/velocity" in track_names
