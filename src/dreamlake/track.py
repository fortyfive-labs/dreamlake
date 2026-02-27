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

    def append(self, _ts: Optional[float] = None, **kwargs) -> 'TrackBuilder':
        """
        Append a single data point to the track (buffered, call flush() to persist).

        The data point can have any structure. The _ts field is used for timestamp-based merging.

        Timestamp handling:
        - _ts=<number>: Use that timestamp (seconds since epoch)
        - _ts=-1: Inherit timestamp from previous append on this track
        - _ts not provided: Auto-generate using time.time()

        Args:
            _ts: Timestamp in seconds since epoch (optional, auto-generated if not provided)
            **kwargs: Data point fields (flexible schema)

        Returns:
            self for method chaining

        Examples:
            # Auto-generated timestamp
            session.track("loss").append(value=0.5, epoch=1)

            # Explicit timestamp
            session.track("robot/position").append(q=[0.1, 0.2], _ts=1.234)

            # Inherit timestamp (merge with previous point)
            session.track("robot/state").append(q=[0.1, 0.2], _ts=1.0)
            session.track("robot/state").append(v=[0.01, 0.02], _ts=-1)  # Uses _ts=1.0
            # After flush: {_ts: 1.0, q: [0.1, 0.2], v: [0.01, 0.02]}
        """
        # Prepare data dict
        data = kwargs.copy()
        if _ts is not None:
            data['_ts'] = _ts

        # Append to buffer (returns TrackBuilder)
        self._session._append_to_track(
            name=self._name,
            data=data,
            description=self._description,
            tags=self._tags,
            metadata=self._metadata
        )
        return self

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

    def flush(self) -> Optional[Dict[str, Any]]:
        """
        Flush buffered data for this track to storage/remote.

        This merges all buffered data points with the same _ts before writing.

        Returns:
            Result from backend (trackId, startIndex, endIndex, count, ...) or None if no data

        Example:
            # Append and flush
            session.track("robot/state").append(q=[0.1, 0.2], _ts=1.0)
            session.track("robot/state").append(v=[0.01, 0.02], _ts=1.0)
            result = session.track("robot/state").flush()
            # Writes merged: {_ts: 1.0, q: [0.1, 0.2], v: [0.01, 0.02]}

            # Can also chain
            session.track("loss").append(value=0.5, epoch=1).flush()
        """
        return self._session._flush_track(
            name=self._name,
            description=self._description,
            tags=self._tags,
            metadata=self._metadata
        )

    def read(self, start_index: int = 0, limit: int = 1000) -> Dict[str, Any]:
        """
        Read data points from the track by index range.

        Automatically flushes buffered data before reading.

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
        # Auto-flush before reading
        self.flush()

        return self._session._read_track_data(
            name=self._name,
            start_index=start_index,
            limit=limit
        )

    def stats(self) -> Dict[str, Any]:
        """
        Get track statistics and metadata.

        Automatically flushes buffered data before querying stats.

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
        # Auto-flush before getting stats
        self.flush()

        return self._session._get_track_stats(name=self._name)

    def list_all(self) -> List[Dict[str, Any]]:
        """
        List all tracks in the session.

        Automatically flushes all buffered tracks before listing.

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
        # Auto-flush all tracks before listing
        self._session._flush_all_tracks()

        return self._session._list_tracks()
