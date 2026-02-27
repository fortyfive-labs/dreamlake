# Testing & Development

This guide covers running tests and setting up a local development environment for DreamLake.

## Test Environment

DreamLake includes a comprehensive test suite with 165 tests covering all SDK features. Tests run in two modes:

- **Local mode** (127 tests): Tests that run without a server, using local filesystem storage
- **Remote mode** (38 tests): Integration tests that require a running DreamLake server

## Quick Start - Running Tests

### Local Tests Only

Run tests that don't require a server:

```bash
cd dreamlake-py
uv run pytest test/ -v -m "not remote"
```

**Result**: 127 tests pass, 38 remote tests skipped

### All Tests (Local + Remote)

To run all tests including remote integration tests, you need to start the test environment first.

#### 1. Start Test Environment

```bash
cd docker
docker compose -f docker-compose.test.yml up -d
```

This starts:
- **MongoDB** on port 27018 (replica set configured)
- **MinIO** on ports 9002 (API) and 9003 (Console)
- **DreamLake Server** on port 3000

#### 2. Verify Services

```bash
# Check all services are healthy
docker compose -f docker-compose.test.yml ps

# Check server health
curl http://localhost:3000/health
```

#### 3. Run All Tests

```bash
cd ../dreamlake-py
uv run pytest test/ -v
```

**Result**: All 165 tests pass

#### 4. Stop Test Environment

```bash
cd docker
docker compose -f docker-compose.test.yml down

# Remove test data volumes (optional)
docker compose -f docker-compose.test.yml down -v
```

## Test Environment Architecture

The test environment uses Docker Compose to orchestrate three services:

```
┌─────────────────────────────────────────────────────┐
│  Test Environment (Isolated from Development)       │
│                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────┐ │
│  │   MongoDB    │  │    MinIO     │  │  DreamLake│ │
│  │ Port: 27018  │  │ Port: 9002/3 │  │  Server   │ │
│  │ (Replica Set)│  │ (S3 Storage) │  │ Port: 3000│ │
│  └──────────────┘  └──────────────┘  └───────────┘ │
│         ▲                 ▲                 ▲       │
│         │                 │                 │       │
│         └─────────────────┴─────────────────┘       │
│                           │                         │
└───────────────────────────┼─────────────────────────┘
                            │
                    ┌───────▼────────┐
                    │  Python Tests  │
                    │  (pytest)      │
                    └────────────────┘
```

### Service Details

| Service | Port | Description |
|---------|------|-------------|
| **mongodb** | 27018 | MongoDB test database (replica set for Prisma) |
| **minio** | 9002 (API), 9003 (Console) | S3-compatible object storage |
| **dreamlake-server** | 3000 | GraphQL API server |

**Note**: Ports are different from development to avoid conflicts:
- MongoDB: 27018 (dev uses 27017)
- MinIO: 9002/9003 (dev uses 9000/9001)

## Configuration Files

### docker/docker-compose.test.yml

The Docker Compose file that orchestrates the test environment. Key features:

- **MongoDB Replica Set**: Required for Prisma transactions
- **Health Checks**: Ensures services are ready before tests run
- **Init Containers**: Automatically sets up MongoDB replica set and MinIO bucket
- **Isolated Volumes**: Test data doesn't interfere with development

### dreamlake-server/.env.test

Server configuration for test environment:

```bash
# Database
DATABASE_URL=mongodb://localhost:27018/dreamlake-test

# S3 Configuration (MinIO)
S3_BUCKET=dreamlake-test
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=minioadmin
AWS_SECRET_ACCESS_KEY=minioadmin
AWS_ENDPOINT=http://localhost:9002
S3_FORCE_PATH_STYLE=true

# JWT Configuration
JWT_SECRET=your-secret-key-change-this-in-production

# Server Configuration
PORT=3000
NODE_ENV=test
```

### dreamlake-py/.env.test

Python SDK configuration for running remote tests:

```bash
# DreamLake Server URL
DREAMLAKE_SERVER_URL=http://localhost:3000

# Test credentials
DREAMLAKE_TEST_USER=test-user

# S3 Configuration (optional, for direct S3 testing)
AWS_ENDPOINT=http://localhost:9002
AWS_ACCESS_KEY_ID=minioadmin
AWS_SECRET_ACCESS_KEY=minioadmin
S3_BUCKET=dreamlake-test
```

## Running Specific Tests

### By Test File

```bash
# Run only session tests
uv run pytest test/test_session.py -v

# Run only file upload tests
uv run pytest test/test_files.py -v

# Run only integration tests
uv run pytest test/test_integration.py -v
```

### By Test Class or Function

```bash
# Run specific test class
uv run pytest test/test_logging.py::TestBasicLogging -v

# Run specific test function
uv run pytest test/test_logging.py::TestBasicLogging::test_simple_log_remote -v
```

### By Marker

```bash
# Run only local mode tests
uv run pytest test/ -v -m local_only

# Run only remote mode tests
uv run pytest test/ -v -m remote_only

# Run tests excluding remote tests
uv run pytest test/ -v -m "not remote"
```

## Test Coverage

The test suite covers all major DreamLake features:

