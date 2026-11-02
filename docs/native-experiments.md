# Native Experiments client

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

Errors: `ExperimentRequestError` carries `status`, `code` (server `error.code`, e.g.
`OPERATION_CONFLICT`, `NATIVE_RUN_ACTIVE`) and `body`. Write methods take
`retry_conflicts=True` to retry (up to 5 times, jittered backoff) only on HTTP 409
`OPERATION_CONFLICT`; it is off by default and nothing else is retried.

For the built-in Dash service (ml-dash managed API) see [dash-writer.md](dash-writer.md).
