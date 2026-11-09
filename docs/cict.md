# CI/CT with GitHub Actions

Use `client.cict` for CI/CT channel A: GitHub Actions jobs that ask for a
DreamLake label (`runs-on: dreamlake-<pool>`) run on DreamLake machines through
single-use runners, and the evidence they declare is written to a Source. The
flow, the workflow contract and the evidence files are described in the product
guide, [CI/CT with GitHub Actions](https://docs.dreamlake.ai/lakeshore/cict).
The same operations are available as `dl cict` in the CLI and as
`dl.cict(namespace)` in TypeScript. Added in Python package 0.50.0.

The server must have the GitHub runner integration enabled; otherwise every call
raises `CictNotFound` with code `github_runners_disabled`.

## Connect GitHub

```python
from dreamlake import DreamLakeClient

client = DreamLakeClient()
print(client.cict.github.connect("team"))       # GitHub App install link
client.cict.github.installations("team")         # bindings, after the browser step
```

Open the link within 15 minutes in a browser signed in to DreamLake as the same
user, install the App and select repositories. GitHub returns to the DreamLake
setup page, which binds the installation to the namespace.

## Runner pools

```python
pool = client.cict.pools.create(
    "team",
    name="bos14-gpu",                         # serves runs-on: dreamlake-bos14-gpu
    provider_id="0123456789abcdef01234567",   # Slurm provider with your association
    evidence_source_id="1123456789abcdef01234567",
    env={"DREAMLAKE_ROBOT_ASSETS_CACHE": "/work/assets"},  # machine facts, never secrets
)
client.cict.pools.list("team")
client.cict.pools.delete("team", pool["id"])
```

`labels=[...]` replaces the default label; each must be `dreamlake` or start
with `dreamlake-`. `evidence_prefix` changes the parent folder (default `ci`).

## Runs and results

```python
for run in client.cict.runs.list("team", limit=20):
    print(run["id"], run["status"], run["conclusion"], run["routing"]["kind"])

run = client.cict.runs.get("team", "91234567-48812345678")
result = client.cict.runs.result("team", run["id"])  # result.json, or None
```

A run ID is `<installationId>-<jobId>`. `runs.result` reads a JSON evidence file
(`file="metrics.json"` for another) through a short-lived Source read
capability; it returns `None` when the run has no evidence location or did not
write the file. `client.cict.jobs("team")` lists the raw recorded GitHub jobs.

## Compare with the baseline

```python
c = client.cict.runs.comparison("team", run["id"])   # None until evidence is written
if c and c["baseline"]:
    print(c["regressions"], "regressed vs", c["baseline"]["branch"])
    for case in c["cases"]:
        if case["change"] != "unchanged":
            print(case["change"], case["id"], case.get("score"))
```

## Edit pools

```python
client.cict.pools.update("team", pool["id"], provider_id="0123456789abcdef01234567")
client.cict.pools.update("team", pool["id"], no_evidence=True, env=None)   # stop evidence, remove env
client.cict.pools.targets("team")   # providers and whether a pool can start runners there
```

Edits apply to the next job. Deleting a pool that still serves running jobs
raises `CictError` with code `runner_pool_busy`.

## DreamLake GPU

```python
status = client.cict.platform.status("team")      # catalog, access, limit, occupancy, month GPU time
client.cict.platform.start("team")                 # creates cict-evidence; you authorize evidence writes
client.cict.platform.set_source("team", "1123456789abcdef01234567")
client.cict.usage("team", month="2026-11")         # GPU time by repository and workflow
```

Workflows then use a label from `status["machines"]`, such as
`runs-on: dreamlake-gpu-rtx5090`. Admins of the platform namespace manage it
with `client.cict.admin`: `overview()`, `set_namespace(slug, state="allowed",
max_concurrent=2)` and `put_machine(id, label=..., provider_id=..., enabled=True)`.

Errors are `CictError` (and `CictNotFound` for HTTP 404) from
`dreamlake.api.cict`, with `status` and the server's `code` when recognized.
Server error text is not included.
