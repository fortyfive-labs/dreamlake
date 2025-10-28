# Getting Started with Dreamlake

This guide will help you get started with Dreamlake.

## Installation

```bash
# Install from source (for now)
cd dreamlake_python_sdk
pip install -e .
```

## Core Concepts

### Sessions

A **Session** represents a single experiment run or training session. Sessions contain:
- Logs (structured logging)
- Parameters (hyperparameters and configuration)
- Tracks (time-series metrics like loss, accuracy)
- Files (models, datasets, artifacts)

### Workspaces

A **Workspace** is a container for organizing related sessions. Think of it as a project or team workspace.

### Local vs Remote Mode

Dreamlake operates in two modes:

- **Local Mode**: Data stored in filesystem (`.dreamlake/` directory)
- **Remote Mode**: Data stored in MongoDB + S3 via API

## Your First Session

Dreamlake supports **three usage styles**. Choose the one that fits your workflow best:

### Style 1: Decorator (Recommended for ML Training)

Perfect for wrapping training functions:

```python
from dreamlake import dreamlake_session

@dreamlake_session(
    name="hello-dreamlake",
    workspace="tutorials",
    local_path="./my_experiments"
)
def my_first_experiment(session):
    """Session is automatically injected as a parameter"""
    # Log a message
    session.log("Hello from Dreamlake!", level="info")

    # Track a parameter
    session.parameters().set(message="Hello World")

    print("Session created successfully!")
    return "Done!"

# Run the experiment - session is managed automatically
result = my_first_experiment()
```

### Style 2: Context Manager (Recommended for Scripts)

The most common and Pythonic approach:

```python
from dreamlake import Session

# Create a session in local mode
with Session(
    name="hello-dreamlake",
    workspace="tutorials",
    local_path="./my_experiments"
) as session:
    # Log a message
    session.log("Hello from Dreamlake!", level="info")

    # Track a parameter
    session.parameters().set(message="Hello World")

    print("Session created successfully!")
    print(f"Data stored in: {session._storage.root_path}")
```

### Style 3: Direct Instantiation (Advanced)

For fine-grained control:

```python
from dreamlake import Session

# Create a session
session = Session(
    name="hello-dreamlake",
    workspace="tutorials",
    local_path="./my_experiments"
)

# Explicitly open
session.open()

try:
    # Log a message
    session.log("Hello from Dreamlake!", level="info")

    # Track a parameter
    session.parameters().set(message="Hello World")

    print("Session created successfully!")
finally:
    # Explicitly close
    session.close()
```

Save this as `hello_dreamlake.py` and run it:

```bash
python hello_dreamlake.py
```

You should see:
```
Session created successfully!
Data stored in: ./my_experiments
```

## What Just Happened?

1. **Session Created**: A new session named "hello-dreamlake" was created in the "tutorials" workspace
2. **Log Written**: A log message was written to `.dreamlake/tutorials/hello-dreamlake/logs.jsonl`
3. **Parameter Saved**: The parameter was saved to `.dreamlake/tutorials/hello-dreamlake/parameters.json`
4. **Auto-Closed**: The `with` statement automatically closed the session

## Inspecting Your Data

Let's check what was created:

```bash
# View the directory structure
tree ./my_experiments/.dreamlake

# View logs
cat ./my_experiments/.dreamlake/tutorials/hello-dreamlake/logs.jsonl

# View parameters
cat ./my_experiments/.dreamlake/tutorials/hello-dreamlake/parameters.json
```

## Session Context Manager

Dreamlake uses Python's context manager pattern (`with` statement) to ensure proper cleanup:

```python
# ✓ Good - Automatic cleanup
with Session(name="my-session", workspace="test", local_path="./data") as session:
    session.log("Training started")
    # ... do work ...
# Session automatically closed here

# ✗ Manual cleanup (not recommended)
session = Session(name="my-session", workspace="test", local_path="./data")
session.open()
try:
    session.log("Training started")
finally:
    session.close()
```

## Session Metadata

You can add metadata to your sessions:

```python
with Session(
    name="mnist-baseline",
    workspace="computer-vision",
    local_path="./experiments",
    description="Baseline CNN for MNIST classification",
    tags=["mnist", "cnn", "baseline"],
    folder="/experiments/mnist"
) as session:
    session.log("Session created with metadata")
```

## Error Handling

Sessions handle errors gracefully:

```python
from dreamlake import Session

try:
    with Session(
        name="test-session",
        workspace="test",
        local_path="./data"
    ) as session:
        session.log("Starting work...")
        # Your code here
        raise Exception("Something went wrong!")
except Exception as e:
    print(f"Error occurred: {e}")
    # Session is still properly closed
```

## Next Steps

Now that you understand the basics, explore:
- [Sessions](sessions.md) - Advanced session management
- [Logging](logging.md) - Structured logging
- [Parameters](parameters.md) - Parameter tracking
- [Tracks](tracks.md) - Time-series metrics
- [Files](files.md) - File uploads

## Quick Reference

### Three Usage Styles

```python
from dreamlake import Session, dreamlake_session

# ========================================
# Style 1: Decorator (ML Training)
# ========================================
@dreamlake_session(
    name="session-name",
    workspace="workspace-name",
    local_path="./path/to/data"
)
def train(session):
    session.log("Training...")

train()  # Session managed automatically

# ========================================
# Style 2: Context Manager (Scripts)
# ========================================
# Local mode (filesystem)
with Session(
    name="session-name",
    workspace="workspace-name",
    local_path="./path/to/data"
) as session:
    pass

# Remote mode (API + S3) - with username
with Session(
    name="session-name",
    workspace="workspace-name",
    remote="https://cu3thurmv3.us-east-1.awsapprunner.com",
    user_name="your-username"
) as session:
    pass

# Remote mode (API + S3) - with API key (advanced)
with Session(
    name="session-name",
    workspace="workspace-name",
    remote="https://cu3thurmv3.us-east-1.awsapprunner.com",
    api_key="your-api-key"
) as session:
    pass

# ========================================
# Style 3: Direct Instantiation (Advanced)
# ========================================
session = Session(
    name="session-name",
    workspace="workspace-name",
    local_path="./path/to/data"
)
session.open()
try:
    # Do work
    pass
finally:
    session.close()
```

### All Styles Work With Remote Mode

```python
# Decorator + Remote
@dreamlake_session(
    name="session-name",
    workspace="workspace-name",
    remote="https://cu3thurmv3.us-east-1.awsapprunner.com",
    user_name="your-username"
)
def train(session):
    pass
```

**Note**: Using `user_name` is simpler for development - it automatically generates an API key from your username.
