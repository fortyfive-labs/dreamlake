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

## Running Remote Tests Against a Local Server

The remote client targets the current server surface: episodes live under
`/namespaces/:slug/projects/:projectSlug/episodes`, and episode-scoped
resources (logs, parameters, files, tracks) under `/episodes/:episodeId/...`.
The namespace comes from the API token (`GET /auth/me`) unless the prefix has
three segments (`namespace/project/name`); missing projects are auto-created
on first episode open.

1. Start dreamlake-server (it reads `dreamlake-server/.env`, which points at
   the shared Atlas dev cluster and sets the dev `JWT_SECRET`):

   ```bash
   cd ../dreamlake-server && pnpm dev   # listens on :10334
   ```

2. Mint a JWT signed with the server's `JWT_SECRET` (the committed dev value
   is `your-secret-key-change-this-in-production`). Any `sub` works; the
   `username` claim becomes the namespace slug:

   ```python
   import time, jwt
   token = jwt.encode(
       {
           "sub": "sdk-remote-test",
           "email": "sdk-remote-test@localhost",
           "name": "SDK Remote Test",
           "username": "sdk-remote-test",
           "iat": int(time.time()),
           "exp": int(time.time()) + 7 * 24 * 3600,
       },
       "your-secret-key-change-this-in-production",
       algorithm="HS256",
   )
   ```

   The first authenticated request (e.g. `GET /auth/me`) registers the user
   and allocates their personal namespace — no manual DB seeding needed.

   Alternatively, run the server with `SKIP_AUTH=true` to disable token
   verification entirely (a stub `dev-user` is injected on every request).

3. Run the suite:

   ```bash
   DREAMLAKE_URL=http://localhost:10334 DREAMLAKE_API_KEY=<token> uv run pytest test/
   ```

## Known Server Bugs (Gated Remote Tests)

Some remote tests are skipped unconditionally because the corresponding
server-side handlers are currently broken (the SDK sends correct requests;
the failures are internal to dreamlake-server). The skip marks live in
`conftest.py` — remove each once the server fix lands.

Fixed by dreamlake-server#61 (PR #65, verified live against the fixed
server — gates removed):

- parameters create - `POST /episodes/:id/parameters` no longer 500s
  (the stray `deletedAt` writes were dropped)
- `fatal` log level - the route schema now accepts the canonical
  debug/info/warn/error/fatal set
- descendants - `GET /nodes/:id/descendants` no longer strips the
  `descendants` array from its response (no SDK test gated on this;
  listed for completeness)

Still broken (dreamlake-server#67) — these gates stay:

- `server_tracks_bug` - track append 500s: the Track row now creates
  correctly, but `services/tracks.ts` still calls Prisma models
  (`TrackMetadata`/`TrackBuffer`/`TrackChunk`) deleted by the `3b2779a`
  schema redesign
- `server_files_bug` - file upload 500s: the multipart route now accepts
  the request, but `services/files.ts` still calls the deleted Prisma
  `File` model

## Notes

- All tests use temporary directories and are automatically cleaned up
- Remote tests are skipped if `DREAMLAKE_URL` is not configured
- `read_by_time` is local-only: the server no longer exposes a by-time track
  read route, so remote-only episodes raise `NotImplementedError`; hybrid
  episodes (url + root) read from local storage
