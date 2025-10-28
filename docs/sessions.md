# Sessions

Sessions are the core concept in Dreamlake. Each session represents a single experiment run or training session.

## Three Ways to Use Sessions

Dreamlake supports **three usage styles** to fit different workflows:

### 1. Decorator Style (Recommended for ML Training)

The `@dreamlake_session` decorator automatically manages session lifecycle:

```python
from dreamlake import dreamlake_session

@dreamlake_session(
    name="my-experiment",
    workspace="my-workspace",
    local_path="./experiments"
)
def train_model(session):
    """Session is automatically injected as a parameter"""
    session.log("Training started")
    session.parameters().set(learning_rate=0.001)

    for epoch in range(10):
        loss = train_epoch()
        session.track("loss").append(value=loss, epoch=epoch)

    return "Training complete!"

# Call the function - session is managed automatically
result = train_model()
```

**Benefits:**
- Clean separation of session setup and training logic
- Automatic session lifecycle management
- Session object injected into function kwargs
- Works great with existing training functions

### 2. Context Manager Style (Recommended for Scripts)

The `with` statement ensures automatic cleanup:

```python
from dreamlake import Session

# Local mode
with Session(
    name="my-experiment",
    workspace="my-workspace",
    local_path="./experiments"
) as session:
    session.log("Session started")
    session.parameters().set(learning_rate=0.001)
    # Session automatically closed on exit
```

**Benefits:**
- Automatic session opening and closing
- Exception-safe cleanup
- Pythonic and familiar pattern

### 3. Direct Instantiation (Advanced)

Manual control over session lifecycle:

```python
from dreamlake import Session

# Create session
session = Session(
    name="my-experiment",
    workspace="my-workspace",
    local_path="./experiments"
)

# Explicitly open
session.open()

try:
    session.log("Session started")
    session.parameters().set(learning_rate=0.001)
finally:
    # Explicitly close
    session.close()
```

**Benefits:**
- Fine-grained control
- Useful when session lifetime spans multiple scopes
- Required when session cannot fit in single `with` block

## Creating Sessions

### Basic Session Creation

### With Metadata

```python
with Session(
    name="resnet50-imagenet",
    workspace="computer-vision",
    local_path="./experiments",
    description="ResNet-50 training on ImageNet with new augmentation",
    tags=["resnet", "imagenet", "augmentation"],
    folder="/experiments/2025/resnet"
) as session:
    session.log("Training started")
```

### Remote Mode

```python
# Remote mode (requires API server) - with username
with Session(
    name="cloud-training",
    workspace="production",
    remote="https://cu3thurmv3.us-east-1.awsapprunner.com",
    user_name="your-username"
) as session:
    session.log("Cloud session started")

# Or with API key (advanced)
with Session(
    name="cloud-training",
    workspace="production",
    remote="https://cu3thurmv3.us-east-1.awsapprunner.com",
    api_key="your-api-key-here"
) as session:
    session.log("Cloud session started")
```

## Session Lifecycle

Sessions have three states:

1. **Created**: Session object exists but not opened
2. **Open**: Session is active and ready for operations
3. **Closed**: Session is finalized and no longer accepts operations

```python
from dreamlake import Session

# Manual lifecycle management
session = Session(name="test", workspace="test", local_path="./data")

# Open the session
session.open()
print(f"Session is open: {session._is_open}")  # True

# Do work
session.log("Working...")

# Close the session
session.close()
print(f"Session is open: {session._is_open}")  # False

# Using context manager (recommended)
with Session(name="test", workspace="test", local_path="./data") as session:
    print(f"Session is open: {session._is_open}")  # True
    session.log("Working...")
# Automatically closed here
print(f"Session is open: {session._is_open}")  # False
```

## Session Properties

Access session information:

```python
with Session(
    name="my-session",
    workspace="my-workspace",
    local_path="./data"
) as session:
    print(f"Name: {session.name}")
    print(f"Workspace: {session.workspace}")
    print(f"Folder: {session.folder}")
    print(f"Tags: {session.tags}")
    print(f"Description: {session.description}")
```

