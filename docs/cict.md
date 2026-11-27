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

## Where a namespace stands

```python
status = client.cict.onboarding("team")
status["ready"], status["route"]          # True/False, "dreamlake-gpu" or "own-machines"
status["next"]                            # {"key", "title", "how": {"ui", "cli", "api", "human"}}
status["workflow"]                        # once ready: the runs-on line and the evidence contract
```

Each of `status["github"]`, `status["platform"]` and `status["ownMachines"]`
(enroll the login node, service account, Slurm provider, association, launch
layer, runner pool) says whether it is `done`. An agent can repeat this call
and do `next` until `ready`. Only installing the GitHub App needs a person in a
browser; enrolling a login node needs SSH and binding its service account a
namespace owner. Added in 0.53.0.

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
metrics = client.cict.runs.result("team", run["id"], file="metrics.json")  # or None
```

A run ID is `<installationId>-<jobId>`. `runs.result` reads one JSON file the
run declared as evidence (`file=`, relative to its evidence folder; it defaults
to `result.json` for existing callers) through a short-lived Source read
capability; it returns `None` when the run has no evidence location or did not
write the file. `client.cict.jobs("team")` lists the raw recorded GitHub jobs.

## Run a workflow from DreamLake

```python
d = client.cict.dispatch("team", repository="acme/sim", workflow="cict.yml",
                         ref="isaac-sim", inputs={"sim_seconds": "120"})
print(d["state"], d.get("workflowRunUrl"))     # dispatched | failed | unknown
client.cict.dispatches("team", limit=10)
```

`dispatch` sends GitHub's `workflow_dispatch` through the namespace's GitHub
App installation (needs `lakeshore:use`); the workflow must declare
`workflow_dispatch:` and each input. `ref` defaults to the default branch and
`installation_id` to the one active installation. `failed` carries GitHub's
reason in `message`; `unknown` means the answer was lost, so check the
repository's Actions page before running it again. Runs started this way carry
`dispatchedBy`.

CI/CT does not compare runs with a baseline or read a result format: a job's
own steps decide whether it passes. `runs.comparison` was removed; the server
answers `410 comparison_removed`.

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
usage = client.cict.usage("team", month="2026-11")  # totals, byWorkflow, bySender, runs
client.cict.usage("team", sender="ada", limit=50)    # one person's jobs, newest first
```

`status["authorizer"]` names who authorizes evidence writes and whether they
still can (`writable`). While it is false, jobs wait in the queue; a namespace
admin calls `set_source` with the current Source to become the authorizer.

Workflows then use a label from `status["machines"]`, such as
`runs-on: blacksmith-4vcpu-gpu-rtx5090-ubuntu-2404`. Admins of the platform namespace manage it
with `client.cict.admin`: `overview(month=...)` (namespaces, machine types and
the month's platform total), `providers()` (Slurm providers in the platform
namespace and what one job gets on each), `set_namespace(slug,
state="allowed", max_concurrent=2)` and `put_machine(id, label=...,
provider_id=..., enabled=True)`. A machine type's spec (GPUs, CPUs, memory,
time limit) is read from the provider's launch layer, not passed in.

Errors are `CictError` (and `CictNotFound` for HTTP 404) from
`dreamlake.api.cict`, with `status` and the server's `code` when recognized.
Server error text is not included.

## Local test harness and decision graphs

Added in Python package 0.62.0. The `dreamlake.cict` module defines tests and
records their execution locally.
It is separate from `client.cict`, the authenticated CI/CT control-plane API
above. Creating a suite or run does not dispatch jobs, allocate machines, upload
files, read terminal output, or index anything.

```python
from dreamlake.cict import Suite

suite = (Suite("simulation-smoke")
    .node("pick", kind="decision", attributes={"suite": "simulation"})
    .node("quick", attributes={"suite": "simulation", "device": "cpu"})
    .node("extended", attributes={"suite": "simulation", "device": "gpu"})
    .edge("quick-path", "pick", "quick", condition="quick check requested")
    .edge("extended-path", "pick", "extended", condition="extended check requested"))
suite.write("outputs/cict-suite.json")
# Definitions can also be loaded with Suite.read("outputs/cict-suite.json").

run = suite.run("commit-sha-attempt-1", on_event=lambda event: None)
run.select("pick", "quick-path")
with run.test("quick"):
    assert 1 + 1 == 2
    run.finish("quick", "passed", metrics={"durationMs": 0})
run.write("outputs/cict.json")
run.write_events("outputs/cict-events.jsonl")
```

Supply stable suite, test/node, and edge IDs yourself; use a distinct run ID for
an attempt or retry. All declared nodes appear in every snapshot, including
unvisited branches. The extended test above remains `not_run`, its edge is
`selected: false`, and its metric is absent. Before a decision, all outgoing
edges have `selected: null`. A condition is descriptive text, never executable
code. The harness does not schedule dependent nodes or infer their outcomes.

A static definition and a runtime snapshot use the same
`schemaVersion: "dreamlake.cict/v1"`, `suiteId`, `records`, and `edges`. Runtime
snapshots add `kind: "run"` and `runId`; definitions use `kind: "suite"`.
Each record has `id`, `testId`, `name`, `kind` (`test`, `decision`, or `group`),
`status`, `attributes`, `metrics`, and an optional `parentId`. Declare parents
before children. Edges have `id`, `source`, `target`, and `selected`, with
optional `label` and `condition`. Dependency edges must be acyclic.

The statuses are `not_run`, `running`, `passed`, `failed`, `skipped`, and
`unknown`. Missing grid cells are a visualization concern, not a successful
test. Start a node before passing or failing it. Explicitly skip a node with
`run.finish(id, "skipped")`; use `unknown` when execution cannot establish an
outcome. Finished nodes cannot restart within a run. The `test` context manager
marks an uncaught assertion as failed and another exception as unknown,
propagates the exception, and never stores its message or output. A normally
completed block passes unless it already finished explicitly. Avoid declaring
a pass before the test's assertions are complete.

While a node is running, call `run.record(id, metrics={"steps": 5})` to emit
explicit live measurements without finishing it.

Metrics are finite numbers keyed by a name that includes its unit, such as
`durationMs`; zero remains zero, and missing values are absent. Attributes are
explicit JSON values with string keys. Snapshots and callback payloads are
detached copies. A run uses one writer; it is not a concurrent scheduler.

`on_event` receives synchronous events with the schema version, suite/run IDs,
a monotonically increasing `sequence`, UTC `time`, and `type`. `run.created`
contains the initial `snapshot`, `node.updated` contains the full `record`, and
`decision.selected` contains `nodeId` and all outgoing `edges` together. This
lets a caller render a live decision graph without losing untraversed
alternatives. Callback errors propagate after the state/event is recorded;
there is no automatic retry. `run.events()` returns the recorded event stream.

The [CPU-only example](https://github.com/fortyfive-labs/dreamlake/tree/main/examples/cict)
includes a `.dreamrc` heatmap configuration mapping rows to
`kind` (retaining decision nodes), columns to `testId`, values to `metrics.durationMs`, and
status to `status`. The file names are intentional: **`results.json` is the
existing CI/CT evidence manifest**, not a test snapshot. Use `cict.json` for
records, and explicitly include the harness files when saving evidence with
`dreamlake-ai/cict-evidence@v1`. Nothing is uploaded merely by using the harness.
