# Parameters

Parameters in Dreamlake are used to track hyperparameters, configuration values, and other key-value settings for your experiments.

## Basic Parameter Tracking

```python
from dreamlake import Session

with Session(name="demo", workspace="test", local_path="./data") as session:
    # Track parameters
    session.parameters().set(
        learning_rate=0.001,
        batch_size=32,
        optimizer="adam",
        epochs=100
    )
```

## Nested Parameters (Dot Notation)

Dreamlake automatically flattens nested dictionaries using dot notation:

```python
with Session(name="demo", workspace="test", local_path="./data") as session:
    # Pass nested dictionary
    session.parameters().set(**{
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

    # Stored as flat keys:
    # {
    #   "model.architecture": "resnet50",
    #   "model.pretrained": true,
    #   "model.num_classes": 1000,
    #   "optimizer.type": "adam",
    #   "optimizer.lr": 0.001,
    #   "optimizer.weight_decay": 0.0001
    # }
```

## Multiple Parameter Calls

You can call `parameters().set()` multiple times - new values merge with existing ones:

```python
with Session(name="demo", workspace="test", local_path="./data") as session:
    # Initial parameters
    session.parameters().set(learning_rate=0.001, batch_size=32)

    # Add more parameters
    session.parameters().set(optimizer="adam", momentum=0.9)

    # Update existing parameter
    session.parameters().set(learning_rate=0.0001)  # Overwrites previous value

    # Final parameters:
    # {
    #   "learning_rate": 0.0001,
    #   "batch_size": 32,
    #   "optimizer": "adam",
    #   "momentum": 0.9
    # }
```

## Parameter Types

Dreamlake supports various parameter types:

```python
with Session(name="demo", workspace="test", local_path="./data") as session:
    session.parameters().set(
        # Numbers
        learning_rate=0.001,
        batch_size=32,
        temperature=0.5,

        # Strings
        optimizer="adam",
        architecture="resnet50",

        # Booleans
        pretrained=True,
        use_dropout=False,

        # Lists
        layer_sizes=[256, 128, 64],
        augmentations=["flip", "rotate", "crop"],

        # None
        checkpoint_path=None
    )
```

## Training Configuration Example

Complete training configuration:

```python
with Session(
    name="resnet-imagenet",
    workspace="computer-vision",
    local_path="./experiments"
) as session:
    # Model parameters
    session.parameters().set({
        "model": {
            "architecture": "resnet50",
            "pretrained": True,
            "num_classes": 1000,
            "dropout": 0.5
        },
        "data": {
            "dataset": "imagenet",
            "train_split": 0.8,
            "val_split": 0.1,
            "test_split": 0.1,
            "num_workers": 4
        },
        "training": {
            "epochs": 100,
            "batch_size": 256,
            "learning_rate": 0.1,
            "optimizer": "sgd",
            "momentum": 0.9,
            "weight_decay": 0.0001,
            "scheduler": "cosine"
        },
        "augmentation": {
            "random_crop": True,
            "random_flip": True,
            "color_jitter": 0.4,
            "normalize": True
        }
    })
```

## Loading from Config File

Load parameters from a configuration file:

```python
import json
from dreamlake import Session

# Load config
with open("config.json", "r") as f:
    config = json.load(f)

with Session(name="experiment", workspace="ml", local_path="./data") as session:
    # Track all config parameters
    session.parameters().set(config)

    session.log("Configuration loaded")
```

## Command Line Arguments

Track command line arguments:

```python
import argparse
from dreamlake import Session

parser = argparse.ArgumentParser()
parser.add_argument("--lr", type=float, default=0.001)
parser.add_argument("--batch-size", type=int, default=32)
parser.add_argument("--optimizer", type=str, default="adam")
args = parser.parse_args()

with Session(name="cli-experiment", workspace="ml", local_path="./data") as session:
    # Convert argparse namespace to dict
    session.parameters().set(vars(args))

    session.log("Parameters loaded from CLI")
```

## Environment Information

Track environment and system information:

```python
import platform
import torch
from dreamlake import Session

with Session(name="demo", workspace="test", local_path="./data") as session:
    # Track environment info
    session.parameters().set({
        "environment": {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "pytorch_version": torch.__version__,
            "cuda_available": torch.cuda.is_available(),
            "cuda_version": torch.version.cuda if torch.cuda.is_available() else None,
            "device_count": torch.cuda.device_count() if torch.cuda.is_available() else 0
        }
    })
```

## Parameter Storage Format

In local mode, parameters are stored as JSON:

```bash
cat .dreamlake/test/demo/parameters.json
```

Output:
```json
{
  "learning_rate": 0.001,
  "batch_size": 32,
  "optimizer": "adam",
  "model.architecture": "resnet50",
  "model.pretrained": true,
  "model.num_classes": 1000
}
```

## Best Practices

1. **Track everything**: Model config, training config, data config, environment
2. **Use nested dicts**: Organize related parameters together
3. **Be consistent**: Use same parameter names across experiments
4. **Version control**: Also commit your config files to git
5. **Update carefully**: Remember that later calls overwrite earlier values

## Common Patterns

### Hyperparameter Search

```python
from itertools import product
from dreamlake import Session

learning_rates = [0.1, 0.01, 0.001]
batch_sizes = [32, 64, 128]

for lr, bs in product(learning_rates, batch_sizes):
    with Session(
        name=f"search-lr{lr}-bs{bs}",
        workspace="hyperparameter-search",
        local_path="./experiments"
    ) as session:
        session.parameters().set(
            learning_rate=lr,
            batch_size=bs,
            optimizer="adam"
        )

        accuracy = train_model(lr, bs)
        session.log(f"Final accuracy: {accuracy}")
```

### Config Class

```python
from dataclasses import dataclass, asdict
from dreamlake import Session

@dataclass
class TrainingConfig:
    learning_rate: float = 0.001
    batch_size: int = 32
    epochs: int = 100
    optimizer: str = "adam"

config = TrainingConfig()

with Session(name="experiment", workspace="ml", local_path="./data") as session:
    # Convert dataclass to dict
    session.parameters().set(asdict(config))
```

### Progressive Updates

```python
with Session(name="demo", workspace="test", local_path="./data") as session:
    # Initial config
    session.parameters().set(
        learning_rate=0.1,
        batch_size=32
    )

    # Train for a while...

    # Learning rate was reduced
    session.parameters().set(learning_rate=0.01)
    session.log("Learning rate reduced to 0.01")

    # Train more...

    # Learning rate reduced again
    session.parameters().set(learning_rate=0.001)
    session.log("Learning rate reduced to 0.001")
```

## Comparison with Tracks

**When to use Parameters:**
- Configuration values (model architecture, optimizer type)
- Hyperparameters (learning rate, batch size)
- System information (Python version, GPU type)
- Values that are set once or change rarely

**When to use Tracks:**
- Metrics that change over time (loss, accuracy)
- Time-series data (training progress)
- Values logged at each step/epoch
- Multiple measurements of the same metric

```python
with Session(name="demo", workspace="test", local_path="./data") as session:
    # Parameters - set once
    session.parameters().set(learning_rate=0.001, batch_size=32)

    # Tracks - logged repeatedly over time
    for epoch in range(100):
        loss = train()
        session.track("loss").append(value=loss, epoch=epoch)
```

## Next Steps

- [Tracks](05-tracks.md) - Time-series metrics tracking
- [Complete Examples](08-complete-examples.md) - See parameters in action