## Reusing Sessions

You can reuse existing sessions by creating a Session with the same name:

```python
# First run - creates session
with Session(name="long-training", workspace="ml", local_path="./data") as session:
    session.parameters().set(epoch=0, learning_rate=0.001)
    session.log("Starting epoch 1")
    session.track("loss").append(value=0.5, epoch=1)

# Second run - continues same session
with Session(name="long-training", workspace="ml", local_path="./data") as session:
    session.log("Continuing training from checkpoint")
    session.track("loss").append(value=0.3, epoch=2)
```

## Session Folders

Organize sessions hierarchically using folders:

```python
# Group related experiments
with Session(
    name="baseline",
    workspace="mnist",
    local_path="./data",
    folder="/experiments/2025/january"
) as session:
    session.log("Baseline experiment")

with Session(
    name="improved",
    workspace="mnist",
    local_path="./data",
    folder="/experiments/2025/january"
) as session:
    session.log("Improved experiment")
```

Storage structure:
```
.dreamlake/
└── mnist/
    ├── baseline/
    │   ├── logs.jsonl
    │   └── parameters.json
    └── improved/
        ├── logs.jsonl
        └── parameters.json
```

## Error Handling

Sessions handle errors gracefully:

```python
from dreamlake import Session

try:
    with Session(name="test", workspace="test", local_path="./data") as session:
        session.log("Starting risky operation")
        1 / 0  # Error!
except ZeroDivisionError:
    print("Error occurred, but session was properly closed")
```

## Session Operations

Once a session is open, you can perform various operations:

```python
with Session(name="demo", workspace="test", local_path="./data") as session:
    # Logging
    session.log("Hello", level="info")

    # Parameters
    session.parameters().set(lr=0.001, batch_size=32)

    # Tracks (metrics)
    session.track("loss").append(value=0.5)

    # Files
    session.files().upload("model.pth", path="/models")
```

## Write Protection

Sessions can be write-protected to prevent accidental modifications:

```python
# Note: Write protection is a server-side feature
# In local mode, it's not enforced

with Session(
    name="final-model",
    workspace="production",
    remote="https://cu3thurmv3.us-east-1.awsapprunner.com",
    api_key="key"
) as session:
    session.log("Training complete")
    # After this, you would call session.protect() via API
    # to make the session immutable
```

## Best Practices

1. **Always use context managers**: Use `with` statement for automatic cleanup
2. **Meaningful names**: Use descriptive session names
3. **Add metadata**: Include description and tags for easy searching
4. **Organize with folders**: Use folders to group related experiments
5. **One session per run**: Don't reuse the same session for different experiments

## Common Patterns

### Experiment Tracking

```python
def train_model(config):
    with Session(
        name=f"model-{config['architecture']}-run-{config['run_id']}",
        workspace="experiments",
        local_path="./results",
        tags=[config['architecture'], config['dataset']]
    ) as session:
        session.parameters().set(**config)
        session.log(f"Starting training with {config['architecture']}")

        for epoch in range(config['epochs']):
            loss = train_epoch()
            session.track("train_loss").append(value=loss, epoch=epoch)

        session.files().upload(f"model_{config['run_id']}.pth", path="/models")
```

### Resuming Training

```python
def resume_training(checkpoint_path):
    with Session(
        name="long-running-experiment",
        workspace="research",
        local_path="./data"
    ) as session:
        # Load checkpoint
        checkpoint = load_checkpoint(checkpoint_path)
        start_epoch = checkpoint['epoch']

        session.log(f"Resuming from epoch {start_epoch}")

        for epoch in range(start_epoch, total_epochs):
            loss = train_epoch()
            session.track("loss").append(value=loss, epoch=epoch)
```

## Next Steps

- [Logging](03-logging.md) - Learn about structured logging
- [Parameters](04-parameters.md) - Track hyperparameters
- [Tracks](05-tracks.md) - Time-series metrics tracking
