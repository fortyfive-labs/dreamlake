# Episodes

Each episode represents a single experiment run containing logs, parameters, metrics, and files.

## Usage Styles

**Context manager** (recommended):

```{code-block} python
:linenos:

from dreamlake import Episode

with Episode(prefix="project/my-experiment",
        local_path=".dreamlake") as episode:
    episode.log("Training started")
    episode.params.set(learning_rate=0.001)
```

**Decorator**:

```{code-block} python
:linenos:

from dreamlake import dreamlake_episode

@dreamlake_episode(name="my-experiment", workspace="project")
def train_model(episode):
    episode.log("Training started")
    episode.params.set(learning_rate=0.001)

    for epoch in range(10):
        loss = train_epoch()
        episode.track("train").append(loss=loss, epoch=epoch)

result = train_model()
```

**Direct** (manual lifecycle):

```{code-block} python
:linenos:

episode = Episode(prefix="project/my-experiment", local_path=".dreamlake")
episode.open()
try:
    episode.log("Training started")
finally:
    episode.close()
```

## Metadata

```{code-block} python
:linenos:

with Episode(prefix="cv/resnet50-imagenet",
    root="./experiments",
    readme="ResNet-50 training with new augmentation",
    tags=["resnet", "imagenet", "baseline"],
    folder="/experiments/2025/resnet",
    local_path=".dreamlake"
) as episode:
    pass
```

## Resuming

Episodes use upsert behavior — reopen by using the same name:

```{code-block} python
:linenos:

with Episode(prefix="ml/long-training", local_path=".dreamlake") as episode:
    episode.track("train").append(loss=0.5, epoch=1)

# Later — continues same episode
with Episode(prefix="ml/long-training", local_path=".dreamlake") as episode:
    episode.track("train").append(loss=0.3, epoch=2)
```

## Storage

**Local mode** creates:

```
./experiments/project/my-experiment/
├── logs/logs.jsonl
├── parameters.json
├── tracks/loss/data.msgpack
└── files/.files_metadata.json
```

**Remote mode** stores data in MongoDB + S3.
