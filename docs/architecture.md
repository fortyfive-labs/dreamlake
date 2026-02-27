# Architecture & Design

This document provides an in-depth look at DreamLake's architecture, design decisions, and internal workings.

## Overview

DreamLake is built with a clean, modular architecture that supports both local filesystem and dash_url API server backends. The design emphasizes simplicity, flexibility, and ease of use while maintaining powerful functionality for ML experiment tracking.

### High-Level Architecture

```{mermaid}
flowchart TB
    User[User Code] --> Session[Session Manager]
    Session --> Builder[Builder APIs]
    Builder --> Log[LogBuilder]
    Builder --> Params[ParametersBuilder]
    Builder --> Track[TrackBuilder]
    Builder --> Files[FileBuilder]

    Log --> Backend{Backend Layer}
    Params --> Backend
    Track --> Backend
    Files --> Backend

    Backend --> Local[LocalStorage]
    Backend --> Remote[RemoteClient]

    Local --> FS[Filesystem<br/>JSON/JSONL]
    Remote --> API[REST API]

    API --> MongoDB[(MongoDB)]
    API --> S3[(S3/MinIO)]

    style Session fill:#e1f5ff
    style Backend fill:#fff4e1
    style Local fill:#e8f5e9
    style Remote fill:#f3e5f5
```

## Core Components

### 1. Session Manager

The `Session` class is the entry point for all DreamLake operations. It:

- **Manages lifecycle**: Creation, opening, closing of experiment sessions
- **Handles backends**: Automatically selects LocalStorage or RemoteClient based on configuration
- **Provides builder access**: Returns builder instances for logs, parameters, tracks, and files
- **Supports multiple patterns**: Context manager, decorator, or direct instantiation

**Key responsibilities**:
```text
Session
├── Lifecycle management (open/close)
├── Backend initialization (local or dash_url)
├── Builder factory methods
├── Session metadata management
└── Error handling and recovery
```

### 2. Builder APIs

DreamLake uses the **Builder Pattern** to provide a fluent, chainable API for data operations:

#### LogBuilder
```python
session.log("Message", level="info", metadata={...})
```
- Structured logging with 5 levels (debug, info, warn, error, fatal)
- Automatic timestamping and sequence numbering
- Metadata support for structured data

#### ParametersBuilder
```python
session.params.set(lr=0.001, batch_size=32)
```
- Stores hyperparameters and configuration
- **Automatic flattening**: Nested dicts converted to dot notation
  - Input: `{"model": {"layers": 50}}`
  - Stored: `{"model.layers": 50}`
- Upsert behavior: Updates existing values

#### TrackBuilder
```python
session.track("loss").append(value=0.5, epoch=0)
session.track("loss").append_batch([...])
```
- Time-series metrics tracking
- Flexible schema (any fields)
- Efficient batch operations
- Read with pagination support

#### FileBuilder
```python
session.files.upload("model.pth", path="/models")
```
- File upload and organization
- Checksum validation (SHA256)
- Metadata and tagging
- Hierarchical organization with prefixes

### 3. Backend Layer

The backend layer abstracts storage implementation, allowing DreamLake to work with different backends without changing user code.

#### LocalStorage

**Filesystem Structure**:
```
<root_path>/
└── <workspace>/
    └── <session>/
        ├── session.json          # Session metadata
        ├── parameters.json       # Hyperparameters
        ├── logs/
        │   └── logs.jsonl       # Log entries (JSON Lines)
        ├── tracks/
        │   └── <track_name>/
        │       ├── metadata.json
        │       └── data.jsonl   # Time-series data
        └── files/
            ├── .files_metadata.json
            ├── <prefix>/               # e.g., models/
            │   └── <file_id>/
            │       └── <filename>
            └── <another_prefix>/       # e.g., config/
                └── <file_id>/
                    └── <filename>
```

**Data Formats**:
- **JSON**: Structured metadata (session.json, parameters.json)
- **JSONL** (JSON Lines): Append-only logs and tracks
- **Raw Files**: Binary files stored with original names

**Advantages**:
- ✅ No server required
- ✅ Easy to inspect and debug
- ✅ Fast for local development
- ✅ Works offline
- ✅ Git-friendly for small experiments

#### RemoteClient

**REST API Communication**:
```
POST   /workspaces/{workspace}/sessions
POST   /sessions/{id}/logs
POST   /sessions/{id}/parameters
POST   /sessions/{id}/tracks/{name}
POST   /sessions/{id}/tracks/{name}/batch
POST   /sessions/{id}/files
GET    /sessions/{id}/tracks/{name}
GET    /sessions/{id}/tracks
GET    /sessions/{id}/files
```

