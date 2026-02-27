# Command Line Interface

DreamLake provides a CLI for managing experiment data and videos from the terminal.

## Installation

```bash
pip install dreamlake
# or
uv pip install dreamlake
```

## Commands Overview

| Command | Description |
|---------|-------------|
| `upload` | Upload a file to a session |
| `download` | Download a file from a session |
| `list` | List files in a session |
| `video upload` | Upload a video to BSS |
| `video download` | Download a video from BSS |
| `video list` | List videos in BSS |

## Session File Commands

### Upload

Upload files to a session:

```bash
# Local mode (default)
dreamlake upload --file ./model.pt --workspace my-ws --session experiment-001

# With path prefix
dreamlake upload --file ./model.pt --workspace my-ws --session exp-001 --path /models

# With description and tags
dreamlake upload --file ./model.pt --workspace my-ws --session exp-001 \
    --description "Final trained model" \
    --tags checkpoint,final

# Remote mode
dreamlake upload --file ./model.pt --workspace my-ws --session exp-001 \
    --remote http://localhost:3000 \
    --api-key $DREAMLAKE_API_KEY
```

**Options:**

| Option | Description |
|--------|-------------|
| `--file` | Path to file to upload (required) |
| `--workspace` | Workspace name (required) |
| `--session` | Session/experiment name (required) |
| `--path` | Logical path prefix (default: `/`) |
| `--description` | Optional file description |
| `--tags` | Comma-separated tags |
| `--remote` | Remote API URL (uses local mode if not set) |
| `--api-key` | API key for remote mode |
| `--local-path` | Local storage path (default: `.dreamlake`) |

### Download

Download files from a session:

```bash
# Download file to current directory
dreamlake download --file-id abc123 --workspace my-ws --session exp-001

# Download file to specific path
dreamlake download --file-id abc123 --workspace my-ws --session exp-001 \
    --output ./downloaded_model.pt
```

**Options:**

| Option | Description |
|--------|-------------|
| `--file-id` | ID of the file to download (required) |
| `--workspace` | Workspace name (required) |
| `--session` | Session/experiment name (required) |
| `--output` | Output path (default: current directory) |
| `--remote` | Remote API URL |
| `--api-key` | API key for remote mode |
| `--local-path` | Local storage path |

### List

List files in a session:

```bash
# List all files
dreamlake list --workspace my-ws --session exp-001

# Filter by path
dreamlake list --workspace my-ws --session exp-001 --path /models

# Filter by tags
dreamlake list --workspace my-ws --session exp-001 --tags checkpoint

# JSON output
dreamlake list --workspace my-ws --session exp-001 --json-output
```

**Options:**

| Option | Description |
|--------|-------------|
| `--workspace` | Workspace name (required) |
| `--session` | Session/experiment name (required) |
| `--path` | Filter by path prefix |
| `--tags` | Filter by tags (comma-separated) |
| `--json-output` | Output as JSON |
| `--remote` | Remote API URL |
| `--api-key` | API key for remote mode |
| `--local-path` | Local storage path |

## Video Commands (BSS)

Video commands interact with Big Streaming Server (BSS) for video management.

### Video Upload

Upload videos to BSS:

```bash
# Basic upload
dreamlake video upload ./demo.mp4 --user alice --project robotics

# With custom name and tags
dreamlake video upload ./experiment.mp4 --name "Training Run 42" \
    --user alice --project robotics --tags training,final

# Using environment variables
export DREAMLAKE_BSS_TOKEN="your-jwt-token"
export DREAMLAKE_USER="alice"
export DREAMLAKE_PROJECT="robotics"
dreamlake video upload ./video.mp4
```

**Options:**

| Option | Description |
|--------|-------------|
| `<file>` | Video file path (positional, required) |
| `--name` | Video name (default: filename) |
| `--user` | User/owner name |
| `--project` | Project name |
| `--tags` | Comma-separated tags |
| `--description` | Video description |
| `--bss-url` | BSS server URL |
| `--token` | JWT authentication token |

