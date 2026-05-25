# Tracks

Time-series metrics — loss, accuracy, robot state, or any structured data that changes over time.

## Basic Usage

```{code-block} python
:linenos:

from dreamlake import Episode

with Episode(prefix="project/my-experiment",
        local_path=".dreamlake") as episode:
    episode.track("train").append(loss=0.5, epoch=1)
    episode.track("metrics").append(accuracy=0.85, learning_rate=0.001, epoch=1)
```

Each track accepts arbitrary fields.

## Batch Append

Append multiple points at once. Uses columnar storage internally for efficiency.

```{code-block} python
:linenos:

episode.track("train_loss").append_batch([
    {"value": 0.5, "step": 1, "epoch": 1},
    {"value": 0.45, "step": 2, "epoch": 1},
    {"value": 0.40, "step": 3, "epoch": 1},
])
```

Collect-then-flush pattern for training loops:

```{code-block} python
:linenos:

batch = []
for step in range(1000):
    loss = train_step()
    batch.append({"value": loss, "step": step})

    if len(batch) >= 100:
        episode.track("train_loss").append_batch(batch)
        batch = []

if batch:
    episode.track("train_loss").append_batch(batch)
```

## Reading Data

```{code-block} python
:linenos:

# By index
result = episode.track("loss").read(start_index=0, limit=10)
for point in result['data']:
    print(f"Index {point['index']}: {point['data']}")

# By time range
result = episode.track("robot/pose").read_by_time(
    start_time=base_time + 7.0,
    end_time=base_time + 10.0,
    limit=1000
)

# Most recent data (reverse order)
result = episode.track("robot/pose").read_by_time(reverse=True, limit=100)
```

**Time range parameters:** `start_time` (inclusive), `end_time` (exclusive), `limit` (default 1000, max 10000), `reverse`.

## Timestamps

All data includes a `_ts` field. If not provided, it's auto-generated.

```{code-block} python
:linenos:

import time

# Explicit timestamp
episode.track("robot/position").append(q=[0.1, 0.2, 0.3], _ts=time.time())

# Timestamp inheritance — _ts=-1 copies the previous timestamp across ALL tracks
episode.track("robot/pose").append(position=[1.0, 2.0, 3.0])
episode.track("camera/left").append(width=640, height=480, _ts=-1)
episode.track("robot/velocity").append(linear=[0.1, 0.0, 0.0], _ts=-1)
```

Data points with the same `_ts` on the same track are merged into a single entry:

```{code-block} python
:linenos:

ts = time.time()
episode.track("robot/state").append(q=[0.1, 0.2], _ts=ts)
episode.track("robot/state").append(v=[0.01, 0.02], _ts=ts)
episode.track("robot/state").flush()
# Result: {_ts: ts, q: [0.1, 0.2], v: [0.01, 0.02]}
```

## Flushing

Appends are buffered in memory. Flush happens automatically on read, stats, list, or episode close.

```{code-block} python
:linenos:

episode.track("loss").flush()    # flush one track
episode.tracks.flush()           # flush all tracks
```

## Hierarchical Names

Use `/` to organize tracks:

```{code-block} python
:linenos:

episode.track("robot/position/left-camera").append(x=1.0, y=2.0)
episode.track("robot/position/right-camera").append(x=1.1, y=2.1)

tracks = episode.tracks.list()
# ["robot/position/left-camera", "robot/position/right-camera"]
```

## Storage

**Local:** Msgpack-lines at `tracks/<name>/data.msgpack`. Row format for single appends, columnar for batches — transparent to the reader.

**Remote:** Recent data in MongoDB, historical data archived to S3.
