# FAQ & Troubleshooting

Common questions and solutions for using DreamLake.

## General Questions

### When should I use local vs url mode?

**Use Local Mode when:**
- ✅ Rapid prototyping and development
- ✅ Working offline or with unstable network
- ✅ Personal projects or single-user experiments
- ✅ You want zero setup overhead
- ✅ Debugging and testing

**Use Remote Mode when:**
- ✅ Team collaboration and sharing experiments
- ✅ Need centralized storage and querying
- ✅ Production ML pipelines
- ✅ Want web UI for visualization
- ✅ Running experiments across multiple machines

**Summary**: Start with local mode for development, switch to url for collaboration.

---

### How do I migrate from local to url mode?

Currently, you need to manually sync data. We recommend:

**Option 1: Re-run experiments** (recommended)
```python
# Change from local to url, re-run your code
Episode(prefix="my-workspace/my-experiment",
    url="http://localhost:3000",  # Changed from local_path
    user_name="your-name"
)
```

**Option 2: Export and import** (planned feature)
```python
# Coming in v0.3
from dreamlake import migrate

migrate.local_to_remote(
    root=".dreamlake",
    remote_url="http://localhost:3000",
    api_key="your-key"
)
```

---

### Can I use both local and url mode simultaneously?

Not yet! **Hybrid mode** is planned for v0.3:

```python
# Coming soon
Episode(prefix="my-workspace/my-experiment",
    root=".dreamlake",  # Local backup
    url="http://localhost:3000",  # Syncs to url
    user_name="your-name"
)
```

This will automatically sync local data to url server.

---

### How does DreamLake compare to MLflow, Weights & Biases, and Neptune?

| Feature | DreamLake | MLflow | W&B | Neptune |
|---------|-----------|--------|-----|---------|
| **Local Mode** | ✅ Zero setup | ✅ Yes | ❌ Cloud only | ❌ Cloud only |
| **Self-hosted** | ✅ Easy Docker | ✅ Complex | ⚠️ Enterprise | ❌ No |
| **Offline** | ✅ Full support | ✅ Yes | ❌ No | ❌ No |
| **Learning Curve** | ✅ 5 minutes | ⚠️ 30 min | ⚠️ 20 min | ⚠️ 20 min |
| **Price** | ✅ Free & OSS | ✅ Free | ⚠️ $$$ | ⚠️ $$$ |
| **API Style** | ✅ Fluent/Builder | ⚠️ Functional | ✅ Fluent | ✅ Fluent |
| **File Storage** | ✅ Built-in | ✅ Artifacts | ✅ Yes | ✅ Yes |
| **Web UI** | 🔜 v0.3 | ✅ Yes | ✅ Advanced | ✅ Advanced |

**DreamLake's strengths**:
- Simplest setup (literally zero for local mode)
- True offline capability
- Full data ownership
- Clean, intuitive API

**When to use alternatives**:
- **MLflow**: Need model registry and deployment features
- **W&B**: Want advanced collaboration and visualization now
- **Neptune**: Enterprise support and compliance

---

### What happens if my episode crashes mid-training?

DreamLake episodes are designed for **resilience**:

1. **Data is written immediately**: Logs, parameters, and tracks are saved as soon as you call them (not buffered)
2. **Re-open episodes**: Use the same episode name to continue

```python
# First run - crashes at epoch 5
try:
    with Episode(prefix="test/training", root=".dreamlake",
        local_path=".dreamlake") as episode:
        for epoch in range(10):
            episode.track("train").append(loss=loss, epoch=epoch)
            # Crashes here at epoch 5
except Exception:
    pass

# Second run - continue from crash
with Episode(prefix="test/training", root=".dreamlake",
        local_path=".dreamlake") as episode:
    # Continue from epoch 6
    for epoch in range(6, 10):
        episode.track("train").append(loss=loss, epoch=epoch)
```

**Result**: You'll have all data from both runs in the same episode!

---

### Does DreamLake support distributed training?

**Current**: Basic support - each worker can log to the same episode

