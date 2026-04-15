# Getting Started with DreamLake

This guide will help you get started with DreamLake.

## Installation

```bash
# Install from source (for now)
cd dreamlake_python_sdk
pip install -e .
```

## Core Concepts

### Episodes

A **Episode** represents a single experiment run or training episode. Episodes contain:
- Logs (structured logging)
- Parameters (hyperparameters and configuration)
- Tracks (time-series metrics like loss, accuracy)
- Files (models, datasets, artifacts)

### Workspaces

A **Workspace** is a container for organizing related episodes. Think of it as a project or team workspace.

### Local vs Remote Mode

DreamLake operates in two modes:

- **Local Mode**: Data stored in filesystem (`.dreamlake/` directory)
- **Remote Mode**: Data stored in MongoDB + S3 via API

## Your First Episode

DreamLake supports **three usage styles**. Choose the one that fits your workflow best:

### Style 1: Decorator (Recommended for ML Training)

Perfect for wrapping training functions:

```python
from dreamlake import dreamlake_episode

@dreamlake_episode(
    name="hello-dreamlake",
    workspace="tutorials",
    root="./my_experiments"
)
def my_first_experiment(episode):
    """Episode is automatically injected as a parameter"""
    # Log a message
    episode.log("Hello from DreamLake!", level="info")

    # Track a parameter
    episode.params.set(message="Hello World")

    print("Episode created successfully!")
    return "Done!"

# Run the experiment - episode is managed automatically
result = my_first_experiment()
```

### Style 2: Context Manager (Recommended for Scripts)

The most common and Pythonic approach:

```python
from dreamlake import Episode

# Create a episode in local mode
with Episode(prefix="tutorials/hello-dreamlake",
    root="./my_experiments",
        local_path=".dreamlake"
) as episode:
    # Log a message
    episode.log("Hello from DreamLake!", level="info")

    # Track a parameter
    episode.params.set(message="Hello World")

    print("Episode created successfully!")
    print(f"Data stored in: {episode._storage.root_path}")
```

### Style 3: Direct Instantiation (Advanced)

For fine-grained control:

```python
from dreamlake import Episode

# Create a episode
episode = Episode(prefix="tutorials/hello-dreamlake",
    root="./my_experiments",
        local_path=".dreamlake"
)

# Explicitly open
episode.open()

try:
    # Log a message
    episode.log("Hello from DreamLake!", level="info")

    # Track a parameter
    episode.params.set(message="Hello World")

    print("Episode created successfully!")
finally:
    # Explicitly close
    episode.close()
```

Save this as `hello_dreamlake.py` and run it:

```bash
python hello_dreamlake.py
```

You should see:
```
Episode created successfully!
Data stored in: ./my_experiments
```

## What Just Happened?

1. **Episode Created**: A new episode named "hello-dreamlake" was created in the "tutorials" workspace
2. **Log Written**: A log message was written to `.dreamlake/tutorials/hello-dreamlake/logs.jsonl`
3. **Parameter Saved**: The parameter was saved to `.dreamlake/tutorials/hello-dreamlake/parameters.json`
4. **Auto-Closed**: The `with` statement automatically closed the episode

Note: Track data (metrics) is stored in msgpack-lines format in `.dreamlake/tutorials/hello-dreamlake/tracks/*/data.msgpack` files.

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

## Episode Context Manager

DreamLake uses Python's context manager pattern (`with` statement) to ensure proper cleanup:

```python
# ✓ Good - Automatic cleanup
with Episode(prefix="test/my-episode", root="./data",
        local_path=".dreamlake") as episode:
    episode.log("Training started")
    # ... do work ...
# Episode automatically closed here

# ✗ Manual cleanup (not recommended)
episode = Episode(prefix="test/my-episode", root="./data",
        local_path=".dreamlake")
episode.open()
try:
    episode.log("Training started")
finally:
    episode.close()
```

## Episode Metadata

You can add metadata to your episodes:

```python
with Episode(prefix="computer-vision/mnist-baseline",
    root="./experiments",
    readme="Baseline CNN for MNIST classification",
    tags=["mnist", "cnn", "baseline"],
    folder="/experiments/mnist",
        local_path=".dreamlake"
) as episode:
    episode.log("Episode created with metadata")
```

## Error Handling

Episodes handle errors gracefully:

```python
from dreamlake import Episode

try:
    with Episode(prefix="test/test-episode",
        root="./data",
        local_path=".dreamlake"
    ) as episode:
        episode.log("Starting work...")
        # Your code here
        raise Exception("Something went wrong!")
except Exception as e:
    print(f"Error occurred: {e}")
    # Episode is still properly closed
```

## Next Steps

Now that you understand the basics, explore:
- [Episodes](episodes.md) - Advanced episode management
- [Logging](logging.md) - Structured logging
- [Parameters](parameters.md) - Parameter tracking
- [Tracks](tracks.md) - Time-series metrics
- [Files](files.md) - File uploads

## Quick Reference

### Three Usage Styles

```python
from dreamlake import Episode, dreamlake_episode

# ========================================
# Style 1: Decorator (ML Training)
# ========================================
@dreamlake_episode(
    name="episode-name",
    workspace="workspace-name",
    root="./path/to/data"
)
def train(episode):
    episode.log("Training...")

train()  # Episode managed automatically

# ========================================
# Style 2: Context Manager (Scripts)
# ========================================
# Local mode (filesystem)
with Episode(prefix="workspace-name/episode-name",
    root="./path/to/data",
        local_path=".dreamlake"
) as episode:
    pass

# Remote mode (API + S3) - with username
with Episode(prefix="workspace-name/episode-name",
    url="https://cu3thurmv3.us-east-1.awsapprunner.com",
    user_name="your-username"
) as episode:
    pass

# Remote mode (API + S3) - with API key (advanced)
with Episode(prefix="workspace-name/episode-name",
    url="https://cu3thurmv3.us-east-1.awsapprunner.com",
    api_key="your-api-key"
) as episode:
    pass

# ========================================
# Style 3: Direct Instantiation (Advanced)
# ========================================
episode = Episode(prefix="workspace-name/episode-name",
    root="./path/to/data",
        local_path=".dreamlake"
)
episode.open()
try:
    # Do work
    pass
finally:
    episode.close()
```

### All Styles Work With Remote Mode

```python
# Decorator + Remote
@dreamlake_episode(
    name="episode-name",
    workspace="workspace-name",
    url="https://cu3thurmv3.us-east-1.awsapprunner.com",
    user_name="your-username"
)
def train(episode):
    pass
```

**Note**: Using `user_name` is simpler for development - it automatically generates an API key from your username.

---

## See Also

Now that you know the basics, explore these guides:

- **[Architecture](architecture.md)** - Understand how DreamLake works internally
- **[Deployment Guide](deployment.md)** - Deploy your own DreamLake server
- **[API Quick Reference](api-quick-reference.md)** - Cheat sheet for common patterns
- **[Complete Examples](complete-examples.md)** - End-to-end ML workflows
- **[FAQ & Troubleshooting](faq.md)** - Common questions and solutions

**Feature-specific guides:**
- [Episodes](episodes.md) - Episode lifecycle and management
- [Logging](logging.md) - Structured logging with levels
- [Parameters](parameters.md) - Hyperparameter tracking
- [Tracks](tracks.md) - Time-series metrics
- [Files](files.md) - File upload and management