### Video Download

Download videos from BSS:

```bash
# Download by video ID
dreamlake video download abc123

# Download to specific path
dreamlake video download abc123 --output ./my_video.mp4

# Use custom BSS server
dreamlake video download abc123 --bss-url http://bss.example.com:4000
```

**Options:**

| Option | Description |
|--------|-------------|
| `<video_id>` | BSS video ID (positional, required) |
| `--output` | Output path (default: `{video_name}.mp4`) |
| `--bss-url` | BSS server URL |

### Video List

List videos from BSS:

```bash
# List all videos
dreamlake video list

# Filter by user
dreamlake video list --user alice

# Filter by project
dreamlake video list --user alice --project robotics

# JSON output
dreamlake video list --json-output

# Pagination
dreamlake video list --limit 20 --offset 40
```

**Options:**

| Option | Description |
|--------|-------------|
| `--user` | Filter by user |
| `--project` | Filter by project |
| `--tags` | Filter by tags (comma-separated) |
| `--limit` | Max results (default: 50) |
| `--offset` | Offset for pagination |
| `--json-output` | Output as JSON |
| `--bss-url` | BSS server URL |

## Environment Variables

Set defaults using environment variables:

```bash
# Session file commands
export DREAMLAKE_REMOTE="http://localhost:3000"
export DREAMLAKE_API_KEY="your-jwt-token"
export DREAMLAKE_LOCAL_PATH=".dreamlake"

# Video commands (BSS)
export DREAMLAKE_BSS_URL="http://localhost:4000"
export DREAMLAKE_BSS_TOKEN="your-bss-jwt-token"
export DREAMLAKE_USER="alice"
export DREAMLAKE_PROJECT="robotics"
```

| Variable | Description |
|----------|-------------|
| `DREAMLAKE_REMOTE` | Default DreamLake API server URL |
| `DREAMLAKE_API_KEY` | Default API key (JWT token) |
| `DREAMLAKE_LOCAL_PATH` | Default local storage path |
| `DREAMLAKE_BSS_URL` | Default BSS server URL |
| `DREAMLAKE_BSS_TOKEN` | Default BSS JWT token |
| `DREAMLAKE_USER` | Default user for video operations |
| `DREAMLAKE_PROJECT` | Default project for video operations |

## Programmatic Usage

The CLI uses params-proto configuration classes that can be used programmatically:

```python
from dreamlake.cli import VideoUploadConfig
from dreamlake.cli.commands.video import cmd_upload

# Configure and run upload
VideoUploadConfig.file = "./video.mp4"
VideoUploadConfig.user = "alice"
VideoUploadConfig.project = "robotics"
VideoUploadConfig.token = "your-jwt-token"

result = cmd_upload(VideoUploadConfig)
```

## Examples

### Batch Video Upload

```bash
#!/bin/bash
# Upload all videos in a directory
for video in ./videos/*.mp4; do
    dreamlake video upload "$video" \
        --user alice \
        --project training \
        --tags batch,experiment
done
```

### CI/CD Integration

```yaml
# GitHub Actions example
- name: Upload training video
  env:
    DREAMLAKE_BSS_URL: ${{ secrets.BSS_URL }}
    DREAMLAKE_BSS_TOKEN: ${{ secrets.BSS_TOKEN }}
  run: |
    dreamlake video upload ./outputs/training.mp4 \
      --user ci \
      --project ${{ github.repository }} \
      --tags ci,${{ github.run_id }}
```

### Download and Process

```bash
# Download video and extract frames
VIDEO_ID=$(dreamlake video list --user alice --project demo --json-output | jq -r '.[0].id')
dreamlake video download "$VIDEO_ID" --output ./video.mp4
ffmpeg -i ./video.mp4 -vf fps=1 ./frames/frame_%04d.png
```
