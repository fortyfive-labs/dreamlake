# DreamLake Examples

This directory contains runnable examples demonstrating DreamLake features.

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
PYTHONPATH=./src python docs/examples/07_time_range_queries.py
PYTHONPATH=./src python docs/examples/08_batch_columnar_format.py
PYTHONPATH=./src python docs/examples/timestamp_sync_example.py
```

## Examples Overview

| Example | Description | Features |
|---------|-------------|----------|
| `three_usage_styles.py` | **NEW!** Three ways to use sessions | Decorator, context manager, direct instantiation |
| `01_basic_session.py` | Your first DreamLake session | Session creation, logging, parameters |
| `02_logging_example.py` | Structured logging | Log levels, metadata, progress logging |
| `03_parameters_example.py` | Hyperparameters tracking | Simple params, nested params, updates |
| `04_tracks_example.py` | Time-series metrics | Tracks, batch append, reading data, stats |
| `05_files_example.py` | File uploads | Upload models, configs, results |
| `06_complete_training.py` | End-to-end training | All features combined, batch metrics |
| `07_time_range_queries.py` | **NEW!** Time-based queries | MCAP-like API, time ranges, reverse iteration |
| `08_batch_columnar_format.py` | **NEW!** Batch & columnar format | Efficient batch writes, performance comparison |
| `timestamp_sync_example.py` | Timestamp synchronization | Multi-modal sync with _ts=-1, merging |

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
│       │   ├── data.msgpack    # Track data (msgpack-lines format)
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

# View track data (requires msgpack tools)
python -c "import msgpack; [print(obj) for obj in msgpack.Unpacker(open('tutorial_data/.dreamlake/tutorials/tracks-demo/tracks/train_loss/data.msgpack', 'rb'), raw=False)]"

# List all sessions
ls tutorial_data/.dreamlake/tutorials/
```

## Clean Up

Remove all tutorial data:

```bash
rm -rf tutorial_data/
```

## Example Files

### Three Usage Styles (`three_usage_styles.py`)

Demonstrates all three ways to use DreamLake sessions:
- **Decorator style**: Best for ML training functions
- **Context manager style**: Best for scripts
- **Direct instantiation**: Advanced usage with manual lifecycle management

### Basic Session (`01_basic_session.py`)

Your first DreamLake session showing:
- Creating a local session
- Basic logging
- Setting parameters

### Logging Example (`02_logging_example.py`)

Comprehensive logging demonstration:
- Different log levels (debug, info, warn, error, fatal)
- Adding metadata to logs
- Progress logging
- Structured logging patterns

### Parameters Example (`03_parameters_example.py`)

Hyperparameter tracking:
- Simple key-value parameters
- Nested parameters with dot notation
- Updating parameters
- Parameter organization

### Tracks Example (`04_tracks_example.py`)

Time-series metrics tracking:
- Creating tracks
- Single and batch data appends
- Flexible schemas
- Reading track data
- Getting statistics
- Listing all tracks

### Files Example (`05_files_example.py`)

File upload and management:
- Uploading files
- Adding descriptions and tags
- Using prefixes for organization
- Adding metadata to files
- Listing uploaded files

### Complete Training Example (`06_complete_training.py`)

End-to-end ML training simulation that combines all features:
- Session creation
- Parameter configuration
- Progress logging
- Metric tracking (loss, accuracy, learning rate)
- File uploads (checkpoints, final model, results)
- Complete workflow demonstration

### Time Range Queries Example (`07_time_range_queries.py`)

**NEW!** Demonstrates the MCAP-like time-based query API:
- Time range queries with `start_time` and `end_time`
- Reverse iteration for getting recent data
- Open-ended queries (start only or end only)
- Synchronized multi-modal data queries
- Comparison of index-based vs time-based queries
- Use cases for robotics and real-time monitoring

### Batch Appending & Columnar Format Example (`08_batch_columnar_format.py`)

**NEW!** Comprehensive demonstration of efficient batch data storage:
- Row vs columnar storage format comparison
- Large batch efficiency (1000+ points)
- Mixed single and batch appends
- Complex/nested data structures in batches
- Performance benchmarks showing 5-10x speedup
- Transparent format handling when reading
- Best practices for batch appending

See [README_COLUMNAR_FORMAT.md](examples/README_COLUMNAR_FORMAT.md) for detailed documentation.

### Timestamp Synchronization Example (`timestamp_sync_example.py`)

Multi-modal data synchronization using timestamps:
- Using `_ts=-1` for timestamp inheritance
- Synchronizing pose, images, and sensor data
- Explicit timestamp control
- Multi-field merging with same timestamp
- Cross-track timestamp inheritance

## Next Steps

- Read the full tutorial documentation
- Explore the [API Quick Reference](api-quick-reference.md)
- Learn about specific features:
  - [Getting Started](getting-started.md)
  - [Sessions](sessions.md)
  - [Logging](logging.md)
  - [Parameters](parameters.md)
  - [Tracks](tracks.md)
  - [Files](files.md)
