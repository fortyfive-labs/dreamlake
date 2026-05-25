# Files

Upload experiment artifacts — models, plots, configs, results. Files are checksummed (SHA256) and organized with metadata.

## Upload

```{code-block} python
:linenos:

from dreamlake import Episode

with Episode(prefix="project/my-experiment",
        local_path=".dreamlake") as episode:
    episode.files.upload("model.pth", path="/models")
    episode.files.upload("loss_curve.png", path="/visualizations")
    episode.files.upload("config.json", path="/config",
        description="Experiment configuration",
        tags=["config"],
        metadata={"epoch": 50, "accuracy": 0.95}
    )
```

## Checkpoints During Training

```{code-block} python
:linenos:

import torch
from dreamlake import Episode

with Episode(prefix="cv/resnet-training",
        local_path=".dreamlake") as episode:
    best_accuracy = 0.0

    for epoch in range(100):
        train_loss = train_one_epoch(model, train_loader)
        _, val_accuracy = validate(model, val_loader)

        episode.track("train").append(loss=train_loss, epoch=epoch)
        episode.track("val").append(accuracy=val_accuracy, epoch=epoch)

        if val_accuracy > best_accuracy:
            best_accuracy = val_accuracy
            torch.save(model.state_dict(), "best_model.pth")
            episode.files.upload("best_model.pth", path="/models",
                tags=["best"],
                metadata={"epoch": epoch, "accuracy": best_accuracy}
            )
```

## List Files

```{code-block} python
:linenos:

files = episode.files.list()
for file in files:
    print(f"{file['prefix']}{file['filename']}")
```

## Storage

**Local:** Files stored under `files/<prefix>/<file_id>/<filename>` with centralized metadata in `.files_metadata.json`.

**Remote:** S3 with metadata in MongoDB. 5GB per file limit.
