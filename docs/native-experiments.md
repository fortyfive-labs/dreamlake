# Native Experiments

`DreamLakeClient(...).experiments` logs runs straight to a DreamLake project over REST
(`/native-experiments/ns/{namespace}/projects/{project}/...`).

```python
from dreamlake import DreamLakeClient

exp = DreamLakeClient(token="...").experiments
ns, project = "team", "research"

run = exp.create_run(ns, project, "trial_1")            # idempotent per key
rid = run["id"]
exp.set_parameters(ns, project, rid, {"lr": 3e-4})
exp.append_metric(ns, project, rid, "loss", [{"step": 0, "value": 1.2}], retry_conflicts=True)
exp.upload_file(ns, project, rid, "model.pt", tags=["checkpoint"])
exp.set_status(ns, project, rid, "COMPLETED")

page = exp.read_metric(ns, project, rid, "loss", start_index=0, limit=100)
page["rows"], page["nextCursor"]                          # nextCursor is None on the last page
```

Files: `upload_file` (direct multipart; size and sha256 are sent and verified by the server),
`list_files`, `get_file`, `download_file(..., dest=None)`.
`prefix` is an absolute run file folder such as `/checkpoints` (default `/`).
Version 0.45.1 preserves Unicode, quotes and literal percent sequences in uploaded filenames.

`delete_run` moves a run to trash; `restore_deleted` restores the same ID.
Lifecycle-enabled servers also allow running runs to be trashed; older servers
require a terminal run.

Errors: `ExperimentRequestError` carries `status`, `code` (server `error.code`, e.g.
`OPERATION_CONFLICT`, `NATIVE_RUN_ACTIVE`) and `body`. Write methods take
`retry_conflicts=True` to retry (up to 5 times, jittered backoff) only on HTTP 409
`OPERATION_CONFLICT`; it is off by default and nothing else is retried.

For the built-in Dash service (ml-dash managed API) see [dash-writer.md](dash-writer.md).

## Record a complete experiment

Version 0.46.0 adds the full recording API to `dreamlake`; it does not require
`ml-dash`. Use the login saved by `dl login`, or pass a token to `DreamLakeClient`.

```python
from dreamlake import DreamLakeClient

project = DreamLakeClient().experiments.project("team", "research")
with project.run("sweeps/trial-1", tags=["baseline"]) as run:
    run.params.set(model={"depth": 4}, lr=3e-4)
    run.logs.info("training started")
    run.metrics("train").log(step=0, loss=1.2)
    run.tracks("robot/pose").append(x=1.0, _ts=0.0)
    run.files("checkpoints").upload("model.pt")
    run.flush()
    print(run.id, run.writer_id)
```

The context flushes pending writes and sets `COMPLETED` on success or `FAILED`
on an exception. `run.run.start()/complete()/fail()` and `@experiment(...)`
(with an `experiment` keyword argument in the decorated function) are also available.
Parameters flatten nested dictionaries to dotted keys. Metrics support named and
root logging, buffering, summary caches and typed `read_rows()` pages. Tracks
support timestamps, `_ts=-1` inheritance, time bounds and JSON/JSONL/Arrow IPC/MCAP
exports; the server's `parquet` format name returns an Arrow IPC file.

Files support glob selection, downloads verified by SHA-256, metadata and Bindr
membership, and JSON, text, binary, pickle, NumPy-compatible image, figure, Torch
and video helpers through `run.files(...)`. Install `dreamlake[video]` for video;
Torch and plotting helpers use the corresponding libraries already in your environment.

### Local and hybrid recording

```python
from dreamlake import Experiment

with Experiment(prefix="team/research/trial-1", root=".dreamlake/experiments") as run:
    run.metrics("train").log(step=0, loss=1.2)

# Keep a local copy while also recording in the selected DreamLake server.
with Experiment(prefix="team/research/trial-2", url="https://api.dreamlake.ai",
                root=".dreamlake/experiments") as run:
    run.params.set(seed=7)
```

Local data uses the `.dash` experiment directory format, so existing experiment
archives can be transferred without a runtime dependency on the old SDK.
`RUN` provides prefix templates and context/decorator configuration.
`DREAMLAKE_EXPERIMENT_PREFIX`, `DREAMLAKE_REMOTE` and `DREAMLAKE_NAMESPACE`
select defaults. Buffer settings use `DREAMLAKE_EXPERIMENT_BUFFER_ENABLED`,
`DREAMLAKE_EXPERIMENT_FLUSH_INTERVAL`, `DREAMLAKE_EXPERIMENT_LOG_BATCH_SIZE`,
`DREAMLAKE_EXPERIMENT_METRIC_BATCH_SIZE` and `DREAMLAKE_EXPERIMENT_TRACK_BATCH_SIZE`.
The corresponding `ML_DASH_*` settings remain accepted for compatibility.

### Manage project data

`project` also exposes these native operations:

| Accessor | Operations |
| --- | --- |
| `runs` | create/get/list, metadata, move, protection, archive, status, trash/restore/purge |
| `parameters`, `logs` | parameter get/set/delete; append and filtered paginated logs |
| `metrics` | list/append, cursor pages, typed rows, stats and metadata |
| `tracks` | create/list/append; bounded paginated reads and format exports |
| `files`, `files.folders` | run file and file-directory CRUD, download and media URLs |
| `folders` | project run directories, metadata/moves, asynchronous subtree operations |
| `project_files` | README and `.dashrc` files, text, content replacement, move and Bindrs |
| `bindrs` | catalog/create/get/update/delete and run/item/folder/project-file membership |
| `writers` | create/list/inspect/rotate/retire and short-lived sessions |
| `jobs` | submit explicit source files and argv to a configured target, list/status/logs/cancel |

`project.series`, `aggregate_series`, `chart_series`, `row`, `search_files` and
`by_path` expose the corresponding native reads. Dictionary payloads use the
server's field names. Pass the ID of an accepted folder operation to
`project.folders.wait(operation_id)`; a timeout does not resubmit it.
File directory paths are absolute within the run (`/checkpoints`); the SDK also
accepts `checkpoints` in its file-directory create/update helpers.

### Run inside a DreamLake job

```python
from dreamlake import Experiment

with Experiment() as run:
    run.params.set(seed=7)
    run.metrics("train").log(step=0, loss=1.2)
```

The worker's reporting broker supplies the existing run and renewable short-lived
session. No user login is needed on the worker. Broker configuration takes precedence;
an unavailable or changed broker never falls back to a user's login or another run.
The compute receipt controls the job run's terminal status; closing the recording
context only flushes data. `project.jobs.submit(...)` accepts an explicitly configured
target, argv and a mapping of relative source filenames to bytes, text or `Path` objects.

Historical managed Dash runs keep their existing data home and remain accessible
through the managed writer. The native API never silently migrates or duplicates them.
