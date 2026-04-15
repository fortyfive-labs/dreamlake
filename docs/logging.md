# Logging

Track events, progress, and debugging information throughout your experiments. Logs are stored with timestamps, levels, and optional metadata.

## Basic Usage

```{code-block} python
:linenos:

from dreamlake import Episode

with Episode(prefix="project/my-experiment",
        local_path=".dreamlake") as episode:
    episode.log("Training started")
    episode.log("Model architecture: ResNet-50", level="info")
    episode.log("GPU memory low", level="warn")
    episode.log("Failed to load checkpoint", level="error")
```

## Log Levels

**Available levels:** `debug`, `info` (default), `warn`, `error`, `fatal`

```{code-block} python
:linenos:

with Episode(prefix="project/my-experiment",
        local_path=".dreamlake") as episode:
    episode.log("Detailed debugging info", level="debug")
    episode.log("Training epoch 1", level="info")
    episode.log("Learning rate decreased", level="warn")
    episode.log("Gradient exploded", level="error")
    episode.log("Out of memory - aborting", level="fatal")
```

## Structured Metadata

Add context and metrics to your logs:

```{code-block} python
:linenos:

with Episode(prefix="project/my-experiment",
        local_path=".dreamlake") as episode:
    # Log with metrics
    episode.log(
        "Epoch completed",
        level="info",
        metadata={
            "epoch": 5,
            "train_loss": 0.234,
            "val_loss": 0.456,
            "learning_rate": 0.001
        }
    )

    # Log with system info
    episode.log(
        "System status",
        level="info",
        metadata={
            "gpu_memory": "8GB",
            "cpu_percent": 45.2
        }
    )
```

## Common Patterns

**Training loop:**

```{code-block} python
:linenos:

with Episode(prefix="ml/mnist-training",
        local_path=".dreamlake") as episode:
    episode.log("Starting training", level="info")

    for epoch in range(10):
        train_loss = train_one_epoch(model, train_loader)
        val_loss = validate(model, val_loader)

        episode.log(
            f"Epoch {epoch + 1} complete",
            level="info",
            metadata={
                "epoch": epoch + 1,
                "train_loss": train_loss,
                "val_loss": val_loss
            }
        )

    episode.log("Training complete", level="info")
```

**Error tracking:**

```{code-block} python
:linenos:

with Episode(prefix="project/my-experiment",
        local_path=".dreamlake") as episode:
    try:
        result = risky_operation()
        episode.log("Operation succeeded", level="info")
    except Exception as e:
        episode.log(
            f"Operation failed: {str(e)}",
            level="error",
            metadata={
                "error_type": type(e).__name__,
                "error_message": str(e)
            }
        )
        raise
```

**Progress tracking:**

```{code-block} python
:linenos:

with Episode(prefix="etl/data-processing",
        local_path=".dreamlake") as episode:
    total = 10000
    episode.log(f"Processing {total} items", level="info")

    for i in range(total):
        process_item(i)

        if (i + 1) % (total // 10) == 0:
            percent = ((i + 1) / total) * 100
            episode.log(
                f"Progress: {percent:.0f}%",
                level="info",
                metadata={"processed": i + 1, "total": total}
            )

    episode.log("Processing complete", level="info")
```

## Storage Format

**Local mode** - Logs stored as JSONL (JSON Lines):

```bash
cat ./experiments/project/my-experiment/logs/logs.jsonl
```

Each line is a JSON object:

```json
{"timestamp": "2025-10-29T10:30:00Z", "level": "info", "message": "Training started", "metadata": null, "sequenceNumber": 0}
{"timestamp": "2025-10-29T10:30:05Z", "level": "info", "message": "Epoch 1 complete", "metadata": {"train_loss": 0.5}, "sequenceNumber": 1}
```

**Remote mode** - Logs stored in MongoDB with automatic indexing on timestamp and level.

---

**Next:** Learn about [Parameters](parameters.md) to track hyperparameters and configuration.
