# DreamLake - API Quick Reference

Quick reference for common DreamLake operations.

## Episode Creation

```python
from dreamlake import Episode

# Local mode
with Episode(prefix="workspace-name/experiment-name",
    root="./data",
        local_path=".dreamlake"
) as episode:
    # Your code here
    pass

# Remote mode (with username - auto-generates API key)
with Episode(prefix="workspace-name/experiment-name",
    url="https://cu3thurmv3.us-east-1.awsapprunner.com",
    user_name="your-username"
) as episode:
    # Your code here
    pass
```

**Note**: When using `user_name`, an API key is automatically generated from the username. This is useful for development when a full authentication service isn't available yet.

## Logging

```python
# Simple log
episode.log("Training started")

# Log with level
episode.log("Error occurred", level="error")

# Log with metadata
episode.log(
    "Epoch complete",
    level="info",
    metadata={"epoch": 5, "loss": 0.234}
)
```

**Levels**: `debug`, `info`, `warn`, `error`, `fatal`

## Parameters

```python
# Set parameters (keyword arguments)
episode.params.set(
    learning_rate=0.001,
    batch_size=32
)

# Set parameters (dictionary - supports nested)
episode.params.set(**{
    "model": {
        "architecture": "resnet50",
        "layers": 50
    }
})
# Stored as: {"model.architecture": "resnet50", "model.layers": 50}

# Update parameters
episode.params.set(learning_rate=0.0001)
```

## Tracks (Time-Series Metrics)

```python
# Append single data point (auto-generated timestamp)
episode.track("train").append(loss=0.5, epoch=1)

# Flexible schema
episode.track("metrics").append(
    loss=0.5,
    accuracy=0.85,
    epoch=1
)

# Explicit timestamp
import time
episode.track("robot/position").append(q=[0.1, 0.2], _ts=time.time())

# Timestamp inheritance for multi-modal synchronization
episode.track("robot/pose").append(position=[1.0, 2.0])  # Auto-generated _ts
episode.track("camera/left").append(width=640, _ts=-1)   # Inherits same _ts!
episode.track("robot/velocity").append(linear=[0.1], _ts=-1)  # Same _ts!

# Timestamp merging (same _ts merges fields)
ts = time.time()
episode.track("robot/state").append(q=[0.1, 0.2], _ts=ts)
episode.track("robot/state").append(v=[0.01, 0.02], _ts=ts)
episode.track("robot/state").flush()
# Result: {_ts: ts, q: [0.1, 0.2], v: [0.01, 0.02]}

# Hierarchical track names
episode.track("robot/position/left-camera").append(x=1.0, y=2.0)

# Batch append
episode.track("loss").append_batch([
    {"value": 0.5, "epoch": 1},
    {"value": 0.4, "epoch": 2},
    {"value": 0.3, "epoch": 3}
])

# Flush operations
episode.track("loss").flush()      # Flush one track
episode.tracks.flush()             # Flush all tracks

# Read data by index
result = episode.track("loss").read(start_index=0, limit=10)
for point in result['data']:
    print(f"Index {point['index']}: {point['data']}")

# Read data by time range (MCAP-like API)
import time
base_time = time.time()
result = episode.track("robot/pose").read_by_time(
    start_time=base_time - 10.0,  # Last 10 seconds
    end_time=base_time,
    limit=1000
)

# Get most recent data (reverse order)
result = episode.track("robot/pose").read_by_time(reverse=True, limit=100)

# Get statistics
stats = episode.track("loss").stats()
print(f"Total points: {stats['totalDataPoints']}")

# List all tracks
tracks = episode.tracks.list()
for track in tracks:
    print(f"{track['name']}: {track['totalDataPoints']} points")
```

## Files

```python
# Upload file
episode.files.upload("model.pth", path="models/",
    description="Trained model",
    tags=["final", "best"]
)

# Upload with metadata
episode.files.upload("model.pth", path="models/checkpoints/",
    metadata={"epoch": 50, "accuracy": 0.95}
)

# List files
files = episode.files.list()
for file in files:
    print(f"{file['prefix']}{file['filename']}")
```

## Complete Example

```python
from dreamlake import Episode

with Episode(prefix="computer-vision/mnist-training",
    root="./experiments",
        local_path=".dreamlake"
) as episode:
    # Configuration
    episode.params.set(
        learning_rate=0.001,
        batch_size=64,
        epochs=10
    )

    episode.log("Training started", level="info")

    # Training loop
    for epoch in range(10):
        # Train
        train_loss, val_loss, accuracy = train_one_epoch()

        # Track metrics
        episode.track("train").append(loss=train_loss, epoch=epoch)
        episode.track("val").append(loss=val_loss, epoch=epoch)
        episode.track("metrics").append(accuracy=accuracy, epoch=epoch)

        # Log progress
        episode.log(
            f"Epoch {epoch + 1}/10 complete",
            metadata={
                "train_loss": train_loss,
                "val_loss": val_loss,
                "accuracy": accuracy
            }
        )

    # Save model
    save_model("model.pth")
    episode.files.upload("model.pth", path="models/")

    episode.log("Training complete!", level="info")
```

## Common Patterns

### Training with Checkpoints

```python
with Episode(...) as episode:
    best_acc = 0
    for epoch in range(epochs):
        train()
        acc = validate()

        episode.track("metrics").append(accuracy=acc, epoch=epoch)

        if acc > best_acc:
            best_acc = acc
            save_checkpoint(f"checkpoint_{epoch}.pth")
            episode.files.upload(f"checkpoint_{epoch}.pth", path="checkpoints/",
                tags=["best"]
            )
```

### Hyperparameter Search

```python
for lr in [0.1, 0.01, 0.001]:
    for bs in [32, 64, 128]:
        with Episode(name=f"search-lr{lr}-bs{bs}", ...) as episode:
            episode.params.set(
                learning_rate=lr,
                batch_size=bs
            )

            accuracy = train(lr, bs)
            episode.track("metrics").append(accuracy=accuracy)
```

### Progress Logging

```python
with Episode(...) as episode:
    total = 1000
    for i in range(total):
        process_item(i)

        if i % 100 == 0:
            percent = (i / total) * 100
            episode.log(
                f"Progress: {percent}%",
                metadata={"processed": i, "total": total}
            )
```

## Data Storage

### Local Mode

```
.dreamlake/
└── workspace-name/
    └── experiment-name/
        ├── logs.jsonl              # Log entries
        ├── parameters.json         # Parameters
        ├── tracks/                 # Time-series data
        │   └── train_loss/
        │       ├── data.msgpack    # Track data (msgpack-lines format)
        │       └── metadata.json
        └── files/                  # Uploaded files
            ├── .files_metadata.json
            └── models/
                └── {file_id}/
                    └── model.pth
```

### Remote Mode

- **MongoDB**: Logs, parameters, track metadata, file metadata
- **S3**: Uploaded files, archived logs, track chunks

## Useful Commands

```bash
# View logs
cat .dreamlake/workspace/episode/logs.jsonl

# View parameters
cat .dreamlake/workspace/episode/parameters.json

# View track data (requires msgpack tools)
python -c "import msgpack; [print(obj) for obj in msgpack.Unpacker(open('.dreamlake/workspace/episode/tracks/train_loss/data.msgpack', 'rb'), raw=False)]"

# List files
ls .dreamlake/workspace/episode/files/
```

## See Also

- [Getting Started](getting-started.md)
- [Complete Examples](complete-examples.md)
- [Runnable Examples](examples.md)
