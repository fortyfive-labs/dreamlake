# Tracks

Tracks are time-series data streams for tracking metrics that change over time, such as training loss, validation accuracy, learning rates, and custom measurements.

## Basic Track Usage

```python
from dreamlake import Session

with Session(name="demo", workspace="test", local_path="./data") as session:
    # Append a single data point
    result = session.track("train_loss").append(value=0.5, epoch=1)
    print(f"Appended at index {result['index']}")
```

## Flexible Data Schema

Tracks support flexible JSON schemas - you define the structure:

```python
with Session(name="demo", workspace="test", local_path="./data") as session:
    # Simple value tracking
    session.track("loss").append(value=0.5)

    # With step/epoch
    session.track("accuracy").append(value=0.85, step=100, epoch=1)

    # Multiple metrics per point
    session.track("metrics").append(
        loss=0.5,
        accuracy=0.85,
        learning_rate=0.001,
        epoch=1
    )

    # With timestamps
    import time
    session.track("system").append(
        cpu_percent=45.2,
        memory_mb=1024,
        timestamp=time.time()
    )
```

## Batch Append

For efficiency, append multiple data points at once:

```python
with Session(name="demo", workspace="test", local_path="./data") as session:
    # Append batch of data points
    result = session.track("train_loss").append_batch([
        {"value": 0.5, "step": 1, "epoch": 1},
        {"value": 0.45, "step": 2, "epoch": 1},
        {"value": 0.40, "step": 3, "epoch": 1},
        {"value": 0.38, "step": 4, "epoch": 1},
    ])

    print(f"Appended {result['count']} data points")
    print(f"Index range: {result['startIndex']} to {result['endIndex']}")
```

## Reading Track Data

Read data points by index range:

```python
with Session(name="demo", workspace="test", local_path="./data") as session:
    # Append some data first
    for i in range(100):
        session.track("loss").append(value=1.0 / (i + 1), step=i)

    # Read first 10 points
    result = session.track("loss").read(start_index=0, limit=10)

    print(f"Total points: {result['total']}")
    for point in result['data']:
        print(f"Index {point['index']}: {point['data']}")
```

## Track Statistics

Get statistics about a track:

```python
with Session(name="demo", workspace="test", local_path="./data") as session:
    # Append data
    for i in range(1000):
        session.track("loss").append(value=1.0 / (i + 1), step=i)

    # Get stats
    stats = session.track("loss").stats()

    print(f"Track: {stats['name']}")
    print(f"Total data points: {stats['totalDataPoints']}")
    print(f"Buffered: {stats['bufferedDataPoints']}")
    print(f"Chunked: {stats['chunkedDataPoints']}")
```

## List All Tracks

List all tracks in a session:

```python
with Session(name="demo", workspace="test", local_path="./data") as session:
    # Create multiple tracks
    session.track("train_loss").append(value=0.5)
    session.track("val_loss").append(value=0.6)
    session.track("train_acc").append(value=0.85)
    session.track("val_acc").append(value=0.83)

    # List all tracks
    tracks = session.track("train_loss").list_all()

    print(f"Found {len(tracks)} tracks:")
    for track in tracks:
        print(f"  - {track['name']}: {track['totalDataPoints']} points")
```

## Training Loop Example

Common pattern for training:

```python
from dreamlake import Session

def train_model():
    with Session(
        name="mnist-training",
        workspace="computer-vision",
        local_path="./experiments"
    ) as session:
        session.parameters().set(
            learning_rate=0.001,
            batch_size=32,
            epochs=10
        )

        session.log("Starting training")

        for epoch in range(10):
            # Training phase
            train_loss = 0.0
            train_correct = 0

            for batch_idx, (data, target) in enumerate(train_loader):
                loss, correct = train_step(data, target)
                train_loss += loss
                train_correct += correct

            # Calculate averages
            avg_train_loss = train_loss / len(train_loader)
            train_accuracy = train_correct / len(train_dataset)

            # Validation phase
            val_loss, val_accuracy = validate(model, val_loader)

            # Track metrics
            session.track("train_loss").append(
                value=avg_train_loss,
                epoch=epoch + 1
            )
            session.track("train_accuracy").append(
                value=train_accuracy,
                epoch=epoch + 1
            )
            session.track("val_loss").append(
                value=val_loss,
                epoch=epoch + 1
            )
            session.track("val_accuracy").append(
                value=val_accuracy,
                epoch=epoch + 1
            )

            # Log progress
            session.log(
                f"Epoch {epoch + 1}/10 complete",
                metadata={
                    "train_loss": avg_train_loss,
                    "val_loss": val_loss,
                    "train_acc": train_accuracy,
                    "val_acc": val_accuracy
                }
            )

        session.log("Training complete")
```

## Multiple Metrics in One Track

Track multiple related metrics together:

```python
with Session(name="demo", workspace="test", local_path="./data") as session:
    for epoch in range(10):
        # All metrics in one track
        session.track("all_metrics").append(
            epoch=epoch,
            train_loss=0.5 / (epoch + 1),
            val_loss=0.6 / (epoch + 1),
            train_acc=0.8 + epoch * 0.01,
            val_acc=0.75 + epoch * 0.01,
            learning_rate=0.001 * (0.9 ** epoch)
        )
```

