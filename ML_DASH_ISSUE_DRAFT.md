# Feature Request: Global timestamp inheritance with `_ts=-1` for multi-modal data synchronization

## Use Case

When logging multi-modal data in robotics/ML experiments, you often need to synchronize multiple data streams with the same timestamp. For example:

- Robot pose + camera images + sensor readings at the same instant
- Training metrics + model weights + validation results at the same step
- Video frame + audio sample + subtitle text at the same time

**Current problem**: Manually passing the same timestamp to every track append is verbose and error-prone:

```python
ts = time.time()
experiment.tracks("robot/pose").append(position=[1.0, 2.0, 3.0], _ts=ts)
experiment.tracks("camera/left/image").append(width=640, height=480, _ts=ts)
experiment.tracks("robot/velocity").append(linear=[0.1, 0.2, 0.3], _ts=ts)
experiment.tracks("sensors/lidar").append(ranges=[1.5, 2.0, 2.5], _ts=ts)
```

## Proposed Solution

Add support for `_ts=-1` as a sentinel value to **inherit the last timestamp globally across all tracks in a session**.

### API Design

```python
# First append: auto-generate timestamp (or use explicit value)
experiment.tracks("robot/pose").append(position=[1.0, 2.0, 3.0])

# Following appends: inherit the same timestamp using _ts=-1
experiment.tracks("camera/left/image").append(width=640, height=480, _ts=-1)
experiment.tracks("robot/velocity").append(linear=[0.1, 0.2, 0.3], _ts=-1)
experiment.tracks("sensors/lidar").append(ranges=[1.5, 2.0, 2.5], _ts=-1)
```

All four tracks now share the exact same timestamp!

### Behavior Specification

**Timestamp handling in `tracks().append(_ts=..., **kwargs)`:**

1. **`_ts=<number>`**: Use that explicit timestamp (seconds since epoch)
2. **`_ts=-1`**: Inherit timestamp from previous append (across ALL tracks in session)
3. **`_ts` not provided**: Auto-generate using `time.time()`

**Important properties:**

- The inheritance is **global across all tracks** (not per-track)
- Works across different Python files/modules that share the same `Experiment` instance
- Session stores last timestamp as instance variable: `self._last_timestamp`
- Thread-safe with proper locking for concurrent appends

### Example: Multi-Modal Robot Logging

```python
from ml_dash import Experiment
import time

with Experiment(name="robot-demo") as exp:
    for step in range(100):
        # First append - auto-generates timestamp
        exp.tracks("robot/pose").append(position=[1.0 + step * 0.1, 2.0, 3.0])

        # Following appends - inherit same timestamp
        exp.tracks("camera/left/image").append(width=640, height=480, _ts=-1)
        exp.tracks("camera/right/image").append(width=640, height=480, _ts=-1)
        exp.tracks("robot/velocity").append(linear=[0.1, 0.0, 0.0], _ts=-1)
        exp.tracks("sensors/lidar").append(ranges=[1.5, 2.0, 2.5], _ts=-1)

        time.sleep(0.01)

    # Verify synchronization
    pose_data = exp.tracks("robot/pose").read()
    image_data = exp.tracks("camera/left/image").read()

    # All tracks have matching timestamps!
    assert pose_data['data'][0]['_ts'] == image_data['data'][0]['_ts']
```

## Implementation Details

This feature has been implemented in **DreamLake** (a fork of ML-Dash) with the following approach:

### Session-Level State

```python
class Session:
    def __init__(self, ...):
        self._last_timestamp: Optional[float] = None  # Global last timestamp
        self._track_buffers: Dict[str, List[Dict]] = {}
        self._track_buffer_lock = threading.Lock()
```

### Append Logic

```python
def _append_to_track(self, name: str, data: Dict[str, Any], ...):
    # Handle _ts field
    if '_ts' not in data:
        # Auto-generate unique timestamp
        data['_ts'] = time.time()
    elif data['_ts'] == -1:
        # Inherit global last timestamp
        if self._last_timestamp is not None:
            data['_ts'] = self._last_timestamp
        else:
            raise ValueError("Cannot use _ts=-1: no previous timestamp to inherit")

    # Validate _ts is a number
    if not isinstance(data['_ts'], (int, float)):
        raise ValueError("_ts must be a number (seconds since epoch)")

    # Update global last timestamp
    self._last_timestamp = data['_ts']

    # Add to buffer
    with self._track_buffer_lock:
        if name not in self._track_buffers:
            self._track_buffers[name] = []
        self._track_buffers[name].append(data)
```

### Test Coverage

Full test in `test_timestamp_features.py`:

```python
def test_timestamp_inheritance_across_tracks():
    """Test timestamp inheritance with _ts=-1 across different tracks."""
    with Session(...) as session:
        # First append - auto-generates timestamp
        session.track("robot/pose").append(position=[1.0, 2.0, 3.0])

        # Second append on different track - inherits same timestamp
        session.track("camera/left/image").append(width=640, height=480, _ts=-1)

        # Third append on another track - also inherits same timestamp
        session.track("robot/velocity").append(linear=[0.1, 0.2, 0.3], _ts=-1)

        # Read back from all tracks
        pose_data = session.track("robot/pose").read(start_index=0, limit=10)
        image_data = session.track("camera/left/image").read(start_index=0, limit=10)
        velocity_data = session.track("robot/velocity").read(start_index=0, limit=10)

        # All three tracks should have same timestamp
        pose_ts = pose_data["data"][0]["data"]["_ts"]
        image_ts = image_data["data"][0]["data"]["_ts"]
        velocity_ts = velocity_data["data"][0]["data"]["_ts"]

        assert pose_ts == image_ts == velocity_ts
```

## Benefits

1. **Cleaner code**: No need to manually pass timestamps around
2. **Less error-prone**: Can't accidentally use wrong timestamp
3. **Multi-modal friendly**: Natural API for synchronized logging
4. **Backward compatible**: Only affects code that uses `_ts=-1`
5. **Thread-safe**: Works correctly with concurrent track appends

## Related Work

- This feature exists in ROS bag files (topics share timestamps)
- Similar to MCAP timestamp inheritance in multi-channel recordings
- Aligns with robotics logging patterns (poses, images, sensors at same instant)

## Request

Would ML-Dash consider adding this feature? I'm happy to contribute a PR if there's interest.

The implementation is straightforward and has been tested in production use cases (robotics data logging).

---

**Implementation reference**: https://github.com/fortyfive-labs/dreamlake (DreamLake fork)
**Test coverage**: `test/test_timestamp_features.py::test_timestamp_inheritance_across_tracks`
