"""
Track API - Time-series data tracking for ML experiments.

Tracks are used for storing continuous data series like training metrics,
validation losses, system measurements, etc.
"""

from typing import Dict, Any, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .session import Session


class TrackBuilder:
    """
    Builder for track operations.

    Provides fluent API for appending, reading, and querying track data.

    Usage:
        # Append single data point
        session.track(name="train_loss").append(value=0.5, step=100)

        # Append batch
        session.track(name="train_loss").append_batch([
            {"value": 0.5, "step": 100},
            {"value": 0.45, "step": 101}
        ])

        # Read data
        data = session.track(name="train_loss").read(start_index=0, limit=100)

        # Get statistics
        stats = session.track(name="train_loss").stats()
    """

    def __init__(self, session: 'Session', name: str, description: Optional[str] = None,
                 tags: Optional[List[str]] = None, metadata: Optional[Dict[str, Any]] = None):
        """
        Initialize TrackBuilder.

        Args:
            session: Parent Session instance
            name: Track name (unique within session)
            description: Optional track description
            tags: Optional tags for categorization
            metadata: Optional structured metadata (units, type, etc.)
        """
        self._session = session
        self._name = name
        self._description = description
        self._tags = tags
        self._metadata = metadata

    def append(self, **kwargs) -> 'TrackBuilder':
        """
        Append a single data point to the track.

        The data point can have any structure - common patterns:
        - {value: 0.5, step: 100}
        - {loss: 0.3, accuracy: 0.92, epoch: 5}
        - {timestamp: "...", temperature: 25.5, humidity: 60}

        Args:
            **kwargs: Data point fields (flexible schema)

        Returns:
            Dict with trackId, index, bufferedDataPoints, chunkSize

        Example:
            result = session.track(name="train_loss").append(value=0.5, step=100, epoch=1)
            print(f"Appended at index {result['index']}")
        """
        result = self._session._append_to_track(
            name=self._name,
            data=kwargs,
            description=self._description,
            tags=self._tags,
            metadata=self._metadata
        )
        return result

    def append_batch(self, data_points: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Append multiple data points in batch (more efficient than multiple append calls).

        Args:
            data_points: List of data point dicts

        Returns:
            Dict with trackId, startIndex, endIndex, count, bufferedDataPoints, chunkSize

        Example:
            result = session.track(name="metrics").append_batch([
                {"loss": 0.5, "acc": 0.8, "step": 1},
                {"loss": 0.4, "acc": 0.85, "step": 2},
                {"loss": 0.3, "acc": 0.9, "step": 3}
            ])
            print(f"Appended {result['count']} points")
        """
        if not data_points:
            raise ValueError("data_points cannot be empty")

        result = self._session._append_batch_to_track(
            name=self._name,
            data_points=data_points,
            description=self._description,
            tags=self._tags,
            metadata=self._metadata
        )
        return result

    def read(self, start_index: int = 0, limit: int = 1000) -> Dict[str, Any]:
        """
        Read data points from the track by index range.

        Args:
            start_index: Starting index (inclusive, default 0)
            limit: Maximum number of points to read (default 1000, max 10000)

        Returns:
            Dict with keys:
            - data: List of {index: str, data: dict, createdAt: str}
            - startIndex: Starting index
            - endIndex: Ending index
            - total: Number of points returned
            - hasMore: Whether more data exists beyond this range

        Example:
            result = session.track(name="train_loss").read(start_index=0, limit=100)
            for point in result['data']:
                print(f"Index {point['index']}: {point['data']}")
        """
        return self._session._read_track_data(
            name=self._name,
            start_index=start_index,
            limit=limit
        )

    def stats(self) -> Dict[str, Any]:
        """
        Get track statistics and metadata.

        Returns:
            Dict with track info:
            - trackId: Unique track ID
            - name: Track name
            - description: Track description (if set)
            - tags: Tags list
            - metadata: User metadata
            - totalDataPoints: Total points (buffered + chunked)
            - bufferedDataPoints: Points in MongoDB (hot storage)
            - chunkedDataPoints: Points in S3 (cold storage)
            - totalChunks: Number of chunks in S3
            - chunkSize: Chunking threshold
            - firstDataAt: Timestamp of first point (if data has timestamp)
            - lastDataAt: Timestamp of last point (if data has timestamp)
            - createdAt: Track creation time
            - updatedAt: Last update time

        Example:
            stats = session.track(name="train_loss").stats()
            print(f"Total points: {stats['totalDataPoints']}")
            print(f"Buffered: {stats['bufferedDataPoints']}, Chunked: {stats['chunkedDataPoints']}")
        """
        return self._session._get_track_stats(name=self._name)

    def list_all(self) -> List[Dict[str, Any]]:
        """
        List all tracks in the session.

        Returns:
            List of track summaries with keys:
            - trackId: Unique track ID
            - name: Track name
            - description: Track description
            - tags: Tags list
            - totalDataPoints: Total data points
            - createdAt: Creation timestamp

        Example:
            tracks = session.track().list_all()
            for track in tracks:
                print(f"{track['name']}: {track['totalDataPoints']} points")
        """
        return self._session._list_tracks()
