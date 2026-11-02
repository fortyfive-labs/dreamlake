# Dash writer (managed Dash)

`dreamlake.experiments.dash_writer(namespace, project)` logs a run to the project's
built-in Dash service. DreamLake stays the only identity: the writer asks
dreamlake-server for a 60-second, project-scoped token
(`POST /managed-experiments/ns/{ns}/projects/{p}/session` with `{"mode": "write"}`)
and calls Dash's managed API (`{apiUrl}/projects/{projectId}/...`) with it. No Dash
account is involved. For the DreamLake-native store use
[`DreamLakeClient().experiments`](native-experiments.md) instead.

```python
from dreamlake import DreamLakeClient
from dreamlake.experiments import dash_writer, DashWriterError

client = DreamLakeClient(token="...")       # or the saved login / DREAMLAKE_API_KEY

with dash_writer("team", "research", client=client, run_key="trial_1") as run:
    run.params.set(lr=3e-4, model={"depth": 4})   # stored as lr, model.depth
    run.log("training started")                   # level: info|warn|error|debug|fatal

    train = run.metrics("train")
    for step in range(10_000):
        train.log(step=step, loss=..., acc=...)  # metrics "train.loss" and "train.acc"

    run.files("checkpoints").upload("model.pt")   # stored as "checkpoints__model.pt"

    page = run.metrics("train").read("loss", limit=1000)
    page["rows"], page["nextCursor"]             # pass cursor=page["nextCursor"] for more
# leaving the block flushes buffers and sets COMPLETED
# (FAILED on an exception, CANCELLED on KeyboardInterrupt)
```

Without `with`: `run = dash_writer(...).open()` … `run.close("COMPLETED")`.

## Behaviour

- **Setup.** `open()` checks `GET .../status`. If Dash is not set up it raises
  `DashWriterError` (`MANAGED_SETUP_REQUIRED`) unless `auto_setup=True`, which runs the
  plan-hash setup (`GET .../setup`, then `POST .../setup` with `expectedRevision`,
  `planHash` and a fresh `idempotencyKey`). Setup needs WRITE on the project.
- **Tokens.** Re-minted lazily before a request when fewer than `refresh_margin`
  seconds (default 15) remain, reusing the first session's workload credential
  (`workloadId`). A 401 re-mints once. Tokens are never logged or put in URLs, and the
  writer refuses a non-HTTPS `apiUrl` (loopback excepted).
- **Runs.** `run_key` names the run idempotently (`PUT .../runs/{run_key}`); reusing a key
  reopens the run. Default: `run-<UTC timestamp>-<random>`. Entering sets `RUNNING`.
- **Metrics.** `metrics(name).log(step=None, **fields)` buffers one point
  `{"step", "value", "timestamp"}` per field into metric `<name>.<field>` (just `<field>`
  for `metrics()` / `metrics.log`). A metric's buffer is sent at 1000 points
  (`flush_every`), on `run.flush()`, before a `read()` and on exit. Points from a failed
  flush stay buffered.
- **Reads.** `read(field, limit=1..10000, cursor=None)` returns committed rows only, in
  storage order, paged by an opaque cursor (the server has no start index). Very recent
  points may not be visible yet.
- **Files.** Up to 8 MiB each, sent as JSON (`filename`, `dataBase64`, `contentType`).
  Dash's managed API stores files flat, so a prefix is folded into the name with `__`.
  `run.files().list()` and `run.files().download(file_id, dest=None)` read them back.
- **Retries.** Bounded (5), jittered backoff on HTTP 409 `OPERATION_CONFLICT`, and on
  HTTP 503 / transport errors for idempotent requests only (run create, parameters,
  status, reads). Appends (metrics, logs, files) are not retried on 503, since the
  write may have landed.

## Errors

`DashWriterError` (a subclass of `ExperimentRequestError`) carries `status`, `code`
(the server's `error.code`) and `body`; its message never includes the body or a token.

`409 MANAGED_LEGACY_READ_ONLY` means dreamlake-server refuses managed Dash writes because
`DREAMLAKE_NATIVE_EXPERIMENTS_ENABLED` is on. A dedicated switch,
`DASH_MANAGED_WRITES_ENABLED`, is being added server-side to allow them; until it is
deployed, log through the native client.
