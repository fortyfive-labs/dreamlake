# Parameters

Track hyperparameters and configuration. Parameters are static key-value pairs stored as JSON.

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

Nested dicts are flattened with dot notation:

```{code-block} python
:linenos:

episode.params.set(**{
    "model": {"architecture": "resnet50", "pretrained": True},
    "optimizer": {"type": "adam", "lr": 0.001}
})
# Stored as: model.architecture = "resnet50", optimizer.type = "adam", ...
```

## Updating

Multiple `set()` calls merge and overwrite:

```{code-block} python
:linenos:

episode.params.set(learning_rate=0.001, batch_size=32)
episode.params.set(learning_rate=0.0001)  # updates learning_rate only
```

## Loading from Config

```{code-block} python
:linenos:

# From JSON file
with open("config.json") as f:
    episode.params.set(**json.load(f))

# From argparse
episode.params.set(**vars(args))

# From dataclass
episode.params.set(**asdict(config))
```
