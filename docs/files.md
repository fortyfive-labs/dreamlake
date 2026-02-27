# Files

Upload and manage experiment artifacts - models, plots, configs, and results. Files are automatically checksummed and organized with metadata.

## Basic Upload

```{code-block} python
:linenos:

from dreamlake import Session

with Session(prefix="project/my-experiment",
        local_path=".dreamlake") as session:
    result = session.files.upload("model.pth", path="/models")

    print(f"Uploaded: {result['filename']}")
    print(f"Size: {result['sizeBytes']} bytes")
    print(f"Checksum: {result['checksum']}")
```

## Organizing Files

Use paths to organize files logically:

```{code-block} python
:linenos:

with Session(prefix="project/my-experiment",
        local_path=".dreamlake") as session:
    # Models
    session.files.upload("model.pth", path="/models")
    session.files.upload("best_model.pth", path="/models/checkpoints")

    # Visualizations
    session.files.upload("loss_curve.png", path="/visualizations")
    session.files.upload("confusion_matrix.png", path="/visualizations")

    # Configuration
    session.files.upload("config.json", path="/config")

    # Results
    session.files.upload("results.csv", path="/results")
```

## File Metadata

Add description, tags, and custom metadata:

```{code-block} python
:linenos:

with Session(prefix="project/my-experiment",
        local_path=".dreamlake") as session:
    session.files.upload("best_model.pth", path="/models",
        description="Best model from epoch 50",
        tags=["checkpoint", "best"],
        metadata={
            "epoch": 50,
            "val_accuracy": 0.95,
            "optimizer_state": True
        }
    )
```

## Training with Checkpoints

Save models during training:

```{code-block} python
:linenos:

import torch
from dreamlake import Session

with Session(prefix="cv/resnet-training",
        local_path=".dreamlake") as session:
    session.params.set(model="resnet50", epochs=100)
    session.log("Starting training")

    best_accuracy = 0.0

    for epoch in range(100):
        train_loss = train_one_epoch(model, train_loader)
        val_loss, val_accuracy = validate(model, val_loader)

        # Track metrics
        session.track("train").append(loss=train_loss, epoch=epoch)
        session.track("val").append(accuracy=val_accuracy, epoch=epoch)

        # Save checkpoint every 10 epochs
        if (epoch + 1) % 10 == 0:
            checkpoint_path = f"checkpoint_epoch_{epoch + 1}.pth"
            torch.save(model.state_dict(), checkpoint_path)

            session.file(
                checkpoint_path,
                prefix="/checkpoints",
                tags=["checkpoint"],
                metadata={"epoch": epoch + 1, "val_accuracy": val_accuracy}
            )

        # Save best model
        if val_accuracy > best_accuracy:
            best_accuracy = val_accuracy

            torch.save(model.state_dict(), "best_model.pth")
            session.files.upload("best_model.pth", path="/models",
                description=f"Best model (accuracy: {best_accuracy:.4f})",
                tags=["best"],
                metadata={"epoch": epoch + 1, "accuracy": best_accuracy}
            )

            session.log(f"New best model saved (accuracy: {best_accuracy:.4f})")

    session.log("Training complete")
```

## Saving Visualizations

Upload matplotlib plots:

```{code-block} python
:linenos:

import matplotlib.pyplot as plt
from dreamlake import Session

with Session(prefix="project/my-experiment",
        local_path=".dreamlake") as session:
    # Generate plot
    losses = [0.5, 0.4, 0.3, 0.25, 0.2]
    plt.plot(losses)
    plt.title("Training Loss")
    plt.xlabel("Epoch")
    plt.ylabel("Loss")

    # Save and upload
    plt.savefig("loss_curve.png")
    session.files.upload("loss_curve.png", path="/visualizations",
        description="Training loss over epochs",
        tags=["plot"]
    )

    plt.close()
```

## Uploading Configuration

Save config files alongside parameters:

```{code-block} python
:linenos:

import json
from dreamlake import Session

config = {
    "model": {"architecture": "resnet50", "pretrained": True},
    "training": {"epochs": 100, "batch_size": 32, "lr": 0.001}
}

with Session(prefix="project/my-experiment",
        local_path=".dreamlake") as session:
    # Track as parameters
    session.params.set(**config)

    # Also save as file
    with open("config.json", "w") as f:
        json.dump(config, f, indent=2)

    session.files.upload("config.json", path="/config",
        description="Experiment configuration",
        tags=["config"]
    )
```

## Storage Format

**Local mode** - Files stored in session directory with prefix organization:

```
./experiments/
└── project/
    └── my-experiment/
        └── files/
            ├── .files_metadata.json        # Centralized metadata
            ├── models/                     # Prefix folder
            │   ├── {file_id_1}/           # Unique file ID
            │   │   └── model.pth
            │   └── {file_id_2}/
            │       └── best_model.pth
            ├── visualizations/
            │   └── {file_id_3}/
            │       └── loss_curve.png
            └── config/
                └── {file_id_4}/
                    └── config.json
```

Each file is stored in a unique ID folder within its prefix directory, ensuring no conflicts and enabling easy tracking.

**Remote mode** - Files uploaded to S3, metadata in MongoDB:
- Files stored: `s3://bucket/files/{namespace}/{workspace}/{session}/{file_id}/filename`
- Metadata: path, size, SHA256 checksum, tags, description

**File size limit:** 5GB per file

---

**That's it!** You've completed all the core DreamLake tutorials. Check out the API Reference for detailed method documentation.
