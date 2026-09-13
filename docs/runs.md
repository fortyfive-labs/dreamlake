# Tracked runs

This development branch adds `client.runs` against the shared tracked-run API.
Backend integration and remote workload acceptance are still in progress; this
page does not claim a released service. Use an enrolled host with the process
runner and working uv. Provider/Slurm/Kubernetes execution is a separate scope.

Only explicitly included files are uploaded: no Git checkout discovery, recursive
folder upload, or automatic `.env`/key inclusion. Paths must be relative regular
files without symlinks or traversal. The first slice permits 100 files and 1 MiB
of decoded contents in one atomic manifest, with hashes and executable modes.

::::{tab-set}
:::{tab-item} Python
```python
from dreamlake import DreamLakeClient
client = DreamLakeClient()
run = client.runs.submit(
    "fortyfive/lab/lab-box", kind="uv-run",
    argv=["train.py", "--epochs", "10"],
    include=["train.py", "pyproject.toml"],
    request_id="training-request-1",
)
result = client.runs.wait("fortyfive", run["id"], timeout_seconds=600)
print(result["status"], result["exitCode"])
```
:::
:::{tab-item} CLI
```shell
dreamlake run --target fortyfive/lab/lab-box \
  --include train.py --include pyproject.toml \
  --request-id training-request-1 --uv-run train.py --epochs 10
```
:::
::::

Python `submit` returns the durable run record immediately. `wait` is explicit;
a local wait timeout raises `RunWaitTimeout` and does not cancel remote work.
Remote failure is a terminal record with an exit code, not a library process exit.
To execute a packaged tool without local files, use `kind="uvx"` and an argument
list such as `["--from", "my-package", "my-command"]`.

All DreamLake CLI flags precede `--uv-run` or `--uvx`; every following argument
belongs to the workload, including `--json`, `--help`, spaces, and empty strings.
Python accepts that argument vector directly and never evaluates a shell string.
The remote timeout is `timeout_seconds` on submission (default 3600 seconds).

## Status, logs, and cancellation

```python
import base64

run = client.runs.status("fortyfive", run["id"])
page = client.runs.logs("fortyfive", run["id"], limit=65536)
for entry in page["entries"]:
    data = base64.b64decode(entry["dataBase64"])  # bytes, possibly partial UTF-8
    # Send bytes to the appropriate stdout/stderr sink.
next_page = client.runs.logs("fortyfive", run["id"], cursor=page["nextCursor"])
requested = client.runs.cancel("fortyfive", run["id"])
```

Log pages preserve separate stdout/stderr byte streams. Use an incremental UTF-8
decoder per stream if displaying text; page boundaries may split a character.
`done` means the run is terminal and all available bytes were returned. Reusing a
cursor replays the same page. Cancellation records durable intent; a
`cancel_requested` response does not prove the process has stopped. Confirm a
terminal status with `status` or `wait`.

Run states are `queued`, `running`, `cancel_requested`, `succeeded`, `failed`,
`cancelled`, and `timed_out`. Reuse the same request ID and identical source and
arguments after an uncertain submission. Changed replay payloads conflict;
clients never retry writes automatically. Authentication and conflict failures
raise sanitized `RunAuthorizationError` / `RunConflictError` with HTTP status
and request/run IDs. Source contents and server response bodies are not copied
into error messages. Calls never prompt, expose control-plane admin credentials,
or implicitly save SSH credentials.
