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
Factory function to create remote-mode episodes. Requires `DREAMLAKE_SERVER_URL` env variable.

## Environment Variables

Optional environment variables for remote testing:

- `DREAMLAKE_SERVER_URL` - Remote server URL (default: skip remote tests)
- `DREAMLAKE_TEST_USER` - Test username (default: "test-user")
- `DREAMLAKE_API_KEY` - API key (optional, auto-generated from username)

## Notes

- All tests use temporary directories and are automatically cleaned up
- Tests use simple function notation (no test classes)
- Remote tests are skipped if server URL is not configured
- All 47 tests currently passing (as of last run)
