# Tracks

Track time-series metrics that change over time - loss, accuracy, learning rate, and custom measurements. Tracks support flexible schemas that you define.

## Basic Usage

```{code-block} python
:linenos:

from dreamlake import Session

with Session(prefix="project/my-experiment",
        local_path=".dreamlake") as session:
    # Append a single data point
    session.track("train").append(loss=0.5, epoch=1)

    # With step and epoch
    session.track("metrics").append(accuracy=0.85, step=100, epoch=1)
```

## Flexible Schema

Define your own data structure for each track:

```{code-block} python
:linenos:

with Session(prefix="project/my-experiment",
        local_path=".dreamlake") as session:
    # Simple value
    session.track("train").append(loss=0.5)

    # Multiple fields per point
    session.track("metrics").append(
        loss=0.5,
        accuracy=0.85,
        learning_rate=0.001,
        epoch=1
    )

    # With timestamp
    import time
    session.track("system").append(
        cpu_percent=45.2,
        memory_mb=1024,
        timestamp=time.time()
    )
```

## Batch Append

Append multiple data points at once for better performance. Batch appends automatically use **columnar storage format** internally for efficient storage and faster writes:

```{code-block} python
:linenos:

with Session(prefix="project/my-experiment",
        local_path=".dreamlake") as session:
    # Batch append - uses columnar format internally
    result = session.track("train_loss").append_batch([
        {"value": 0.5, "step": 1, "epoch": 1},
        {"value": 0.45, "step": 2, "epoch": 1},
        {"value": 0.40, "step": 3, "epoch": 1},
        {"value": 0.38, "step": 4, "epoch": 1},
    ])

    print(f"Appended {result['count']} points")
    # 5-10x faster than individual appends!
```

### Storage Formats

DreamLake automatically chooses the optimal storage format:

- **Row format**: Used for single `append()` calls
  - Storage: `{"value": 10, "_ts": 1.0}`
  - Best for: Streaming data, incremental logging

- **Columnar format**: Automatically used for `append_batch()` calls
  - Storage: `{"_ts": [1.0, 2.0], "value": [10, 20]}`
  - Best for: Batch writes, large datasets
  - Benefits: Better compression, faster writes, more storage efficient

The format is **transparent to users** - reading works the same regardless of how data was written.

See [08_batch_columnar_format.py](examples/08_batch_columnar_format.py) for detailed examples and performance comparisons.

## Reading Data

### Reading by Index

Read track data by index range:

```{code-block} python
:linenos:

with Session(prefix="project/my-experiment",
        local_path=".dreamlake") as session:
    # Append data
    for i in range(100):
        session.track("train").append(loss=1.0 / (i + 1), step=i)

    # Read first 10 points
    result = session.track("loss").read(start_index=0, limit=10)

    for point in result['data']:
        print(f"Index {point['index']}: {point['data']}")
```

### Reading by Time Range (MCAP-like API)

Query track data by timestamp range using the `read_by_time()` method. This is especially useful for robotics and time-series data:

```{code-block} python
:linenos:

import time
from dreamlake import Session

with Session(prefix="project/my-experiment",
        local_path=".dreamlake") as session:
    # Write data with timestamps
    base_time = time.time()
    for i in range(100):
        session.track("robot/pose").append(
            position=[i * 0.1, i * 0.2, i * 0.3],
            _ts=base_time + i * 0.1
        )

    # Query last 10 seconds
    result = session.track("robot/pose").read_by_time(
        start_time=base_time + 90 * 0.1,  # Last 10 points
        end_time=base_time + 100 * 0.1,
        limit=1000
    )

    print(f"Found {result['total']} points in time range")
```

**Time Range Parameters:**
- `start_time`: Starting timestamp (seconds since epoch, inclusive). `None` = from beginning
- `end_time`: Ending timestamp (seconds since epoch, exclusive). `None` = to end
- `limit`: Maximum number of points (default 1000, max 10000)
- `reverse`: If `True`, return newest points first (default `False`)

**Examples:**

```{code-block} python
:linenos:

# Get most recent data (reverse order)
result = session.track("robot/pose").read_by_time(
    reverse=True,
    limit=100
)
print(f"Latest position: {result['data'][0]['data']['position']}")

# Query from specific time to end
result = session.track("robot/pose").read_by_time(
    start_time=1234567890.0,
    limit=1000
)

# Query all data in a session
result = session.track("robot/pose").read_by_time(limit=10000)
```

**Use Cases:**
- Robotics: Query pose data for the last N seconds
- Real-time monitoring: Get recent metrics in reverse order
- Data export: Extract data for specific time ranges
- Synchronized playback: Read multi-modal data by time