```python
# Each worker
rank = dist.get_rank()
with Episode(name=f"training-rank-{rank}", workspace="distributed", ...) as episode:
    # Each worker tracks its own metrics
    episode.track("train").append(loss=local_loss, epoch=epoch)
```

**Planned (v0.4)**: First-class distributed training support with:
- Automatic rank detection
- Aggregated metrics
- Distributed file uploads
- Synchronization primitives

---

### Can I query or search my experiments?

**Local Mode**:
- ❌ No built-in query API yet
- ✅ Can use `find`, `grep`, or Python to search JSON files

**Remote Mode**:
- 🔜 Query API coming in v0.3
- 🔜 Filter by tags, parameters, date ranges
- 🔜 Full-text search on logs

Example (coming soon):
```python
from dreamlake import query

results = query.search(
    workspace="my-workspace",
    tags=["production"],
    parameters={"learning_rate": {"$gt": 0.001}},
    date_range=("2024-01-01", "2024-12-31")
)
```

---

## Authentication Questions

### How do I get an API key?

**Development Mode** (automatic):
```python
# SDK automatically generates JWT from username
Episode(
    url="http://localhost:3000",
    user_name="alice"  # No API key needed!
)
```

The SDK generates a deterministic JWT token using the username and the server's JWT secret.

**Production Mode** (proper auth service):
1. Set up authentication service (OAuth, SAML, custom)
2. User logs in, receives JWT token
3. Pass token to SDK:

```python
# Your auth service returns JWT
api_key = your_auth_service.login("alice", "password")

Episode(
    url="https://dreamlake.company.com",
    api_key=api_key
)
```

---

### Why am I getting 401 Unauthorized errors?

**Cause**: Invalid or missing API key, or JWT secret mismatch

**Solutions**:

1. **Using `user_name`** - Check JWT secret matches:
   ```python
   # SDK generates JWT using secret from episode.py:
   # secret = "your-secret-key-change-this-in-production"

   # Server must use SAME secret in .env:
   JWT_SECRET=your-secret-key-change-this-in-production
   ```

2. **Using `api_key`** - Verify token is valid:
   ```python
   # Test if token is valid
   import jwt

   try:
       decoded = jwt.decode(api_key, verify=False)
       print(f"Token payload: {decoded}")
       print(f"Expires: {decoded.get('exp')}")
   except:
       print("Invalid JWT token format")
   ```

3. **Check server logs**:
   ```bash
   docker-compose logs dreamlake-server | grep "401"
   ```

---

### How does user_name authentication work?

**Development Feature**: Simplified auth for development and testing

**How it works**:
1. SDK takes `user_name` parameter
2. Generates deterministic user ID from username hash
3. Creates JWT token with payload:
   ```json
   {
     "userId": "1234567890",
     "userName": "alice",
     "iat": 1704067200,
     "exp": 1706745600
   }
   ```
4. Signs with secret: `"your-secret-key-change-this-in-production"`
5. Sends as `Authorization: Bearer <token>` header

**Security Note**: This is for development only! Production should use proper authentication.

---

### Can I use my own JWT secret?

**Yes!** Change the secret in two places:

**1. Server** (`.env` or environment variable):
```bash
JWT_SECRET=my-custom-super-secret-key-123
```

**2. SDK** (if using `user_name`):

Edit `src/dreamlake/episode.py`:
```python
def _generate_api_key_from_username(user_name: str) -> str:
    # ...
    secret = "my-custom-super-secret-key-123"  # Change this
    token = jwt.encode(payload, secret, algorithm="HS256")
    return token
```

**Better approach**: Use `api_key` parameter instead of `user_name` in production!

---

## Performance Questions

### How do I handle large files efficiently?

**Best Practices**:

1. **Compress before upload**:
   ```python
   import gzip

   # Compress model
   with open("model.pth", "rb") as f_in:
       with gzip.open("model.pth.gz", "wb") as f_out:
           f_out.writelines(f_in)

   episode.files.upload("model.pth.gz", path="/models")
   ```