**Authentication**:
- JWT tokens via `Authorization: Bearer <token>` header
- Auto-generation from `user_name` parameter (development mode)
- Custom API key support

**Advantages**:
- ✅ Centralized storage and sharing
- ✅ Team collaboration
- ✅ Scalable for large experiments
- ✅ Query and search capabilities
- ✅ Web UI integration

## Data Flow

### Local Mode Flow

```{mermaid}
sequenceDiagram
    participant User
    participant Session
    participant LocalStorage
    participant FS as Filesystem

    User->>Session: Create session
    Session->>LocalStorage: Initialize storage
    LocalStorage->>FS: Create directories

    User->>Session: log("message")
    Session->>LocalStorage: write_log(...)
    LocalStorage->>FS: Append to logs.jsonl

    User->>Session: parameters().set(...)
    Session->>LocalStorage: write_parameters(...)
    LocalStorage->>FS: Write parameters.json

    User->>Session: close()
    Session->>LocalStorage: finalize()
```

### Remote Mode Flow

```{mermaid}
sequenceDiagram
    participant User
    participant Session
    participant RemoteClient
    participant API as REST API
    participant DB as MongoDB

    User->>Session: Create session
    Session->>RemoteClient: create_session(...)
    RemoteClient->>API: POST /workspaces/{ws}/sessions
    API->>DB: Insert session doc
    DB-->>API: Session ID
    API-->>RemoteClient: Session data

    User->>Session: log("message")
    Session->>RemoteClient: create_log_entries([...])
    RemoteClient->>API: POST /sessions/{id}/logs
    API->>DB: Insert log docs

    User->>Session: close()
    Session->>RemoteClient: (finalize if needed)
```

## Design Decisions

### 1. Builder Pattern

**Why?**
- **Fluent API**: Chainable methods for better readability
- **Lazy initialization**: Builders created on-demand
- **Separation of concerns**: Each builder focuses on one data type
- **Extensibility**: Easy to add new data types

**Example**:
```python
# Clean, readable API
session.track("accuracy").append(value=0.95, epoch=10)

# vs procedural approach
session.append_track("accuracy", {"value": 0.95, "epoch": 10})
```

### 2. Upsert Behavior

**What**: Sessions can be reopened and updated

**Why?**
- **Recovery**: Resume after crashes or interruptions
- **Iterative development**: Add data to existing sessions
- **Flexibility**: Update metadata, add new tracks/logs

**Implementation**:
- Local: Check if session directory exists, merge data
- Remote: API checks session existence, merges on server

### 3. Auto-Creation

**What**: Automatically creates namespace → workspace → folder hierarchy

**Why?**
- **Less boilerplate**: No manual directory/workspace creation
- **Better UX**: Focus on experiment, not setup
- **Convention over configuration**: Sensible defaults

**Example**:
```python
# This automatically creates:
# - Namespace (if dash_url)
# - Workspace "my-workspace"
# - Folder "/experiments/2024"
# - Session "baseline"
Session(prefix="my-workspace/baseline",
    folder="/experiments/2024",
    dash_root=".dreamlake",
        local_path=".dreamlake"
)
```

### 4. Dual Backend Support

**Why?**
- **Flexibility**: Local for development, dash_url for production
- **Gradual adoption**: Start local, migrate to dash_url when ready
- **Offline capability**: Work without network access
- **Testing**: Easy to test with local mode

**Trade-offs**:

| Aspect | Local Mode | Remote Mode |
|--------|-----------|-------------|
| Setup | ✅ Zero setup | ⚠️ Requires server |
| Performance | ✅ Fast writes | ⚠️ Network latency |
| Collaboration | ❌ File sharing only | ✅ Built-in sharing |
| Querying | ❌ Manual file inspection | ✅ API queries |
| Scalability | ⚠️ Limited by disk | ✅ Scales horizontally |

### 5. JSON/JSONL Format

**Why?**
- **Human-readable**: Easy to inspect and debug
- **Language-agnostic**: Any tool can read
- **Append-friendly**: JSONL for logs/tracks
- **Git-friendly**: Text-based diffs

**JSONL (JSON Lines)** for append operations:
```json
{"timestamp": "2024-01-01T00:00:00Z", "message": "Log 1"}
{"timestamp": "2024-01-01T00:00:01Z", "message": "Log 2"}
{"timestamp": "2024-01-01T00:00:02Z", "message": "Log 3"}
```

