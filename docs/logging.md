# Logging

Dreamlake provides structured logging for your experiments. Logs are stored with timestamps, levels, and optional metadata.

## Basic Logging

```python
from dreamlake import Session

with Session(name="demo", workspace="test", local_path="./data") as session:
    # Simple log message
    session.log("Training started")

    # Log with level
    session.log("Model architecture: ResNet-50", level="info")
    session.log("GPU memory low", level="warn")
    session.log("Failed to load checkpoint", level="error")
```

## Log Levels

Dreamlake supports standard log levels:

- `debug`: Detailed debugging information
- `info`: General information (default)
- `warn`: Warning messages
- `error`: Error messages
- `fatal`: Fatal errors

```python
with Session(name="demo", workspace="test", local_path="./data") as session:
    session.log("Verbose debug info", level="debug")
    session.log("Training epoch 1", level="info")
    session.log("Learning rate decreased", level="warn")
    session.log("Gradient exploded", level="error")
    session.log("Out of memory - aborting", level="fatal")
```

## Structured Logging with Metadata

Add structured metadata to your logs:

```python
with Session(name="demo", workspace="test", local_path="./data") as session:
    # Log with metrics
    session.log(
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
    session.log(
        "System status",
        level="info",
        metadata={
            "gpu_memory": "8GB",
            "cpu_percent": 45.2,
            "disk_space": "50GB"
        }
    )
```

## Logging During Training

Common pattern for training loops:

```python
import time
from dreamlake import Session

with Session(name="mnist-training", workspace="ml", local_path="./data") as session:
    session.log("Starting training", level="info")

    for epoch in range(10):
        session.log(f"Epoch {epoch + 1}/10", level="info")

        # Training phase
        train_loss = 0.0
        for batch_idx, (data, target) in enumerate(train_loader):
            loss = train_step(data, target)
            train_loss += loss

            # Log every 100 batches
            if batch_idx % 100 == 0:
                session.log(
                    f"Batch {batch_idx}",
                    level="debug",
                    metadata={"batch_loss": loss}
                )

        avg_train_loss = train_loss / len(train_loader)

        # Validation phase
        val_loss = validate(model, val_loader)

        # Log epoch summary
        session.log(
            f"Epoch {epoch + 1} complete",
            level="info",
            metadata={
                "epoch": epoch + 1,
                "train_loss": avg_train_loss,
                "val_loss": val_loss,
                "time": time.time()
            }
        )
```

## Log Storage Format

In local mode, logs are stored as JSONL (JSON Lines):

```bash
cat .dreamlake/test/demo/logs.jsonl
```

Output:
```json
{"timestamp": "2025-10-25T10:30:00Z", "level": "info", "message": "Training started", "metadata": null, "sequenceNumber": 0}
{"timestamp": "2025-10-25T10:30:05Z", "level": "info", "message": "Epoch 1 complete", "metadata": {"train_loss": 0.5, "val_loss": 0.6}, "sequenceNumber": 1}
```

## Error Logging

Log exceptions and errors:

```python
with Session(name="demo", workspace="test", local_path="./data") as session:
    try:
        result = risky_operation()
        session.log("Operation succeeded", level="info")
    except Exception as e:
        session.log(
            f"Operation failed: {str(e)}",
            level="error",
            metadata={
                "error_type": type(e).__name__,
                "error_message": str(e)
            }
        )
        raise
```

## Progress Logging

Log progress updates:

```python
with Session(name="data-processing", workspace="etl", local_path="./data") as session:
    total = 10000
    session.log(f"Processing {total} items", level="info")

    for i in range(total):
        process_item(i)

        # Log progress every 10%
        if (i + 1) % (total // 10) == 0:
            percent = ((i + 1) / total) * 100
            session.log(
                f"Progress: {percent:.0f}%",
                level="info",
                metadata={
                    "processed": i + 1,
                    "total": total,
                    "percent": percent
                }
            )

    session.log("Processing complete", level="info")
```

## Conditional Logging

Log based on conditions:

```python
with Session(name="demo", workspace="test", local_path="./data") as session:
    verbose = True

    if verbose:
        session.log("Verbose mode enabled", level="debug")

    loss = train()

    if loss > threshold:
        session.log(
            f"Loss {loss} exceeds threshold {threshold}",
            level="warn",
            metadata={"loss": loss, "threshold": threshold}
        )
```

## Best Practices

1. **Use appropriate levels**: Don't log everything as "info"
2. **Add metadata**: Include relevant metrics and context
3. **Be concise**: Keep messages clear and actionable
4. **Log important events**: Session start/end, epoch completion, errors
5. **Use structured data**: Prefer metadata over string formatting

## Common Patterns

### Training Loop Logging

```python
with Session(name="training", workspace="ml", local_path="./data") as session:
    session.log("Training started", level="info", metadata={"model": "ResNet50"})

    for epoch in range(epochs):
        # Start of epoch
        session.log(f"Starting epoch {epoch + 1}", level="info")

        # Training
        metrics = train_one_epoch()

        # End of epoch
        session.log(
            f"Epoch {epoch + 1} complete",
            level="info",
            metadata=metrics
        )

    session.log("Training complete", level="info")
```

### Debugging

```python
with Session(name="debug", workspace="test", local_path="./data") as session:
    session.log("Debug session started", level="debug")

    session.log(
        "Model architecture",
        level="debug",
        metadata={"layers": count_layers(model)}
    )

    session.log(
        "Data shapes",
        level="debug",
        metadata={
            "input_shape": data.shape,
            "batch_size": batch_size
        }
    )
```

### Error Tracking

```python
with Session(name="production", workspace="prod", local_path="./data") as session:
    errors = []

    for item in items:
        try:
            process(item)
        except Exception as e:
            errors.append(str(e))
            session.log(
                f"Failed to process item {item}",
                level="error",
                metadata={"item": item, "error": str(e)}
            )

    if errors:
        session.log(
            f"Processing complete with {len(errors)} errors",
            level="warn",
            metadata={"total_errors": len(errors)}
        )
    else:
        session.log("Processing complete successfully", level="info")
```

## Next Steps

- [Parameters](04-parameters.md) - Track hyperparameters
- [Tracks](05-tracks.md) - Time-series metrics
- [Complete Examples](08-complete-examples.md) - Full training examples