2. **Split large files**:
   ```python
   # Split into chunks
   for i, chunk in enumerate(split_file("large_dataset.tar", chunk_size_mb=100)):
       episode.files.upload(chunk, path=f"/data/part-{i}")
   ```

3. **Use external storage** (for very large files):
   ```python
   # Upload to S3 directly, just store reference
   s3_url = upload_to_s3("huge_model.bin")
   episode.params.set(model_url=s3_url)
   ```

**Performance Tips**:
- Files < 10MB: Upload normally
- Files 10-100MB: Consider compression
- Files > 100MB: Split or use external storage

---

### Best practices for rapid logging/tracking?

**Use batch operations** for high-throughput scenarios:

❌ **Slow** (individual calls):
```python
for i in range(10000):
    episode.track("metric").append(metric=i, step=i)
```

✅ **Fast** (batch operation):
```python
batch_data = [{"value": i, "step": i} for i in range(10000)]
episode.track("metric").append_batch(batch_data)
```

**Performance gains**:
- Local mode: 10-50x faster (fewer file operations)
- Remote mode: 50-100x faster (fewer network requests)

**Other tips**:
1. **Buffer logs** for very rapid logging (>100/sec)
2. **Reduce log frequency** - log every N iterations instead of every iteration
3. **Use appropriate data types** - avoid large nested structures in metadata

---

### When should I use batch operations?

**Use `append_batch()` when**:
- Tracking > 10 data points at once
- High-frequency tracking (>10/sec)
- Post-processing results (already have all data)

**Use individual `append()` when**:
- Real-time tracking during training
- Immediate feedback needed
- Tracking < 10 data points

**Example**: Training loop
```python
# Batch append for per-batch metrics
batch_metrics = []
for batch_idx, batch in enumerate(dataloader):
    loss = train_step(batch)
    batch_metrics.append({"loss": loss, "step": batch_idx})

    # Batch append every 100 steps
    if len(batch_metrics) >= 100:
        episode.track("batch_loss").append_batch(batch_metrics)
        batch_metrics = []

# Append remaining
if batch_metrics:
    episode.track("batch_loss").append_batch(batch_metrics)
```

---

## Troubleshooting

### Problem: 401 Unauthorized

**Symptoms**:
```
httpx.HTTPStatusError: Client error '401 Unauthorized'
```

**Causes**:
1. Missing or invalid API key
2. JWT secret mismatch
3. Expired token

**Solutions**:

1. **Use `user_name` for development**:
   ```python
   Episode(url="http://localhost:3000", user_name="test-user")
   ```

2. **Check JWT secret matches**:
   ```bash
   # Server .env
   cat .env | grep JWT_SECRET

   # SDK (if using user_name)
   grep "secret =" src/dreamlake/episode.py
   ```

3. **Verify server is running**:
   ```bash
   curl http://localhost:3000/health
   ```

---

### Problem: Episode data not appearing

**Symptoms**:
- Logs/parameters written but not visible in filesystem/server
- Empty files or missing data

**Causes**:
1. Episode not properly closed
2. Buffering in url mode
3. File permissions (local mode)

**Solutions**:

1. **Always use context manager**:
   ```python
   # ✅ Good - auto-closes
   with Episode(...) as episode:
       episode.log("message")

   # ❌ Bad - might not close
   episode = Episode(...)
   episode.log("message")
   # Forgot to call episode.close()!
   ```

2. **Manual close**:
   ```python
   episode = Episode(...)
   try:
       episode.log("message")
   finally:
       episode.close()  # Ensures data is flushed
   ```

3. **Check permissions** (local mode):
   ```bash
   ls -la .dreamlake/
   # Should be writable by your user
   ```

---

### Problem: Server connection timeout

**Symptoms**:
```
httpx.ConnectTimeout: Connection timeout
```

**Causes**:
1. Server not running
2. Wrong URL
3. Network/firewall issues
4. Server overloaded

**Solutions**:

1. **Check server health**:
   ```bash
   curl http://localhost:3000/health
   ```

