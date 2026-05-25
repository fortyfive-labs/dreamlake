# Deployment

Remote mode requires a backend server with MongoDB, S3-compatible storage, and JWT authentication.

## Docker Compose

```yaml
version: '3.8'

services:
  dreamlake-server:
    image: dreamlake/server:latest
    ports:
      - "3000:3000"
    environment:
      PORT: 3000
      NODE_ENV: production
      MONGODB_URI: mongodb://mongo:27017/dreamlake
      S3_ENDPOINT: http://minio:9000
      S3_ACCESS_KEY: minioadmin
      S3_SECRET_KEY: minioadmin
      S3_BUCKET: dreamlake-files
      S3_REGION: us-east-1
      JWT_SECRET: change-this-in-production
      JWT_EXPIRATION: 30d
    depends_on:
      - mongo
      - minio

  mongo:
    image: mongo:6.0
    ports:
      - "27017:27017"
    volumes:
      - mongo-data:/data/db

  minio:
    image: minio/minio:latest
    ports:
      - "9000:9000"
      - "9001:9001"
    volumes:
      - minio-data:/data
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    command: server /data --console-address ":9001"

  minio-setup:
    image: minio/mc:latest
    depends_on:
      - minio
    entrypoint: >
      /bin/sh -c "
      until /usr/bin/mc alias set myminio http://minio:9000 minioadmin minioadmin; do sleep 1; done;
      /usr/bin/mc mb myminio/dreamlake-files || true;
      exit 0;
      "

volumes:
  mongo-data:
  minio-data:
```

```bash
docker-compose up -d
curl http://localhost:3000/health
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MONGODB_URI` | Yes | — | MongoDB connection string |
| `S3_ACCESS_KEY` | Yes | — | S3 access key |
| `S3_SECRET_KEY` | Yes | — | S3 secret key |
| `S3_BUCKET` | Yes | — | S3 bucket name |
| `JWT_SECRET` | Yes | — | JWT signing secret |
| `PORT` | No | 3000 | Server port |
| `S3_ENDPOINT` | No | — | S3 endpoint (for MinIO) |
| `S3_REGION` | No | us-east-1 | S3 region |
| `JWT_EXPIRATION` | No | 30d | Token expiration |
| `MAX_FILE_SIZE` | No | 100MB | Max upload size |

## Authentication

Generate a strong JWT secret:

```bash
openssl rand -base64 64
```

**Development:** `user_name` auto-generates JWT tokens from the username. The SDK and server must share the same `JWT_SECRET`.

**Production:** Use a proper auth service and pass real JWT tokens via `api_key`.

## Backups

```bash
# MongoDB
mongodump --uri="mongodb://localhost:27017/dreamlake" --out=/backup/$(date +%Y%m%d)

# S3
aws s3 sync s3://dreamlake-files s3://dreamlake-files-backup
```
