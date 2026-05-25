# CLI

```bash
pip install dreamlake=={VERSION}
```

## Episode File Commands

```bash
# Upload
dreamlake upload --file ./model.pt --workspace my-ws --episode exp-001
dreamlake upload --file ./model.pt --workspace my-ws --episode exp-001 \
    --path /models --tags checkpoint,final

# Download
dreamlake download --file-id abc123 --workspace my-ws --episode exp-001

# List
dreamlake list --workspace my-ws --episode exp-001
dreamlake list --workspace my-ws --episode exp-001 --path /models --json-output
```

Add `--url` and `--api-key` for remote mode.

## Video Commands (BSS)

```bash
# Upload
dreamlake video upload ./demo.mp4 --user alice --project robotics
dreamlake video upload ./experiment.mp4 --name "Training Run 42" \
    --user alice --project robotics --tags training,final

# Download
dreamlake video download abc123 --output ./my_video.mp4

# List
dreamlake video list --user alice --project robotics
dreamlake video list --json-output --limit 20
```

## Environment Variables

```bash
# Episode commands
export DREAMLAKE_REMOTE="http://localhost:3000"
export DREAMLAKE_API_KEY="your-jwt-token"
export DREAMLAKE_LOCAL_PATH=".dreamlake"

# Video commands (BSS)
export DREAMLAKE_BSS_URL="http://localhost:4000"
export DREAMLAKE_BSS_TOKEN="your-bss-jwt-token"
export DREAMLAKE_USER="alice"
export DREAMLAKE_PROJECT="robotics"
```