Benefits:
- ✅ No need to load entire file
- ✅ Append-only (no file rewrites)
- ✅ Stream-friendly
- ✅ Fault-tolerant (partial reads work)

## Extensibility

### Custom Storage Backends

DreamLake's architecture allows for custom storage backends by implementing the storage interface:

```python
class CustomStorage:
    def create_session(self, name, workspace, **kwargs):
        # Create session
        pass

    def write_log(self, session_id, log_entry):
        # Store log
        pass

    def write_parameters(self, session_id, params):
        # Store parameters
        pass

    # ... other methods
```

### Future Extensibility Points

**Planned**:
- Plugin system for custom data types
- Middleware for data transformation
- Custom serialization formats
- Storage adapters (PostgreSQL, DynamoDB, etc.)
- Event hooks (pre-save, post-save)

## Performance Considerations

### Batch Operations

For high-throughput scenarios, use batch operations:

```python
# Instead of multiple appends
for data in dataset:
    session.track("metric").append(**data)  # ❌ Slow

# Use batch append
batch_data = [{"value": x, "step": i} for i, x in enumerate(values)]
session.track("metric").append_batch(batch_data)  # ✅ Fast
```

**Performance gains**:
- Local: 10-50x faster (reduces filesystem operations)
- Remote: 20-100x faster (reduces network requests)

### File Upload Optimization

- **Chunked upload**: For large files (dash_url mode)
- **Checksum validation**: Ensures data integrity
- **Concurrent uploads**: Multiple files in parallel (planned)

### Caching Strategy

**Current**:
- Session metadata cached in memory
- Parameters cached until update
- No caching for logs/tracks (append-only)

**Future**:
- Configurable write batching
- Read caching for tracks
- Lazy loading for large datasets

## Security

### Authentication (Remote Mode)

**JWT Tokens**:
- Standard Bearer token authentication
- Configurable expiration (default: 30 days)
- Secret key must match server configuration

**Development Mode**:
```python
# Auto-generates JWT from username
Session(dash_url="...", user_name="alice")
# Equivalent to providing a JWT token
```

**Production Mode**:
```python
# Use proper API key from authentication service
Session(dash_url="...", api_key="actual-jwt-token")
```

### Data Security

**In Transit**:
- Use HTTPS for dash_url connections
- TLS for MongoDB connections

**At Rest**:
- Local: Standard filesystem permissions
- Remote: Encryption at database level
- S3: Server-side encryption

## Comparison with Other Tools

| Feature | DreamLake | MLflow | Weights & Biases | Neptune.ai |
|---------|-----------|---------|------------------|------------|
| **Local Mode** | ✅ First-class | ✅ Yes | ❌ Cloud-only | ❌ Cloud-only |
| **Self-hosted** | ✅ Easy | ✅ Yes | ❌ Enterprise only | ❌ No |
| **Offline Work** | ✅ Yes | ✅ Yes | ❌ No | ❌ No |
| **File Storage** | ✅ Built-in | ✅ Artifact store | ✅ Yes | ✅ Yes |
| **Learning Curve** | ✅ Low | ⚠️ Medium | ⚠️ Medium | ⚠️ Medium |
| **Setup Time** | ✅ < 1 min | ⚠️ 5-10 min | ✅ 2 min | ✅ 2 min |

**DreamLake's sweet spot**:
- Quick local experiments with zero setup
- Easy transition to collaborative dash_url mode
- Simple, intuitive API
- Full control over your data

## Future Roadmap

### Short Term (v0.3)
- [ ] Hybrid mode (local + dash_url sync)
- [ ] Query API for searching experiments
- [ ] Web UI for visualization
- [ ] Batch file uploads

### Medium Term (v0.4-0.5)
- [ ] Real-time streaming API
- [ ] Experiment comparison tools
- [ ] Plugin system
- [ ] Integration with popular frameworks

### Long Term (v1.0+)
- [ ] Distributed training support
- [ ] Advanced query language
- [ ] Multi-cloud support
- [ ] Enterprise features (RBAC, audit logs)

## See Also

- [Getting Started](getting-started.md) - Quick start guide
- [Local vs Remote](local-vs-dash_url.md) - Choosing the right mode
- [Deployment Guide](deployment.md) - Setting up your own server
- [API Reference](api/modules.rst) - Detailed API documentation