## Training Loop Example

```{code-block} python
:linenos:

with Session(prefix="cv/mnist-training",
        local_path=".dreamlake") as session:
    session.params.set(learning_rate=0.001, batch_size=32)
    session.log("Starting training")

    for epoch in range(10):
        train_loss = train_one_epoch(model, train_loader)
        val_loss, val_accuracy = validate(model, val_loader)

        # Track metrics
        session.track("train").append(loss=train_loss, epoch=epoch + 1)
        session.track("val").append(loss=val_loss, epoch=epoch + 1)
        session.track("val").append(accuracy=val_accuracy, epoch=epoch + 1)

        session.log(
            f"Epoch {epoch + 1}/10 complete",
            metadata={"train_loss": train_loss, "val_loss": val_loss}
        )

    session.log("Training complete")
```

## Batch Collection Pattern

Collect points in memory, then append in batches:

```{code-block} python
:linenos:

with Session(prefix="project/my-experiment",
        local_path=".dreamlake") as session:
    batch = []

    for step in range(1000):
        loss = train_step()

        batch.append({"value": loss, "step": step, "epoch": step // 100})

        # Append every 100 steps
        if len(batch) >= 100:
            session.track("train_loss").append_batch(batch)
            batch = []

    # Append remaining
    if batch:
        session.track("train_loss").append_batch(batch)
```

## Multiple Metrics in One Track

Combine related metrics:

```{code-block} python
:linenos:

with Session(prefix="project/my-experiment",
        local_path=".dreamlake") as session:
    for epoch in range(10):
        session.track("all_metrics").append(
            epoch=epoch,
            train_loss=0.5 / (epoch + 1),
            val_loss=0.6 / (epoch + 1),
            train_acc=0.8 + epoch * 0.01,
            val_acc=0.75 + epoch * 0.01
        )
```

## Timestamp-Based Tracking

### Auto-Generated Timestamps

All track data includes a `_ts` (timestamp) field. If not provided, timestamps are auto-generated:

```{code-block} python
:linenos:

import time
from dreamlake import Session

with Session(prefix="project/my-experiment",
        local_path=".dreamlake") as session:
    # Auto-generated timestamp
    session.track("train").append(loss=0.5, epoch=1)

    # Read back - _ts was added automatically
    data = session.track("loss").read(start_index=0, limit=1)
    print(data['data'][0]['data']['_ts'])  # e.g., 1234567890.123
```

### Explicit Timestamps

Use explicit timestamps for precise control:

```{code-block} python
:linenos:

with Session(prefix="project/my-experiment",
        local_path=".dreamlake") as session:
    ts = time.time()

    session.track("robot/position").append(
        q=[0.1, 0.2, 0.3],
        _ts=ts
    )
```

### Timestamp Inheritance (`_ts=-1`)

Use `_ts=-1` to inherit the previous timestamp across ALL tracks in the session. This is useful for synchronizing multi-modal data:

```{code-block} python
:linenos:

with Session(prefix="robotics/robot-demo",
        local_path=".dreamlake") as session:
    for step in range(100):
        # First append - auto-generates timestamp
        session.track("robot/pose").append(
            position=[1.0, 2.0, 3.0],
            orientation=[0.0, 0.0, 0.0, 1.0]
        )

        # Following appends - inherit the same timestamp
        session.track("camera/left/image").append(
            width=640,
            height=480,
            frame_id=step,
            _ts=-1  # Same timestamp as robot/pose!
        )

        session.track("robot/velocity").append(
            linear=[0.1, 0.0, 0.0],
            angular=[0.0, 0.0, 0.05],
            _ts=-1  # Same timestamp!
        )

    # All tracks have synchronized timestamps
    pose_data = session.track("robot/pose").read()
    image_data = session.track("camera/left/image").read()
    assert pose_data['data'][0]['data']['_ts'] == image_data['data'][0]['data']['_ts']
```

**Key properties:**
- `_ts=-1` inherits globally across ALL tracks (not per-track)
- Works across different Python files/modules that share the same session
- Enables clean multi-modal data synchronization (pose + images + sensors)

### Timestamp Merging

Data points with the same `_ts` are merged into a single entry:

