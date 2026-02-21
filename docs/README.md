# Dreamlake Python SDK Tutorial

Welcome to the Dreamlake Python SDK tutorial! Dreamlake is an ML experiment tracking system that helps you organize, track, and analyze your machine learning experiments.

## Table of Contents

1. [Getting Started](01-getting-started.md) - Installation and basic concepts
2. [Sessions](02-sessions.md) - Creating and managing sessions
3. [Logging](03-logging.md) - Structured logging for your experiments
4. [Parameters](04-parameters.md) - Tracking hyperparameters and configurations
5. [Tracks](05-tracks.md) - Time-series metrics tracking
6. [Files](06-files.md) - Uploading and managing experiment artifacts
7. [Local vs Remote Mode](07-local-vs-remote.md) - Understanding the two modes
8. [Complete Examples](08-complete-examples.md) - Full end-to-end examples

## Quick Start
```shell
pip install dreamlake
```

### Three Usage Styles

Dreamlake supports **three styles** to fit your workflow:

#### 1. Decorator Style (Recommended for ML Training)

```python
from dreamlake import dreamlake_session

@dreamlake_session(
    name="my-experiment",
    workspace="my-workspace",
    local_path="./experiments"
)
def train_model(session):
    # Session is automatically injected
    session.log("Starting training...", level="info")
    session.parameters().set(learning_rate=0.001, batch_size=32)

    for epoch in range(10):
        train_loss = train_epoch()
        session.track("train_loss").append(value=train_loss, epoch=epoch)

    session.files().upload("model.pth", path="/models")

# Call function - session managed automatically
train_model()
```

#### 2. Context Manager Style (Recommended for Scripts)

```python
from dreamlake import Session

# Create a session (local mode)
with Session(
    name="my-experiment",
    workspace="my-workspace",
    local_path="./experiments"
) as session:
    # Log messages
    session.log("Starting training...", level="info")

    # Track parameters
    session.parameters().set(learning_rate=0.001, batch_size=32)

    # Track metrics over time
    for epoch in range(10):
        train_loss = train_model()
        session.track("train_loss").append(value=train_loss, epoch=epoch)

    # Upload files
    session.files().upload("model.pth", path="/models")
```

#### 3. Direct Instantiation (Advanced)

```python
from dreamlake import Session

session = Session(
    name="my-experiment",
    workspace="my-workspace",
    local_path="./experiments"
)

session.open()
try:
    session.log("Starting training...", level="info")
    session.parameters().set(learning_rate=0.001, batch_size=32)
finally:
    session.close()
```

### Remote Mode

All styles work with remote mode (just change parameters):

```python
# Decorator
@dreamlake_session(
    name="my-experiment",
    workspace="my-workspace",
    remote="https://cu3thurmv3.us-east-1.awsapprunner.com",
    user_name="your-username"
)
def train(session):
    pass

# Context manager
with Session(
    name="my-experiment",
    workspace="my-workspace",
    remote="https://cu3thurmv3.us-east-1.awsapprunner.com",
    user_name="your-username"
) as session:
    pass
```

## Features

- **Dual Mode Operation**: Work locally (filesystem) or remotely (API + S3)
- **Structured Logging**: Organize logs by level with metadata
- **Parameter Tracking**: Flat key-value storage with dot-notation support
- **Time-Series Metrics**: Track training metrics, losses, and custom measurements
- **File Management**: Upload models, datasets, and artifacts (up to 5GB)
- **Context Manager**: Automatic session lifecycle management
- **Flexible Schemas**: Define your own data structures for tracks

## Examples Directory

All runnable examples are in the [examples/](examples/) directory:
- [examples/basic_session.py](examples/basic_session.py)
- [examples/logging_example.py](examples/logging_example.py)
- [examples/parameters_example.py](examples/parameters_example.py)
- [examples/tracks_example.py](examples/tracks_example.py)
- [examples/files_example.py](examples/files_example.py)
- [examples/complete_training.py](examples/complete_training.py)

## Running Examples

All examples can be run directly:

```bash
# Local mode examples (no server needed)
python docs/examples/basic_session.py
python docs/examples/logging_example.py
python docs/examples/complete_training.py

# Remote mode examples (requires server running)
python docs/examples/remote_session.py
```

## Next Steps

Start with [Getting Started](01-getting-started.md) to learn the basics, then explore the specific features you need.
