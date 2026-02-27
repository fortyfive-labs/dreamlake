# DreamLake - API Quick Reference

Quick reference for common DreamLake operations.

## Session Creation

```python
from dreamlake import Session

# Local mode
with Session(prefix="workspace-name/experiment-name",
    root="./data",
        local_path=".dreamlake"
) as session:
    # Your code here
    pass

# Remote mode (with username - auto-generates API key)
with Session(prefix="workspace-name/experiment-name",
    url="https://cu3thurmv3.us-east-1.awsapprunner.com",
    user_name="your-username"
) as session:
    # Your code here
    pass
```

**Note**: When using `user_name`, an API key is automatically generated from the username. This is useful for development when a full authentication service isn't available yet.

## Logging

```python
# Simple log
session.log("Training started")

# Log with level
session.log("Error occurred", level="error")

# Log with metadata
session.log(
    "Epoch complete",
    level="info",
    metadata={"epoch": 5, "loss": 0.234}
)
```

**Levels**: `debug`, `info`, `warn`, `error`, `fatal`

## Parameters

```python
# Set parameters (keyword arguments)
session.params.set(
    learning_rate=0.001,
    batch_size=32
)

# Set parameters (dictionary - supports nested)
session.params.set(**{
    "model": {
        "architecture": "resnet50",
        "layers": 50
    }
})
# Stored as: {"model.architecture": "resnet50", "model.layers": 50}

# Update parameters
session.params.set(learning_rate=0.0001)
```

## Tracks (Time-Series Metrics)

```python
# Append single data point (auto-generated timestamp)
session.track("train").append(loss=0.5, epoch=1)

# Flexible schema
session.track("metrics").append(
    loss=0.5,
    accuracy=0.85,
    epoch=1
)

# Explicit timestamp
import time
session.track("robot/position").append(q=[0.1, 0.2], _ts=time.time())

# Timestamp inheritance for multi-modal synchronization
session.track("robot/pose").append(position=[1.0, 2.0])  # Auto-generated _ts
session.track("camera/left").append(width=640, _ts=-1)   # Inherits same _ts!
session.track("robot/velocity").append(linear=[0.1], _ts=-1)  # Same _ts!

# Timestamp merging (same _ts merges fields)
ts = time.time()
session.track("robot/state").append(q=[0.1, 0.2], _ts=ts)
session.track("robot/state").append(v=[0.01, 0.02], _ts=ts)
session.track("robot/state").flush()
# Result: {_ts: ts, q: [0.1, 0.2], v: [0.01, 0.02]}

# Hierarchical track names
session.track("robot/position/left-camera").append(x=1.0, y=2.0)

# Batch append
session.track("loss").append_batch([
    {"value": 0.5, "epoch": 1},
    {"value": 0.4, "epoch": 2},
    {"value": 0.3, "epoch": 3}
])

# Flush operations
session.track("loss").flush()      # Flush one track
session.tracks.flush()             # Flush all tracks

# Read data
result = session.track("loss").read(start_index=0, limit=10)
for point in result['data']:
    print(f"Index {point['index']}: {point['data']}")

# Get statistics
stats = session.track("loss").stats()
print(f"Total points: {stats['totalDataPoints']}")

# List all tracks
tracks = session.tracks.list()
for track in tracks:
    print(f"{track['name']}: {track['totalDataPoints']} points")
```

## Files

```python
# Upload file
session.files.upload("model.pth", path="models/",
    description="Trained model",
    tags=["final", "best"]
)

# Upload with metadata
session.files.upload("model.pth", path="models/checkpoints/",
    metadata={"epoch": 50, "accuracy": 0.95}
)

# List files
files = session.files.list()
for file in files:
    print(f"{file['prefix']}{file['filename']}")
```

## Complete Example

```python
from dreamlake import Session

with Session(prefix="computer-vision/mnist-training",
    root="./experiments",
        local_path=".dreamlake"
) as session:
    # Configuration
    session.params.set(
        learning_rate=0.001,
        batch_size=64,
        epochs=10
    )

    session.log("Training started", level="info")

    # Training loop
    for epoch in range(10):
        # Train
        train_loss, val_loss, accuracy = train_one_epoch()

        # Track metrics
        session.track("train").append(loss=train_loss, epoch=epoch)
        session.track("val").append(loss=val_loss, epoch=epoch)
        session.track("metrics").append(accuracy=accuracy, epoch=epoch)

        # Log progress
        session.log(
            f"Epoch {epoch + 1}/10 complete",
            metadata={
                "train_loss": train_loss,
                "val_loss": val_loss,
                "accuracy": accuracy
            }
        )

    # Save model
    save_model("model.pth")
    session.files.upload("model.pth", path="models/")

    session.log("Training complete!", level="info")
```

## Common Patterns

### Training with Checkpoints

```python
with Session(...) as session:
    best_acc = 0
    for epoch in range(epochs):
        train()
        acc = validate()

        session.track("metrics").append(accuracy=acc, epoch=epoch)

        if acc > best_acc:
            best_acc = acc
            save_checkpoint(f"checkpoint_{epoch}.pth")
            session.files.upload(f"checkpoint_{epoch}.pth", path="checkpoints/",
                tags=["best"]
            )
```

### Hyperparameter Search

```python
for lr in [0.1, 0.01, 0.001]:
    for bs in [32, 64, 128]:
        with Session(name=f"search-lr{lr}-bs{bs}", ...) as session:
            session.params.set(
                learning_rate=lr,
                batch_size=bs
            )

            accuracy = train(lr, bs)
            session.track("metrics").append(accuracy=accuracy)
```

### Progress Logging

```python
with Session(...) as session:
    total = 1000
    for i in range(total):
        process_item(i)

        if i % 100 == 0:
            percent = (i / total) * 100
            session.log(
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
        │       ├── data.jsonl
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
cat .dreamlake/workspace/session/logs.jsonl

# View parameters
cat .dreamlake/workspace/session/parameters.json

# View track data
cat .dreamlake/workspace/session/tracks/train_loss/data.jsonl

# List files
ls .dreamlake/workspace/session/files/
```

## See Also

- [Getting Started](getting-started.md)
- [Complete Examples](complete-examples.md)
- [Runnable Examples](examples.md)