### Session Management
- Context manager and manual open/close
- Session metadata and properties
- Multiple sessions in same workspace
- Error handling and data persistence
- Session reopening

### Logging
- Basic logging with different levels
- Log metadata and sequence numbers
- Progress logging and exception tracking
- Rapid logging (stress test)

### Parameters
- Simple and complex parameter types
- Nested parameters
- Parameter updates
- Large parameter sets

### Tracks (Metrics)
- Single and multiple track append
- Batch append operations
- Flexible schema with multi-field tracking
- Track statistics and data reading
- Listing tracks
- Frequent tracking (stress test)

### File Uploads
- Single and multiple file uploads
- File metadata and tags
- Various file types (text, binary, JSON, images)
- Large file handling (10MB+)
- File listing

### Integration Workflows
- Complete ML workflow (train → eval → save model)
- Hyperparameter search workflows
- Multi-session pipelines
- Kitchen sink (all features combined)

## MinIO Console Access

Access the MinIO web console to inspect uploaded files:

```
URL: http://localhost:9003
Username: minioadmin
Password: minioadmin
```

You can browse the `dreamlake-test` bucket to see:
- Uploaded session files
- File metadata
- Storage structure

## Troubleshooting

### Server Won't Start

```bash
# Check logs
docker compose -f docker-compose.test.yml logs dreamlake-server

# Rebuild server container
docker compose -f docker-compose.test.yml build dreamlake-server
docker compose -f docker-compose.test.yml up -d
```

### MongoDB Connection Issues

```bash
# Check MongoDB is healthy
docker compose -f docker-compose.test.yml ps mongodb

# Connect to MongoDB shell
docker exec -it dreamlake-test-mongodb mongosh dreamlake-test
```

### Remote Tests Failing with 401 Unauthorized

This usually means JWT secret mismatch. Verify that:

1. `dreamlake-server/.env.test` has `JWT_SECRET=your-secret-key-change-this-in-production`
2. Python SDK is using the same secret (hardcoded in `src/dreamlake/session.py:164`)

If you changed the secret, restart the server:

```bash
docker compose -f docker-compose.test.yml restart dreamlake-server
```

### Remote Tests Failing with 500 Internal Server Error

Check server logs for details:

```bash
docker compose -f docker-compose.test.yml logs dreamlake-server --tail 100
```

Common causes:
- MongoDB not running as replica set
- MinIO endpoint not configured in S3Service
- S3 credentials invalid

### Reset Test Environment

If tests are failing mysteriously, try a clean reset:

```bash
# Stop all services and remove volumes
docker compose -f docker-compose.test.yml down -v

# Restart fresh
docker compose -f docker-compose.test.yml up -d

# Wait for services to be healthy
sleep 15

# Run tests again
cd ../dreamlake-py && uv run pytest test/ -v
```

## Development Workflow

### Making Changes to the SDK

1. **Make your changes** in `src/dreamlake/`

2. **Run local tests** to verify basic functionality:
   ```bash
   uv run pytest test/ -v -m "not remote"
   ```

3. **Run remote tests** to verify server integration:
   ```bash
   cd docker && docker compose -f docker-compose.test.yml up -d
   cd ../dreamlake-py && uv run pytest test/ -v -m remote
   ```

4. **Run all tests** before committing:
   ```bash
   uv run pytest test/ -v
   ```

### Making Changes to the Server

If you're also developing the DreamLake server:

1. **Make changes** in `dreamlake-server/src/`

2. **Restart server** to pick up changes:
   ```bash
   docker compose -f docker-compose.test.yml restart dreamlake-server
   ```

3. **Run integration tests**:
   ```bash
   cd dreamlake-py && uv run pytest test/ -v -m remote
   ```

**Note**: The Docker Compose test setup mounts `dreamlake-server` as a volume, so server code changes are reflected after restart. However, dependency changes require rebuilding:

```bash
docker compose -f docker-compose.test.yml build dreamlake-server
docker compose -f docker-compose.test.yml up -d
```

## Continuous Integration

For CI/CD pipelines, here's a complete workflow:

```bash
#!/bin/bash
set -e

# Start test environment
cd docker
docker compose -f docker-compose.test.yml up -d

# Wait for services to be healthy
echo "Waiting for services to start..."
sleep 15

# Verify health
curl -f http://localhost:3000/health || exit 1

# Run all tests
cd ../dreamlake-py
uv run pytest test/ -v --junitxml=test-results.xml

# Cleanup
cd ../docker
docker compose -f docker-compose.test.yml down -v
```

## Performance Testing

The test suite includes stress tests for high-frequency operations:

```bash
# Run only stress tests
uv run pytest test/ -v -k "rapid or frequent"
```

These tests verify that DreamLake can handle:
- **Rapid logging**: 100 log messages in quick succession
- **Frequent tracking**: 1000 metric appends
- **Large parameter sets**: 100+ parameters
- **Large files**: 10MB+ file uploads

## Next Steps

- ✅ Tests passing locally and remotely
- → [Architecture](architecture.md) - Understand DreamLake internals
- → [Deployment](deployment.md) - Deploy your own server
- → [Contributing](https://github.com/fortyfive-labs/dreamlake/blob/main/CONTRIBUTING.md) - Contribute to DreamLake
