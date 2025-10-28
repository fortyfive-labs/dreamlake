# Files

Dreamlake allows you to upload and manage files associated with your experiments, such as trained models, datasets, visualizations, and other artifacts.

## Basic File Upload

```python
from dreamlake import Session

with Session(name="demo", workspace="test", local_path="./data") as session:
    # Upload a file
    result = session.files().upload("model.pth", path="/models")

    print(f"File uploaded: {result['filename']}")
    print(f"Size: {result['sizeBytes']} bytes")
```

## Upload with Metadata

Add metadata to your files:

```python
with Session(name="demo", workspace="test", local_path="./data") as session:
    result = session.files().upload(
        "model.pth",
        path="/models/checkpoints",
        description="Best model from epoch 50",
        tags=["checkpoint", "best"],
        metadata={
            "epoch": 50,
            "val_accuracy": 0.95,
            "optimizer_state": True
        }
    )
```

## File Paths

Organize files using logical paths:

```python
with Session(name="demo", workspace="test", local_path="./data") as session:
    # Models
    session.files().upload("model.pth", path="/models")
    session.files().upload("best_model.pth", path="/models/checkpoints")

    # Data
    session.files().upload("train.csv", path="/data")
    session.files().upload("test.csv", path="/data")

    # Visualizations
    session.files().upload("loss_curve.png", path="/visualizations")
    session.files().upload("confusion_matrix.png", path="/visualizations")

    # Logs
    session.files().upload("training.log", path="/logs")
```

## Upload Multiple Files

Upload multiple files at once:

```python
with Session(name="demo", workspace="test", local_path="./data") as session:
    files_to_upload = [
        ("model.pth", "/models"),
        ("config.json", "/config"),
        ("results.csv", "/results"),
        ("plot.png", "/visualizations")
    ]

    for filename, path in files_to_upload:
        result = session.files().upload(filename, path=path)
        print(f"Uploaded: {filename}")
```

## List Files

List all files in a session:

```python
with Session(name="demo", workspace="test", local_path="./data") as session:
    # Upload some files
    session.files().upload("model1.pth", path="/models")
    session.files().upload("model2.pth", path="/models")

    # List all files
    files = session.files().list()

    print(f"Found {len(files)} files:")
    for file in files:
        print(f"  - {file['path']}/{file['filename']} ({file['sizeBytes']} bytes)")
```

## File Checksums

Files are automatically checksummed for integrity:

```python
with Session(name="demo", workspace="test", local_path="./data") as session:
    result = session.files().upload("model.pth", path="/models")

    print(f"Checksum (SHA256): {result['checksum']}")
    # Use checksum to verify file integrity
```

## Training Example with File Upload

Save checkpoints and final model:

```python
import torch
from dreamlake import Session

def train_model():
    with Session(
        name="resnet-training",
        workspace="computer-vision",
        local_path="./experiments"
    ) as session:
        session.parameters().set(
            model="resnet50",
            epochs=100,
            batch_size=32
        )

        session.log("Starting training")

        best_accuracy = 0.0

        for epoch in range(100):
            # Train
            train_loss = train_one_epoch(model, train_loader)
            val_loss, val_accuracy = validate(model, val_loader)

            # Track metrics
            session.track("train_loss").append(value=train_loss, epoch=epoch)
            session.track("val_accuracy").append(value=val_accuracy, epoch=epoch)

            # Save checkpoint every 10 epochs
            if (epoch + 1) % 10 == 0:
                checkpoint_path = f"checkpoint_epoch_{epoch + 1}.pth"
                torch.save({
                    'epoch': epoch,
                    'model_state_dict': model.state_dict(),
                    'optimizer_state_dict': optimizer.state_dict(),
                    'loss': train_loss,
                }, checkpoint_path)

                session.files().upload(
                    checkpoint_path,
                    path="/models/checkpoints",
                    description=f"Checkpoint at epoch {epoch + 1}",
                    tags=["checkpoint"],
                    metadata={"epoch": epoch + 1, "val_accuracy": val_accuracy}
                )

            # Save best model
            if val_accuracy > best_accuracy:
                best_accuracy = val_accuracy

                torch.save(model.state_dict(), "best_model.pth")
                session.files().upload(
                    "best_model.pth",
                    path="/models",
                    description=f"Best model (accuracy: {best_accuracy:.4f})",
                    tags=["best", "final"],
                    metadata={"epoch": epoch + 1, "accuracy": best_accuracy}
                )

                session.log(f"New best model saved (accuracy: {best_accuracy:.4f})")

        # Save final model
        torch.save(model.state_dict(), "final_model.pth")
        session.files().upload(
            "final_model.pth",
            path="/models",
            description="Final model after all epochs",
            tags=["final"]
        )

        session.log("Training complete")
```

## Save Plots and Visualizations

