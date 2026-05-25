# Testing

## Running Tests

```bash
cd dreamlake-py
uv sync --extra dev

# Local tests only (no server needed)
uv run pytest test/ -v -m "not url"

# All tests (requires test environment)
uv run pytest test/ -v
```

## Test Environment

Start the test stack:

```bash
cd docker
docker compose -f docker-compose.test.yml up -d
```

This starts MongoDB (port 27018), MinIO (ports 9002/9003), and the DreamLake server (port 3000) — all on non-default ports to avoid conflicts with dev services.

```bash
# Verify
curl http://localhost:3000/health

# Run all tests
cd ../dreamlake-py && uv run pytest test/ -v

# Tear down
cd docker && docker compose -f docker-compose.test.yml down -v
```

## Running Specific Tests

```bash
uv run pytest test/test_files.py -v           # by file
uv run pytest test/test_logging.py::TestBasicLogging -v  # by class
uv run pytest test/ -v -m local_only          # by marker
uv run pytest test/ -v -k "rapid or frequent" # stress tests
```

## Development Workflow

1. Make changes in `src/dreamlake/`
2. Run local tests: `uv run pytest test/ -v -m "not url"`
3. Start test env, run remote tests: `uv run pytest test/ -v -m url`
4. Run full suite before committing: `uv run pytest test/ -v`

## Troubleshooting

**401 Unauthorized:** JWT secret mismatch between server and SDK. Check `dreamlake-server/.env.test` matches the hardcoded secret in `src/dreamlake/episode.py`.

**500 Internal Server Error:** Check `docker compose -f docker-compose.test.yml logs dreamlake-server --tail 100`.

**Clean reset:**

```bash
cd docker
docker compose -f docker-compose.test.yml down -v
docker compose -f docker-compose.test.yml up -d
sleep 15
cd ../dreamlake-py && uv run pytest test/ -v
```
