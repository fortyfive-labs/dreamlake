# Experiment workflow

Organize a sweep in Dash as **question → configs × seeds → attempts → results**. Requires a server whose
`status` lists the `experiments` capability. A complete example lives in
[`docs/examples/experiment_workflow/`](examples/experiment_workflow/).

## Three files, one order

`generate → commit → launch → review`

| File | Job |
|---|---|
| `*_generate.py` | Defines the combinations with params-proto v3 (`Sweep(Train).product`) and writes `grids/<name>.jsonl`. Each row `{slug, config, seed, params}`; the slug comes only from the configuration and seed (`grid_rows`). Commit it: it is the plan. |
| `train.py` | Plain training code: a `@proto.prefix` config, ordinary files out (`train.jsonl`, `eval.jsonl` with an absolute `step`, checkpoints). No DreamLake import. |
| `*_experiment_*_launch.py` | Reads the grid, selects slugs, runs training and records each attempt in Dash. Never redefines combinations. |

## Publish the experiment

```python
from dreamlake import DreamLakeClient
from dreamlake.experiments.workflow import ExperimentFolder

folder = ExperimentFolder(DreamLakeClient().experiments.project("team", "rl"), "ppo/lr-sweep")
folder.publish({"question": "...", "step": "step", "budget": 100_000, "baseline": "<config slug>",
                "metrics": [{"metric": "eval", "key": "return", "label": "Eval return", "goal": "max"}],
                "changed": ["train.lr"]}, grid="grids/lr.jsonl", readme="README.md")
```

The folder's id is the experiment's identity: rename or move it freely. `README.md` is the one summary and
conclusion; put the full analysis in a Memo and link it with `"memo"`.

## Attempts

Each attempt is an ordinary native run in the folder, named `<slug>.<attempt id>`.

```python
with folder.attempt(slug, params=row["params"], config=row["config"], seed=row["seed"]) as attempt:  # fresh
    attempt.sync_metric("eval", rows)          # appends only rows the run does not hold yet

folder.attempt(slug, kind="resume", parent="<attempt id>", checkpoint="ckpt/30000.json", step=30000)
folder.attempt(slug, attempt_id="job-1001")   # reconnect: reopens that same run, creates nothing
```

- **Fresh** starts a new attempt. Older attempts keep their data; the newest fresh one (even if it failed) is the
  one compared.
- **Resume** continues an explicit parent from a checkpoint at an absolute step. Its metrics carry the absolute
  step; the server composes the curve on that step column. The checkpoint step belongs to the parent (the state
  it saved); the resume owns every step after it.
- **Reconnect** reuses the attempt id, so a restarted launcher never duplicates a run or points. One writer per
  attempt.

On exit the attempt's status is set (`COMPLETED` or `FAILED`) and `experiment/receipt.json` is stored next to
`experiment/reproduce.sh` (and `code.diff` when the tree was dirty).

## Review

`folder.contract()` returns the server's reconciliation: per slug its state (`not_started`, `running`,
`completed`, `failed`, `cancelled`, `invalid`, or `unknown` when a read failed), active chain, segments,
attempts with roles (`active`, `superseded`, `branch`, `orphan`), parameter drift and diagnostics.
`folder.series(metric)` returns the composed curves, per-config mean/s.d./min/max with the seed count per step,
and the full-budget endpoint. `folder.export_receipts("results/runs.jsonl")` writes one line per attempt from
the contract for diffs and review.
