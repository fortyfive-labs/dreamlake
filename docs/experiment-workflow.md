# Experiment workflow

Organize a sweep in Dash as **question → configs × seeds → attempts → results**. Requires a server whose
`status` lists the `experiments` capability. The template ships with the SDK:

```sh
python -m dreamlake.experiments.workflow init my-sweep        # generator, plain train.py, launcher, README.md, .dashrc
python -m dreamlake.experiments.workflow status --namespace team --project rl --folder ppo/lr-sweep
python -m dreamlake.experiments.workflow export --namespace team --project rl --folder ppo/lr-sweep
```

(`src/dreamlake/experiments/workflow/template/`; there is no `dl` command for this workflow.)

## Tested environment

Checked end to end with **Python 3.13.14, `dreamlake` 0.50.1 and `params-proto` 3.3.0**. On Python 3.14.5 with
`params-proto` 3.3.0 the template's grid generator (`python ppo_lr_generate.py`) fails with
`TypeError: cannot pickle 'itertools.product' object` (raised in params-proto's `Sweep(...).list`). This failure
occurs in the template generator's params-proto dependency. Use Python 3.13 for this template:

```sh
uv venv --python 3.13 .venv
source .venv/bin/activate
uv pip install "dreamlake==0.50.1" "params-proto==3.3.0"
```

The project must already exist; the workflow commands do not create it (create it in the app or with
`dl create project <name> --namespace <ns>`). The step-by-step walkthrough with the
expected counts after publish, first launch and relaunch is in the
[Experiment workflow guide](https://docs.dreamlake.ai/ml-dash/guides/experiment-workflow).

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

folder.attempt(slug, kind="resume", parent="<attempt id>", checkpoint="/abs/ckpt/30000.json", step=30000)
folder.attach(slug, "job-1001").open()       # reconnect: the stored kind/parent/step, never a new record
```

- **Fresh** starts a new attempt. Older attempts keep their data; the newest fresh one (even if it failed) is the
  one compared.
- **Resume** continues an explicit parent from a checkpoint at an absolute step. Its metrics carry the absolute
  step; the server composes the curve on that step column. The checkpoint step belongs to the parent (the state
  it saved); the resume owns every step after it.
- **Reconnect** reuses the attempt id. Creation is the server's create-only `PUT`: the first request writes the
  record, every other one receives it unchanged; a different config, seed or params is refused
  (`ExperimentWorkflowError`), the creator's provenance is kept. `sync_metric` appends only missing rows after
  checking that the stored last row matches, and refuses NaN/inf or non-scalar values; `sync_file` skips a file
  already stored with the same SHA-256. One writer per attempt.
- Pass `command=` and `cwd=` exactly as executed (the template does): `experiment/reproduce.sh` replays them. It is
  marked **not exact** when there are untracked files (listed, not captured), tracked changes over 1 MiB, or no git.
- A plan row whose parameters the attempt did not run with makes the run **invalid** (never averaged).

Manifest `media: [{label, run, file}]` names a run file shown with the primary result (uploaded / planned / missing).
On exit the attempt's status is set (`COMPLETED` or `FAILED`) and `experiment/receipt.json` is stored next to
`experiment/reproduce.sh` (and `code.diff` when the tree was dirty).

## Review

`folder.contract()` returns the server's reconciliation: per slug its state (`not_started`, `running`,
`completed`, `failed`, `cancelled`, `invalid`, or `unknown` when a read failed), active chain, segments,
attempts with roles (`active`, `superseded`, `branch`, `orphan`), parameter drift and diagnostics.
`folder.series(metric)` returns the composed curves, per-config mean/s.d./min/max with the seed count per step,
and the full-budget endpoint. `folder.export_receipts("results/runs.jsonl")` writes one line per attempt from
the contract for diffs and review.

## Evaluation and analysis (unreleased)

- `attempt.evaluation(checkpoint=..., step=..., config='play', command=..., replaces=None)` returns an `Evaluation`:
  `.start()` records it running, `.finish(summary, evidence=[...])` uploads `summary.json` and evidence under
  `/eval/<id>/` and records the checks (`{"checks": [{name, id?, result, value?, threshold?, reason}]}`), `.fail(error)`
  records a technical error. A summary the server refuses is recorded as an error. The checkpoint's SHA-256 is recorded
  when the file is readable locally. Only the attempt's `evaluation` metadata changes; its training status never does.
- `folder.inputs()` returns the server's inputs fingerprint; `folder.publish_analysis(dir, report)` uploads the files of
  `dir` into `analysis/<id>/` and `analysis/report.json` last, and returns `stale=True` when the inputs moved meanwhile.
- The template's `play.py` (plain) and `analysis.py` (exit 0 met / 1 not met or inconclusive / 2 analysis error) show
  both. Server rules: `dreamlake-server/docs/experiment-workflow.md`, "Evaluation and analysis".
