# Local vs Remote Mode

DreamLake operates in two modes: **Local** (filesystem) and **Remote** (API + Cloud storage). Understanding the differences helps you choose the right mode for your use case.

## Local Mode

Local mode stores all data on your local filesystem in a `.dreamlake/` directory.

### When to Use Local Mode

- **Development**: Rapid prototyping and testing
- **Single machine**: Running on your local laptop/workstation
- **Offline work**: No internet connection required
- **Quick experiments**: Simple experiments that don't need cloud storage
- **Privacy**: Keep all data local

### Creating a Local Episode

```python
from dreamlake import Episode

with Episode(prefix="my-workspace/my-experiment",
    root="./experiments"  # Required for local mode,
        local_path=".dreamlake"
) as episode:
    episode.log("Running in local mode")
    episode.params.set(batch_size=32)
    episode.track("train").append(loss=0.5)
    episode.files.upload("model.pth", path="/models")
```

### Local Storage Structure

```
./experiments/.dreamlake/
└── my-workspace/
    └── my-experiment/
        ├── logs.jsonl              # Log entries (JSONL format)
        ├── parameters.json         # Parameters (JSON)
        ├── tracks/                 # Time-series data
        │   └── loss/
        │       ├── data.msgpack    # Track data points (msgpack-lines format)
        │       └── metadata.json   # Track metadata
        └── files/                  # Uploaded files
            ├── .files_metadata.json    # File metadata
            └── models/                 # Prefix folder
                └── {file_id}/          # Unique file ID
                    └── model.pth       # Actual file
```

### Advantages of Local Mode

- **Fast**: No network latency
- **Simple**: No server setup required
- **Portable**: Copy `.dreamlake/` directory to move experiments
- **No costs**: No cloud storage fees

### Disadvantages of Local Mode

- **No sharing**: Can't easily share with team
- **Single machine**: Tied to one computer
- **No web UI**: Can't browse experiments in browser
- **Limited scale**: Large experiments may fill disk

## Remote Mode

Remote mode stores data in MongoDB (metadata) and S3 (large files), accessed via API.

### When to Use Remote Mode

- **Team collaboration**: Share experiments with team
- **Cloud training**: Training on cloud GPUs
- **Large scale**: Many experiments or large files
- **Web UI**: Browse experiments in web interface
- **Production**: Production ML workflows

### Creating a Remote Episode

```python
from dreamlake import Episode

# With username (simpler for development)
with Episode(prefix="my-workspace/my-experiment",
    url="https://cu3thurmv3.us-east-1.awsapprunner.com",     # API endpoint
    user_name="your-username"            # Authentication
) as episode:
    episode.log("Running in url mode")
    episode.params.set(batch_size=32)
    episode.track("train").append(loss=0.5)
    episode.files.upload("model.pth", path="/models")

# Or with API key (advanced)
with Episode(prefix="my-workspace/my-experiment",
    url="https://cu3thurmv3.us-east-1.awsapprunner.com",     # API endpoint
    api_key="your-api-key-here"          # Authentication
) as episode:
    episode.log("Running in url mode")
    episode.params.set(batch_size=32)
    episode.track("train").append(loss=0.5)
    episode.files.upload("model.pth", path="/models")
```

### Remote Storage Architecture

```
MongoDB:
- Episode metadata
- Logs (recent)
- Parameters
- Track metadata
- File metadata

S3:
- Files (models, datasets, etc.)
- Archived logs (old logs moved from MongoDB)
- Track chunks (old track data moved from MongoDB)
```

### Advantages of Remote Mode

- **Collaborative**: Share with team members
- **Scalable**: Handle large volumes of data
- **Accessible**: Access from anywhere
- **Durable**: Data backed up in cloud
- **Web UI**: View experiments in browser

### Disadvantages of Remote Mode

- **Requires server**: Need to run API server
- **Network dependency**: Requires internet connection
- **Costs**: S3 storage and MongoDB costs
- **Latency**: Network requests slower than local

## Comparison Table

| Feature | Local Mode | Remote Mode |
|---------|-----------|-------------|
| Setup | None | Requires server |
| Speed | Fast (local disk) | Slower (network) |
| Collaboration | No | Yes |
| Scalability | Limited by disk | Unlimited (S3) |
| Cost | Free | Cloud storage costs |
| Offline work | Yes | No |
| Web UI | No | Yes |
| Data backup | Manual | Automatic |

## Switching Between Modes

You can't directly convert between modes, but you can export/import data.

### Export from Local

```python
# Local data is in .dreamlake/ directory
# Copy the entire directory to back up or share
```

### Start Local, Move to Remote Later

```python
# Development (local)
with Episode(prefix="dev/experiment", root="./data",
        local_path=".dreamlake") as episode:
    # Develop your code...
    pass

# Production (url)
with Episode(prefix="prod/experiment", url="https://api", api_key="key") as episode:
    # Run at scale...
    pass
```

## Environment Variables

Set default mode using environment variables:

```bash
# Local mode
export DREAMLAKE_LOCAL_PATH="./experiments"

# Remote mode
export DREAMLAKE_API_URL="https://api.dreamlake.ai"
export DREAMLAKE_API_KEY="your-api-key"
```

Then in code:

```python
import os
from dreamlake import Episode

# Will use environment variables
with Episode(prefix="my-workspace/experiment",
    local_path=os.getenv("DREAMLAKE_LOCAL_PATH"),
    url=os.getenv("DREAMLAKE_API_URL"),
    api_key=os.getenv("DREAMLAKE_API_KEY")
) as episode:
    pass
```

## Hybrid Approach

Run locally during development, url in production:

```python
import os
from dreamlake import Episode

# Check if running in production
is_production = os.getenv("ENVIRONMENT") == "production"

if is_production:
    # Remote mode for production
    episode_config = {
        "url": "https://api.dreamlake.ai",
        "api_key": os.getenv("DREAMLAKE_API_KEY")
    }
else:
    # Local mode for development
    episode_config = {
        "local_path": "./experiments"
    }

with Episode(prefix="ml/experiment", **episode_config,
        local_path=".dreamlake") as episode:
    episode.log("Starting training")
    # Your training code...
```

## Best Practices

1. **Development**: Start with local mode for fast iteration
2. **Production**: Use url mode for team collaboration
3. **Backup**: Regularly back up `.dreamlake/` in local mode
4. **Environment vars**: Use environment variables for configuration
5. **Testing**: Test both modes before deploying

## Decision Guide

Choose **Local Mode** if:
- Working alone
- Rapid prototyping
- Small experiments
- No cloud infrastructure
- Privacy concerns

Choose **Remote Mode** if:
- Working with a team
- Large-scale experiments
- Cloud training
- Need web UI
- Production workflows

## See Also

**Deployment & Operations:**
- **[Deployment Guide](deployment.md)** - Deploy your own DreamLake server (Docker, Kubernetes, Cloud)
- **[Architecture](architecture.md)** - Understand the technical differences between modes
- **[FAQ](faq.md)** - When should I use local vs url mode?

**Getting Started:**
- [Complete Examples](complete-examples.md) - Full examples for both modes
- [Getting Started](getting-started.md) - Quick start tutorial
