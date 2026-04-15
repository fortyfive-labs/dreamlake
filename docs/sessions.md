# Episodes

Episodes are the foundation of DreamLake. Each episode represents a single experiment run, containing all your logs, parameters, metrics, and files.

## Three Usage Styles

**Context Manager** (recommended for most cases):

```{code-block} python
:linenos:

from dreamlake import Episode

with Episode(prefix="project/my-experiment",
        local_path=".dreamlake") as episode:
    episode.log("Training started")
    episode.params.set(learning_rate=0.001)
    # Episode automatically closed on exit
```

**Decorator** (clean for training functions):

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

    return "Training complete!"

result = train_model()
```

**Direct** (manual control):

```{code-block} python
:linenos:

from dreamlake import Episode

episode = Episode(prefix="project/my-experiment",
        local_path=".dreamlake")
episode.open()

try:
    episode.log("Training started")
    episode.params.set(learning_rate=0.001)
finally:
    episode.close()
```

## Local vs Remote Mode

**Local mode** - Zero setup, filesystem storage:

```{code-block} python
:linenos:

with Episode(prefix="project/my-experiment",
    root="./experiments",
        local_path=".dreamlake"
) as episode:
    episode.log("Using local storage")
```

**Remote mode** - Team collaboration with server:

```{code-block} python
:linenos:

with Episode(prefix="project/my-experiment",
    url="https://your-server.com",
    user_name="alice"
) as episode:
    episode.log("Using url server")
```

## Episode Metadata

Add description, tags, and folders for organization:

```{code-block} python
:linenos:

with Episode(prefix="computer-vision/resnet50-imagenet",
    root="./experiments",
    readme="ResNet-50 training with new augmentation",
    tags=["resnet", "imagenet", "baseline"],
    folder="/experiments/2025/resnet",
        local_path=".dreamlake"
) as episode:
    episode.log("Training started")
```

## Resuming Episodes

Episodes use **upsert behavior** - reopen by using the same name:

```{code-block} python
:linenos:

# First run
with Episode(prefix="ml/long-training",
        local_path=".dreamlake") as episode:
    episode.log("Starting epoch 1")
    episode.track("train").append(loss=0.5, epoch=1)

# Later - continues same episode
with Episode(prefix="ml/long-training",
        local_path=".dreamlake") as episode:
    episode.log("Resuming from checkpoint")
    episode.track("train").append(loss=0.3, epoch=2)
```

## Available Operations

Once a episode is open, you can use all DreamLake features:

```{code-block} python
:linenos:

with Episode(prefix="test/demo",
        local_path=".dreamlake") as episode:
    # Logging
    episode.log("Training started", level="info")

    # Parameters
    episode.params.set(lr=0.001, batch_size=32)

    # Metrics tracking
    episode.track("train").append(loss=0.5, epoch=1)

    # File uploads
    episode.files.upload("model.pth", path="/models")
```

## Storage Structure

**Local mode** creates a directory structure:

```
./experiments/
└── project/
    └── my-experiment/
        ├── logs/
        │   └── logs.jsonl
        ├── parameters.json
        ├── tracks/
        │   └── loss/
        │       └── data.msgpack
        └── files/
            ├── .files_metadata.json
            └── models/
                └── {file_id}/
                    └── model.pth
```

**Remote mode** stores data in MongoDB + S3 on your server.

---

**Next:** Learn about [Logging](logging.md) to track events and progress.