2. **Verify URL**:
   ```python
   # Common mistakes:
   # ❌ Missing http://
   url="localhost:3000"

   # ❌ Wrong port
   url="http://localhost:8000"

   # ✅ Correct
   url="http://localhost:3000"
   ```

3. **Check server logs**:
   ```bash
   docker-compose logs -f dreamlake-server
   ```

4. **Test network**:
   ```bash
   # Can you reach the server?
   telnet localhost 3000

   # Check firewall
   sudo ufw status
   ```

---

### Problem: File upload fails

**Symptoms**:
```
Error uploading file: File not found / Permission denied
```

**Causes**:
1. File path doesn't exist
2. File is locked or permissions issue
3. File too large
4. S3 storage full or misconfigured

**Solutions**:

1. **Verify file exists**:
   ```python
   import os
   file_path = "model.pth"

   if not os.path.exists(file_path):
       print(f"File not found: {file_path}")
   elif not os.access(file_path, os.R_OK):
       print(f"Cannot read file: {file_path}")
   else:
       episode.files.upload(file_path, path="/models")
   ```

2. **Check file size**:
   ```python
   size_mb = os.path.getsize(file_path) / (1024 * 1024)
   print(f"File size: {size_mb:.2f} MB")

   # Server default limit: 100MB
   if size_mb > 100:
       print("File too large, consider compression or splitting")
   ```

3. **Check S3 configuration** (url mode):
   ```bash
   # Server logs
   docker-compose logs dreamlake-server | grep "S3"

   # Test S3 connection
   aws s3 ls s3://your-bucket/
   ```

---

### Problem: Parameters not flattening correctly

**Symptoms**:
```python
# Expected: {"model.layers": 50}
# Got: {"model": {"layers": 50}}
```

**Cause**: Not passing dict to `set()`

**Solution**:

```python
# ❌ Wrong - not flattened
episode.params.set(model={"layers": 50})

# ✅ Correct - use ** unpacking
episode.params.set(**{"model": {"layers": 50}})

# ✅ Alternative - use dict variable
params = {"model": {"layers": 50}}
episode.params.set(**params)
```

---

### Problem: Tracks showing wrong indices

**Symptoms**:
- Index numbers not sequential
- Duplicate indices
- Indices start from wrong number

**Cause**: Multiple episodes or concurrent writes

**Solution**:

Indices are auto-managed. If you see issues:

1. **Don't reuse episode names** for different runs
2. **Use unique episode names** or add timestamps:
   ```python
   from datetime import datetime

   episode_name = f"training-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
   Episode(name=episode_name, ...)
   ```

3. **Check for concurrent access** (local mode):
   ```bash
   # Multiple processes writing to same episode?
   lsof +D .dreamlake/workspace/episode/
   ```

---

### Problem: Import Error - "No module named 'dreamlake'"

**Symptoms**:
```
ModuleNotFoundError: No module named 'dreamlake'
```

**Solutions**:

1. **Install package** (current version: {VERSION}):
   ```bash
   pip install dreamlake=={VERSION}
   # Or from source
   pip install -e .
   ```

2. **Check Python environment**:
   ```bash
   which python
   pip list | grep dreamlake
   ```

3. **Virtual environment activation**:
   ```bash
   # Activate venv
   source venv/bin/activate  # Linux/Mac
   venv\Scripts\activate  # Windows
   ```

---

## Still Having Issues?

1. **Check the logs**:
   - Local: `ls -la .dreamlake/workspace/episode/`
   - Remote: `docker-compose logs dreamlake-server`

2. **Enable debug logging**:
   ```python
   import logging
   logging.basicConfig(level=logging.DEBUG)
   ```

3. **Search existing issues**: [GitHub Issues](https://github.com/your-org/dreamlake/issues)

4. **Ask for help**:
   - GitHub Discussions
   - Community Discord/Slack
   - Email: support@dreamlake.com

## See Also

- [Getting Started](getting-started.md) - Quick start guide
- [Architecture](architecture.md) - How DreamLake works internally
- [Deployment Guide](deployment.md) - Setting up your server
- [API Reference](api/modules.rst) - Detailed API documentation
