# Parameters

Track hyperparameters, configuration values, and experiment settings. Parameters are static key-value pairs that define your experiment.

## Basic Usage

```{code-block} python
:linenos:

from dreamlake import Episode

with Episode(prefix="project/my-experiment",
        local_path=".dreamlake") as episode:
    episode.params.set(
        learning_rate=0.001,
        batch_size=32,
        optimizer="adam",
        epochs=100
    )
```

## Nested Parameters

Use nested dictionaries - they're automatically flattened with dot notation:

```{code-block} python
:linenos:

with Episode(prefix="project/my-experiment",
        local_path=".dreamlake") as episode:
    episode.params.set(**{
        "model": {
            "architecture": "resnet50",
            "pretrained": True,
            "num_classes": 1000
        },
        "optimizer": {
            "type": "adam",
            "lr": 0.001,
            "weight_decay": 0.0001
        }
    })

    # Stored as:
    # model.architecture = "resnet50"
    # model.pretrained = True
    # optimizer.type = "adam"
    # ...
```

## Updating Parameters

Call `parameters().set()` multiple times - values merge and overwrite:

```{code-block} python
:linenos:

with Episode(prefix="project/my-experiment",
        local_path=".dreamlake") as episode:
    # Initial parameters
    episode.params.set(learning_rate=0.001, batch_size=32)

    # Add more
    episode.params.set(optimizer="adam", momentum=0.9)

    # Update existing
    episode.params.set(learning_rate=0.0001)

    # Final result:
    # learning_rate = 0.0001  (updated)
    # batch_size = 32
    # optimizer = "adam"
    # momentum = 0.9
```

## Loading from Config Files

**From JSON:**

```{code-block} python
:linenos:

import json
from dreamlake import Episode

with open("config.json", "r") as f:
    config = json.load(f)

with Episode(prefix="project/my-experiment",
        local_path=".dreamlake") as episode:
    episode.params.set(**config)
    episode.log("Configuration loaded")
```

**From command line arguments:**

```{code-block} python
:linenos:

import argparse
from dreamlake import Episode

parser = argparse.ArgumentParser()
parser.add_argument("--lr", type=float, default=0.001)
parser.add_argument("--batch-size", type=int, default=32)
args = parser.parse_args()

with Episode(prefix="project/my-experiment",
        local_path=".dreamlake") as episode:
    episode.params.set(**vars(args))
```

**From dataclass:**

```{code-block} python
:linenos:

from dataclasses import dataclass, asdict
from dreamlake import Episode

@dataclass
class TrainingConfig:
    learning_rate: float = 0.001
    batch_size: int = 32
    epochs: int = 100

config = TrainingConfig()

with Episode(prefix="project/my-experiment",
        local_path=".dreamlake") as episode:
    episode.params.set(**asdict(config))
```

## Complete Training Configuration

```{code-block} python
:linenos:

with Episode(prefix="cv/resnet-imagenet",
        local_path=".dreamlake") as episode:
    episode.params.set(**{
        "model": {
            "architecture": "resnet50",
            "pretrained": True,
            "num_classes": 1000
        },
        "data": {
            "dataset": "imagenet",
            "train_split": 0.8,
            "num_workers": 4
        },
        "training": {
            "epochs": 100,
            "batch_size": 256,
            "learning_rate": 0.1,
            "optimizer": "sgd",
            "momentum": 0.9
        }
    })
```

## Storage Format

**Local mode** - Stored as JSON:

```bash
cat ./experiments/project/my-experiment/parameters.json
```

```json
{
  "learning_rate": 0.001,
  "batch_size": 32,
  "optimizer": "adam",
  "model.architecture": "resnet50",
  "model.pretrained": true
}
```

**Remote mode** - Stored in MongoDB as a document.

---

**Next:** Learn about [Tracks](tracks.md) for time-series metrics tracking.
