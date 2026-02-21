# Dreamlake Python SDK Examples

This directory contains runnable examples demonstrating Dreamlake features.

## Running Examples

All examples work with local mode (no server needed):

```bash
# Run from the project root with proper PYTHONPATH
PYTHONPATH=./src python docs/examples/three_usage_styles.py
PYTHONPATH=./src python docs/examples/01_basic_session.py
PYTHONPATH=./src python docs/examples/02_logging_example.py
PYTHONPATH=./src python docs/examples/03_parameters_example.py
PYTHONPATH=./src python docs/examples/04_tracks_example.py
PYTHONPATH=./src python docs/examples/05_files_example.py
PYTHONPATH=./src python docs/examples/06_complete_training.py
```

## Examples Overview

| Example | Description | Features |
|---------|-------------|----------|
| `three_usage_styles.py` | **NEW!** Three ways to use sessions | Decorator, context manager, direct instantiation |
| `01_basic_session.py` | Your first Dreamlake session | Session creation, logging, parameters |
| `02_logging_example.py` | Structured logging | Log levels, metadata, progress logging |
| `03_parameters_example.py` | Hyperparameters tracking | Simple params, nested params, updates |
| `04_tracks_example.py` | Time-series metrics | Tracks, batch append, reading data, stats |
| `05_files_example.py` | File uploads | Upload models, configs, results |
| `06_complete_training.py` | End-to-end training | All features combined |

## What Gets Created

All examples create data in `tutorial_data/.dreamlake/tutorials/` directory:

```
tutorial_data/.dreamlake/tutorials/
├── hello-dreamlake/
│   ├── logs.jsonl
│   └── parameters.json
├── logging-demo/
│   └── logs.jsonl
├── parameters-demo/
│   └── parameters.json
├── tracks-demo/
│   ├── logs.jsonl
│   ├── parameters.json
│   └── tracks/
│       ├── train_loss/
│       │   ├── data.jsonl
│       │   └── metadata.json
│       └── ...
└── ...
```

## Exploring Your Data

After running examples, explore the generated data:

```bash
# View logs
cat tutorial_data/.dreamlake/tutorials/hello-dreamlake/logs.jsonl

# View parameters
cat tutorial_data/.dreamlake/tutorials/parameters-demo/parameters.json

# View track data
cat tutorial_data/.dreamlake/tutorials/tracks-demo/tracks/train_loss/data.jsonl

# List all sessions
ls tutorial_data/.dreamlake/tutorials/
```

## Clean Up

Remove all tutorial data:

```bash
rm -rf tutorial_data/
```

## API Methods Reference

Quick reference for Dreamlake API:

```python
from dreamlake import Session, dreamlake_session

# ==============================================
# Style 1: Decorator (Best for ML Training)
# ==============================================
@dreamlake_session(name="...", workspace="...", local_path="...")
def train(session):
    session.log("Training started")
    session.parameters().set(lr=0.001)
    session.track("loss").append(value=0.5)

train()

# ==============================================
# Style 2: Context Manager (Best for Scripts)
# ==============================================
# Local mode
with Session(name="...", workspace="...", local_path="...") as session:
    # Logging
    session.log("message", level="info", metadata={...})

    # Parameters
    session.parameters().set(learning_rate=0.001, batch_size=32)

    # Tracks
    session.track("name").append(value=0.5, epoch=1)
    session.track("name").append_batch([{...}, {...}])
    session.track("name").read(start_index=0, limit=100)
    session.track("name").stats()
    session.track("name").list_all()

    # Files
    session.files().upload("file.txt", path="/models", description="...")
    session.files().list()

# Remote mode (with username)
with Session(name="...", workspace="...", remote="https://cu3thurmv3.us-east-1.awsapprunner.com", user_name="username") as session:
    # Same API as above
    pass

# ==============================================
# Style 3: Direct Instantiation (Advanced)
# ==============================================
session = Session(name="...", workspace="...", local_path="...")
session.open()
try:
    session.log("message")
finally:
    session.close()
```

## Next Steps

- Read the full tutorial: [docs/README.md](../README.md)
- Learn about features:
  - [Getting Started](../01-getting-started.md)
  - [Sessions](../02-sessions.md)
  - [Logging](../03-logging.md)
  - [Parameters](../04-parameters.md)
  - [Tracks](../05-tracks.md)
  - [Files](../06-files.md)
