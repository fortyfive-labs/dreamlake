# Sessions

Sessions are the foundation of DreamLake. Each session represents a single experiment run, containing all your logs, parameters, metrics, and files.

## Three Usage Styles

**Context Manager** (recommended for most cases):

```{code-block} python
:linenos:

from dreamlake import Session

with Session(prefix="project/my-experiment",
        local_path=".dreamlake") as session:
    session.log("Training started")
    session.params.set(learning_rate=0.001)
    # Session automatically closed on exit
```

**Decorator** (clean for training functions):

```{code-block} python
:linenos:

from dreamlake import dreamlake_session

@dreamlake_session(name="my-experiment", workspace="project")
def train_model(session):
    session.log("Training started")
    session.params.set(learning_rate=0.001)

    for epoch in range(10):
        loss = train_epoch()
        session.track("train").append(loss=loss, epoch=epoch)

    return "Training complete!"

result = train_model()
```

**Direct** (manual control):

```{code-block} python
:linenos:

from dreamlake import Session

session = Session(prefix="project/my-experiment",
        local_path=".dreamlake")
session.open()

try:
    session.log("Training started")
    session.params.set(learning_rate=0.001)
finally:
    session.close()
```

## Local vs Remote Mode

**Local mode** - Zero setup, filesystem storage:

```{code-block} python
:linenos:

with Session(prefix="project/my-experiment",
    root="./experiments",
        local_path=".dreamlake"
) as session:
    session.log("Using local storage")
```

**Remote mode** - Team collaboration with server:

```{code-block} python
:linenos:

with Session(prefix="project/my-experiment",
    url="https://your-server.com",
    user_name="alice"
) as session:
    session.log("Using url server")
```

## Session Metadata

Add description, tags, and folders for organization:

```{code-block} python
:linenos:

with Session(prefix="computer-vision/resnet50-imagenet",
    root="./experiments",
    readme="ResNet-50 training with new augmentation",
    tags=["resnet", "imagenet", "baseline"],
    folder="/experiments/2025/resnet",
        local_path=".dreamlake"
) as session:
    session.log("Training started")
```

## Resuming Sessions

Sessions use **upsert behavior** - reopen by using the same name:

```{code-block} python
:linenos:

# First run
with Session(prefix="ml/long-training",
        local_path=".dreamlake") as session:
    session.log("Starting epoch 1")
    session.track("train").append(loss=0.5, epoch=1)

# Later - continues same session
with Session(prefix="ml/long-training",
        local_path=".dreamlake") as session:
    session.log("Resuming from checkpoint")
    session.track("train").append(loss=0.3, epoch=2)
```

## Available Operations

Once a session is open, you can use all DreamLake features:

```{code-block} python
:linenos:

with Session(prefix="test/demo",
        local_path=".dreamlake") as session:
    # Logging
    session.log("Training started", level="info")

    # Parameters
    session.params.set(lr=0.001, batch_size=32)

    # Metrics tracking
    session.track("train").append(loss=0.5, epoch=1)

    # File uploads
    session.files.upload("model.pth", path="/models")
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
