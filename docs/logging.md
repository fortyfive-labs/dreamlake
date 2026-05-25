# Logging

Track events and progress with timestamps, levels, and optional metadata.

## Basic Usage

```{code-block} python
:linenos:

from dreamlake import Episode

with Episode(prefix="project/my-experiment",
        local_path=".dreamlake") as episode:
    episode.log("Training started")
    episode.log("GPU memory low", level="warn")
    episode.log("Failed to load checkpoint", level="error")
```

**Levels:** `debug`, `info` (default), `warn`, `error`, `fatal`

## Structured Metadata

```{code-block} python
:linenos:

episode.log(
    "Epoch completed",
    level="info",
    metadata={"epoch": 5, "train_loss": 0.234, "val_loss": 0.456}
)
```

## Training Loop Pattern

```{code-block} python
:linenos:

with Episode(prefix="ml/mnist-training",
        local_path=".dreamlake") as episode:
    episode.log("Starting training")

    for epoch in range(10):
        train_loss = train_one_epoch(model, train_loader)
        val_loss = validate(model, val_loader)

        episode.log(
            f"Epoch {epoch + 1} complete",
            metadata={"train_loss": train_loss, "val_loss": val_loss}
        )

    episode.log("Training complete")
```

## Storage

**Local:** JSONL at `logs/logs.jsonl`

```json
{"timestamp": "2025-10-29T10:30:00Z", "level": "info", "message": "Training started", "sequenceNumber": 0}
```

**Remote:** MongoDB with indexing on timestamp and level.