## Learning Rate Tracking

Track learning rate changes:

```python
with Session(name="demo", workspace="test", local_path="./data") as session:
    learning_rate = 0.1

    for epoch in range(100):
        # Track current learning rate
        session.track("learning_rate").append(
            value=learning_rate,
            epoch=epoch
        )

        # Train...
        train_loss = train_one_epoch(learning_rate)

        session.track("train_loss").append(
            value=train_loss,
            epoch=epoch
        )

        # Reduce learning rate
        if (epoch + 1) % 30 == 0:
            learning_rate *= 0.1
            session.log(f"Learning rate reduced to {learning_rate}")
```

## System Monitoring

Track system metrics:

```python
import psutil
import time
from dreamlake import Session

with Session(name="system-monitor", workspace="monitoring", local_path="./data") as session:
    for i in range(100):
        # Get system metrics
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()

        # Track system metrics
        session.track("system_metrics").append(
            timestamp=time.time(),
            cpu_percent=cpu_percent,
            memory_percent=memory.percent,
            memory_used_gb=memory.used / (1024**3),
            memory_available_gb=memory.available / (1024**3)
        )

        time.sleep(5)
```

## Batch Tracking for Performance

Use batch append for better performance:

```python
with Session(name="demo", workspace="test", local_path="./data") as session:
    # Collect data points
    batch = []

    for step in range(1000):
        loss = train_step()

        # Add to batch
        batch.append({
            "value": loss,
            "step": step,
            "epoch": step // 100
        })

        # Append batch every 100 steps
        if len(batch) >= 100:
            session.track("train_loss").append_batch(batch)
            batch = []

    # Append remaining points
    if batch:
        session.track("train_loss").append_batch(batch)
```

## Track Metadata

Add metadata to tracks:

```python
with Session(name="demo", workspace="test", local_path="./data") as session:
    # Create track with metadata
    session.track(
        name="train_loss",
        description="Training loss (cross-entropy)",
        tags=["training", "loss"],
        metadata={"unit": "nats", "goal": "minimize"}
    ).append(value=0.5, epoch=1)
```

## Data Storage

### Local Mode

In local mode, tracks are stored as JSONL:

```bash
cat .dreamlake/test/demo/tracks/train_loss/data.jsonl
```

Output:
```json
{"index": 0, "data": {"value": 0.5, "epoch": 1}}
{"index": 1, "data": {"value": 0.45, "epoch": 2}}
{"index": 2, "data": {"value": 0.40, "epoch": 3}}
```

Metadata:
```bash
cat .dreamlake/test/demo/tracks/train_loss/metadata.json
```

```json
{
  "name": "train_loss",
  "totalDataPoints": 3,
  "bufferedDataPoints": 3,
  "chunkedDataPoints": 0
}
```

### Remote Mode

In remote mode, tracks use a two-tier storage:
- **Hot tier**: Recent data in MongoDB (fast access)
- **Cold tier**: Historical data in S3 (cost-effective)

Data is automatically chunked from MongoDB to S3 when the buffer reaches a threshold (default: 10,000 points).

## Best Practices

1. **Consistent schema**: Keep the same fields across all points in a track
2. **Use batch append**: For better performance when logging many points
3. **Separate tracks**: Don't mix unrelated metrics (separate train_loss and val_loss)
4. **Add context**: Include step, epoch, or timestamp in each point
5. **Meaningful names**: Use clear, descriptive track names

## Common Patterns

### Training with Validation

```python
with Session(name="training", workspace="ml", local_path="./data") as session:
    for epoch in range(epochs):
        # Training
        train_metrics = train_epoch()
        session.track("train_loss").append(value=train_metrics['loss'], epoch=epoch)
        session.track("train_acc").append(value=train_metrics['acc'], epoch=epoch)

        # Validation
        val_metrics = validate_epoch()
        session.track("val_loss").append(value=val_metrics['loss'], epoch=epoch)
        session.track("val_acc").append(value=val_metrics['acc'], epoch=epoch)
```

### Per-Step Logging

```python
with Session(name="demo", workspace="test", local_path="./data") as session:
    global_step = 0

    for epoch in range(epochs):
        for batch in train_loader:
            loss = train_step(batch)

            # Log every step
            session.track("loss").append(
                value=loss,
                step=global_step,
                epoch=epoch
            )

            global_step += 1
```

### Comparison with Logging and Parameters

- **Logs**: Events, messages, status updates
- **Parameters**: Configuration values, hyperparameters (set once)
- **Tracks**: Metrics that change over time

```python
with Session(name="demo", workspace="test", local_path="./data") as session:
    # Parameters - configuration (set once)
    session.params(learning_rate=0.001, batch_size=32)

    # Logs - events and messages
    session.log("Training started", level="info")

    # Tracks - time-series metrics
    for epoch in range(10):
        loss = train()
        session.track("loss").append(value=loss, epoch=epoch)

    session.log("Training complete", level="info")
```

## Next Steps

- [Files](06-files.md) - Upload models and artifacts
- [Complete Examples](08-complete-examples.md) - Full training examples
