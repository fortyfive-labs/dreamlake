# Tracks

Track time-series metrics that change over time - loss, accuracy, learning rate, and custom measurements. Tracks support flexible schemas that you define.

## Basic Usage

```{code-block} python
:linenos:

from dreamlake import Session

with Session(name="my-experiment", workspace="project",
        local_path=".dreamlake") as session:
    # Append a single data point
    session.track("train_loss").append(value=0.5, epoch=1)

    # With step and epoch
    session.track("accuracy").append(value=0.85, step=100, epoch=1)
```

## Flexible Schema

Define your own data structure for each track:

```{code-block} python
:linenos:

with Session(name="my-experiment", workspace="project",
        local_path=".dreamlake") as session:
    # Simple value
    session.track("loss").append(value=0.5)

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

Append multiple data points at once for better performance:

```{code-block} python
:linenos:

with Session(name="my-experiment", workspace="project",
        local_path=".dreamlake") as session:
    result = session.track("train_loss").append_batch([
        {"value": 0.5, "step": 1, "epoch": 1},
        {"value": 0.45, "step": 2, "epoch": 1},
        {"value": 0.40, "step": 3, "epoch": 1},
        {"value": 0.38, "step": 4, "epoch": 1},
    ])

    print(f"Appended {result['count']} points")
```

## Reading Data

Read track data by index range:

```{code-block} python
:linenos:

with Session(name="my-experiment", workspace="project",
        local_path=".dreamlake") as session:
    # Append data
    for i in range(100):
        session.track("loss").append(value=1.0 / (i + 1), step=i)

    # Read first 10 points
    result = session.track("loss").read(start_index=0, limit=10)

    for point in result['data']:
        print(f"Index {point['index']}: {point['data']}")
```

## Training Loop Example

```{code-block} python
:linenos:

with Session(name="mnist-training", workspace="cv",
        local_path=".dreamlake") as session:
    session.parameters().set(learning_rate=0.001, batch_size=32)
    session.log("Starting training")

    for epoch in range(10):
        train_loss = train_one_epoch(model, train_loader)
        val_loss, val_accuracy = validate(model, val_loader)

        # Track metrics
        session.track("train_loss").append(value=train_loss, epoch=epoch + 1)
        session.track("val_loss").append(value=val_loss, epoch=epoch + 1)
        session.track("val_accuracy").append(value=val_accuracy, epoch=epoch + 1)

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

with Session(name="my-experiment", workspace="project",
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

with Session(name="my-experiment", workspace="project",
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

## Storage Format

**Local mode** - JSONL files:

```bash
cat ./experiments/project/my-experiment/tracks/train_loss/data.jsonl
```

```json
{"index": 0, "data": {"value": 0.5, "epoch": 1}}
{"index": 1, "data": {"value": 0.45, "epoch": 2}}
{"index": 2, "data": {"value": 0.40, "epoch": 3}}
```

**Remote mode** - Two-tier storage:
- **Hot tier:** Recent data in MongoDB (fast access)
- **Cold tier:** Historical data in S3 (auto-archived after 10,000 points)

---

**Next:** Learn about [Files](files.md) to upload models, plots, and artifacts.