```{code-block} python
:linenos:

with Session(prefix="project/merge-demo",
        local_path=".dreamlake") as session:
    ts = time.time()

    # Append different fields with same timestamp
    session.track("robot/state").append(q=[0.1, 0.2], _ts=ts)
    session.track("robot/state").append(v=[0.01, 0.02], _ts=ts)
    session.track("robot/state").append(e=[0.5, 0.6, 0.7], _ts=ts)

    # Flush to apply merging
    session.track("robot/state").flush()

    # Read back - merged into single entry
    data = session.track("robot/state").read()
    assert len(data['data']) == 1

    merged = data['data'][0]['data']
    # Contains all fields: {_ts: ..., q: [...], v: [...], e: [...]}
    assert 'q' in merged and 'v' in merged and 'e' in merged
```

### Buffering and Flushing

Track appends are buffered in memory and merged by timestamp before writing:

```{code-block} python
:linenos:

with Session(prefix="project/my-experiment",
        local_path=".dreamlake") as session:
    # Append data (buffered)
    session.track("train").append(loss=0.5, epoch=1)
    session.track("train").append(loss=0.4, epoch=2)

    # Flush specific track
    session.track("loss").flush()

    # Flush all tracks
    session.tracks.flush()

    # Auto-flush on read/stats/list
    data = session.track("loss").read()  # Automatically flushes first

    # Auto-flush on session close
    # (session context manager calls flush on exit)
```

### Complete Time-Based Example

Here's a complete example combining timestamps, time-range queries, and reverse iteration:

```{code-block} python
:linenos:

import time
from dreamlake import Session

with Session(prefix="robotics/robot-demo",
        local_path=".dreamlake") as session:
    # Record robot data over 10 seconds
    base_time = time.time()

    for i in range(100):
        current_time = base_time + i * 0.1

        # First track - generates timestamp
        session.track("robot/pose").append(
            position=[i * 0.1, i * 0.2, i * 0.3],
            orientation=[0.0, 0.0, 0.0, 1.0],
            _ts=current_time
        )

        # Synchronized tracks using same timestamp
        session.track("robot/velocity").append(
            linear=[0.1, 0.0, 0.0],
            angular=[0.0, 0.0, 0.05],
            _ts=current_time
        )

        session.track("robot/torque").append(
            joints=[1.0, 2.0, 3.0, 4.0],
            _ts=current_time
        )

    # Query last 3 seconds of data
    recent_pose = session.track("robot/pose").read_by_time(
        start_time=base_time + 7.0,
        end_time=base_time + 10.0,
        limit=1000
    )
    print(f"Last 3 seconds: {recent_pose['total']} pose samples")

    # Get the 10 most recent velocity readings
    recent_velocity = session.track("robot/velocity").read_by_time(
        reverse=True,
        limit=10
    )
    print(f"Latest velocity: {recent_velocity['data'][0]['data']['linear']}")

    # Query specific time window
    middle_data = session.track("robot/torque").read_by_time(
        start_time=base_time + 4.0,
        end_time=base_time + 6.0,
        limit=1000
    )
    print(f"Middle 2 seconds: {middle_data['total']} torque samples")
```

### Hierarchical Track Names

Use `/` to organize tracks hierarchically:

```{code-block} python
:linenos:

with Session(prefix="project/my-experiment",
        local_path=".dreamlake") as session:
    session.track("robot/position/left-camera").append(x=1.0, y=2.0, _ts=1.0)
    session.track("robot/position/right-camera").append(x=1.1, y=2.1, _ts=1.0)
    session.track("robot/velocity").append(vx=0.5, vy=0.6, _ts=1.0)

    # List all tracks
    tracks = session.tracks.list()
    # Returns: ["robot/position/left-camera", "robot/position/right-camera", "robot/velocity"]
```

## Storage Format

**Local mode** - Msgpack-lines files:

Track data is stored in msgpack-lines format (one msgpack object per line), which is efficient and handles binary data. The storage layer supports both row-based and columnar formats:

**Row format** (single appends):
```python
# Each append creates one msgpack object
{"value": 0.5, "epoch": 1}
{"value": 0.45, "epoch": 2}
```

**Columnar format** (batch appends):
```python
# Batch appends are stored in columnar format for efficiency
{"value": [0.5, 0.45, 0.40], "epoch": [1, 2, 3]}
```

Both formats are transparently handled by the storage layer. Files are stored as `data.msgpack`:

```bash
# View track data (requires msgpack tools)
python -c "import msgpack; [print(obj) for obj in msgpack.Unpacker(open('./experiments/project/my-experiment/tracks/train_loss/data.msgpack', 'rb'), raw=False)]"
```

**Remote mode** - Two-tier storage:
- **Hot tier:** Recent data in MongoDB (fast access)
- **Cold tier:** Historical data in S3 (auto-archived after 10,000 points)

---

**Next:** Learn about [Files](files.md) to upload models, plots, and artifacts.
