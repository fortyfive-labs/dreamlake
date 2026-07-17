# Dreamlake Python SDK Tests

This directory contains the pytest test suite for the Dreamlake Python SDK.

## Test Files

- **`conftest.py`** - Pytest configuration and fixtures
- **`test_basic_episode.py`** - Basic episode operations (7 tests)
- **`test_logging.py`** - Logging functionality (7 tests)
- **`test_parameters.py`** - Parameter tracking (7 tests)
- **`test_tracks.py`** - Time-series metrics tracking (10 tests)
- **`test_files.py`** - File upload operations (10 tests)
- **`test_integration.py`** - End-to-end integration workflows (6 tests)

**Total: 47 tests**

## Running Tests

### Run all tests
```bash
uv run pytest test/
```

### Run specific test file
```bash
uv run pytest test/test_basic_episode.py
```

### Run with verbose output
```bash
uv run pytest test/ -v
```

### Run specific test
```bash
uv run pytest test/test_logging.py::test_basic_logging
```

## Test Coverage

The test suite covers:

-  Episode creation and lifecycle management
-  Context manager usage
-  Manual open/close operations
-  Episode metadata (description, tags, folder)
-  Log levels (debug, info, warn, error, fatal)
-  Structured logging with metadata
-  Simple and nested parameter tracking
-  Parameter flattening
-  Parameter updates
-  Time-series metrics tracking
-  Batch data appending
-  Track statistics and metadata
-  File uploads with metadata
-  File checksums
-  File tagging and prefixes
-  Complete ML workflow integration
-  Hyperparameter search workflows
-  Multi-episode pipelines
-  Error handling

## Fixtures

### `temp_workspace`
Provides a temporary directory for test data (uses pytest's `tmp_path`).

### `local_episode`
Factory function to create local-mode episodes with default configuration.

Usage:
```python
def test_example(local_episode, temp_workspace):
    with local_episode(name="my-episode", workspace="test") as episode:
        episode.log("Test message")
```

### `sample_files`
Creates sample files for testing file uploads:
- `model.txt` - Simulated model weights
- `config.json` - Configuration file
- `results.txt` - CSV-like results

### `remote_episode` (Optional)
Factory function to create remote-mode episodes. Requires `DREAMLAKE_URL` and
`DREAMLAKE_API_KEY` env variables; skipped otherwise.

## Environment Variables

Live-server tests are opt-in via environment variables. With none of these
set, the suite passes on a machine with nothing running.

- `DREAMLAKE_URL` - dreamlake-server URL (dev default: `http://localhost:10334`);
  unset = skip remote tests. Note: port 3000 is the frontend, not the server.
- `DREAMLAKE_API_KEY` - API token for remote mode; unset = skip remote tests
- `DREAMLAKE_BSS_URL` - BSS URL for the `test_api.py` integration tests;
  unset = skip integration tests
- `TEST_VIDEO_ID` - ID of a video that exists in the BSS instance above
- `QDRANT_URL` - Qdrant URL for vector-index tests (default: `http://localhost:6333`)

## Notes

- All tests use temporary directories and are automatically cleaned up
- Remote tests are skipped if `DREAMLAKE_URL` is not configured
- The remote (ML-Dash compatible) client still targets the server's legacy
  `/workspaces/...` routes, which the server has since replaced with
  `/namespaces/:slug/projects/:projectSlug/...`. Until the client is ported,
  remote-mode tests will fail against a current server even when opted in.