Upload matplotlib figures:

```python
import matplotlib.pyplot as plt
from dreamlake import Session

with Session(name="demo", workspace="test", local_path="./data") as session:
    # Generate plot
    losses = [0.5, 0.4, 0.3, 0.25, 0.2]
    plt.plot(losses)
    plt.title("Training Loss")
    plt.xlabel("Epoch")
    plt.ylabel("Loss")

    # Save to file
    plt.savefig("loss_curve.png")

    # Upload to session
    session.files().upload(
        "loss_curve.png",
        path="/visualizations",
        description="Training loss over epochs",
        tags=["plot", "loss"]
    )

    plt.close()
```

## Save Configuration Files

Upload experiment configuration:

```python
import json
from dreamlake import Session

config = {
    "model": {"architecture": "resnet50", "pretrained": True},
    "training": {"epochs": 100, "batch_size": 32, "lr": 0.001},
    "data": {"dataset": "imagenet", "augmentation": True}
}

with Session(name="demo", workspace="test", local_path="./data") as session:
    # Track parameters
    session.params(config)

    # Also save config as file
    with open("config.json", "w") as f:
        json.dump(config, f, indent=2)

    session.files().upload(
        "config.json",
        path="/config",
        description="Experiment configuration",
        tags=["config"]
    )
```

## Export Results

Save and upload results:

```python
import pandas as pd
from dreamlake import Session

with Session(name="demo", workspace="test", local_path="./data") as session:
    # Run experiment and collect results
    results = []
    for epoch in range(10):
        train_loss, val_loss, accuracy = train_and_validate()
        results.append({
            "epoch": epoch,
            "train_loss": train_loss,
            "val_loss": val_loss,
            "accuracy": accuracy
        })

    # Save to CSV
    df = pd.DataFrame(results)
    df.to_csv("results.csv", index=False)

    # Upload
    session.files().upload(
        "results.csv",
        path="/results",
        description="Training results per epoch",
        tags=["results", "metrics"]
    )
```

## File Size Limits

- **Maximum file size**: 5GB per file
- Larger files will be rejected

```python
import os
from dreamlake import Session

with Session(name="demo", workspace="test", local_path="./data") as session:
    filename = "large_model.pth"

    # Check file size before uploading
    file_size = os.path.getsize(filename)
    max_size = 5 * 1024 * 1024 * 1024  # 5GB

    if file_size > max_size:
        session.log(f"File too large: {file_size} bytes", level="error")
    else:
        session.files().upload(filename, path="/models")
        session.log(f"File uploaded: {file_size} bytes")
```

## Storage Location

### Local Mode

Files are stored in the session directory:

```
.dreamlake/
└── test/
    └── demo/
        ├── files/
        │   ├── models/
        │   │   └── model.pth
        │   └── visualizations/
        │       └── plot.png
        ├── files_metadata.json
        ├── logs.jsonl
        └── parameters.json
```

### Remote Mode

Files are uploaded to S3 and metadata stored in MongoDB:

```
S3: s3://bucket/files/{namespaceId}/{workspaceId}/{sessionId}/{fileId}/model.pth
MongoDB: File metadata (path, size, checksum, tags, etc.)
```

## Best Practices

1. **Organize with paths**: Use logical directory structure
2. **Add metadata**: Include relevant information (epoch, metrics, etc.)
3. **Tag files**: Use tags for easy filtering
4. **Save regularly**: Upload checkpoints during training
5. **Include config**: Always save your configuration
6. **Document files**: Add clear descriptions

## Common Patterns

### Checkpoint Strategy

```python
with Session(name="training", workspace="ml", local_path="./data") as session:
    for epoch in range(100):
        train()

        # Save checkpoint every N epochs
        if (epoch + 1) % save_interval == 0:
            save_checkpoint(f"checkpoint_{epoch + 1}.pth")
            session.files().upload(
                f"checkpoint_{epoch + 1}.pth",
                path="/checkpoints",
                tags=["checkpoint"],
                metadata={"epoch": epoch + 1}
            )

        # Save best model
        if is_best:
            save_model("best.pth")
            session.files().upload(
                "best.pth",
                path="/models",
                tags=["best"]
            )
```

### Experiment Artifacts

```python
with Session(name="experiment", workspace="research", local_path="./data") as session:
    # Upload all relevant files
    artifacts = {
        "model.pth": "/models",
        "config.json": "/config",
        "results.csv": "/results",
        "loss_curve.png": "/plots",
        "confusion_matrix.png": "/plots",
        "training.log": "/logs"
    }

    for filename, path in artifacts.items():
        if os.path.exists(filename):
            session.files().upload(filename, path=path)
```

## Next Steps

- [Local vs Remote Mode](07-local-vs-remote.md) - Understanding modes
- [Complete Examples](08-complete-examples.md) - Full examples
