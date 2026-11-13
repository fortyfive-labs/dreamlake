"""Launcher: reads the committed grid, runs plain training per slug, and records each attempt in Dash.

It never redefines combinations (that is the generator's job) and the training code never imports DreamLake:
train.py writes ordinary files, this launcher uploads them into the attempt's native run.

    # publish the manifest, README and plan, then run every slug as a fresh attempt
    python ppo_experiment_lr_launch.py --launch.publish
    # a subset; inject a crash to see a failed attempt
    python ppo_experiment_lr_launch.py --launch.slugs lr0.001-...-s2 --launch.fail-at 40000
    # continue a failed attempt from its 30k checkpoint (explicit parent, checkpoint and absolute step)
    python ppo_experiment_lr_launch.py --launch.slugs <slug> --launch.kind resume --launch.parent <attempt> --launch.step 30000
    # reconnect: re-attach to the SAME attempt (its stored kind/parent/step) and finish uploading; status unchanged
    python ppo_experiment_lr_launch.py --launch.slugs <slug> --launch.attempt <attempt> --launch.sync-only true
    # ... and set the outcome explicitly once you know it
    python ppo_experiment_lr_launch.py --launch.slugs <slug> --launch.attempt <attempt> --launch.sync-only true --launch.finish COMPLETED
    # export results/runs.jsonl (derived from the server's contract) for review
    python ppo_experiment_lr_launch.py --launch.export

Auth: the saved `dl login`, or DREAMLAKE_REMOTE / DREAMLAKE_API_KEY.
"""
import json
import subprocess
import sys
from pathlib import Path

from params_proto import proto

from dreamlake import DreamLakeClient
from dreamlake.experiments.workflow import ExperimentFolder, new_attempt_id, read_grid

HERE = Path(__file__).resolve().parent
MANIFEST = {
    'question': 'Does lr 3e-4 reach a higher eval return at the full 100k-step budget than 1e-4 and 1e-3?',
    'step': 'step', 'budget': 100_000,
    'metrics': [{'metric': 'eval', 'key': 'return', 'label': 'Eval return', 'units': 'episode return', 'split': 'eval', 'goal': 'max'},
                {'metric': 'train', 'key': 'loss', 'label': 'Train loss', 'split': 'train', 'goal': 'min'}],
    'changed': ['train.lr'], 'fixed': {'train.steps': 100_000, 'train.eval_every': 10_000},
    'code': 'train.py (this directory)',
    'launch': 'python ppo_experiment_lr_launch.py --launch.slugs <slug>',
}


@proto.prefix
class Launch:
    namespace: str = 'expwf'      # example values: pass --launch.namespace and --launch.project
    project: str = 'rl-lab'
    folder: str = 'ppo/lr-sweep'   # the Dash experiment folder (display path; its id is the identity)
    grid: str = 'grids/lr.jsonl'
    slugs: str = ''                # comma list; empty: every row of the grid
    kind: str = 'fresh'            # fresh | resume
    parent: str = ''               # resume: the attempt id continued
    step: int = -1                 # resume: the absolute step of the parent's checkpoint
    attempt: str = ''              # pre-assign this attempt id; with --launch.sync-only, the existing attempt to reconnect to
    sync_only: bool = False        # reconnect: upload the attempt's files again (idempotent), no training, status unchanged
    finish: str = ''               # with --launch.sync-only: COMPLETED or FAILED to set explicitly (default: keep the status)
    fail_at: int = -1              # fault injection for the example
    publish: bool = False
    export: bool = False


def rows_of(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def upload_outputs(attempt, slug, out):
    for name in ('train', 'eval'):
        rows_file = out / f'{name}.jsonl'
        if rows_file.exists():
            print(f'{slug} {attempt.attempt_id} {name}: +{attempt.sync_metric(name, rows_of(rows_file))} rows')
    for media in sorted((out / 'media').glob('*.png')):
        attempt.sync_file(media, prefix='/media')


@proto.cli
def main():
    project = DreamLakeClient().experiments.project(Launch.namespace, Launch.project)
    folder = ExperimentFolder(project, Launch.folder)
    if Launch.publish:
        grid = read_grid(HERE / Launch.grid)
        baseline = next(row for row in grid if row['params']['train.lr'] == 3e-4)
        folder.publish({**MANIFEST, 'baseline': baseline['config'],
                        'media': [{'label': 'Eval rollout at the full budget (baseline, seed 0)', 'run': baseline['slug'],
                                   'file': '/media/rollout.png'}]},
                       grid=HERE / Launch.grid, readme=HERE / 'README.md')
        print(f'published {Launch.folder} (experiment {folder.id})')
    if Launch.export:
        print(folder.export_receipts(HERE / 'results' / 'runs.jsonl'))
        return
    if not Launch.slugs and (Launch.publish or Launch.export):
        return
    rows = read_grid(HERE / Launch.grid, only=Launch.slugs.split(',') if Launch.slugs else None)
    if Launch.sync_only:
        # Reconnect: the stored record decides kind, parent and resume step; nothing is trained, nothing is re-created.
        if not Launch.attempt:
            raise SystemExit('--launch.sync-only needs --launch.attempt <id> (the attempt to reconnect to)')
        for row in rows:
            attempt = folder.attach(row['slug'], Launch.attempt).open()
            upload_outputs(attempt, row['slug'], HERE / 'runs' / row['slug'] / attempt.attempt_id)
            if Launch.finish:
                attempt.finish(Launch.finish)
            print(f'{row["slug"]} attempt {attempt.attempt_id} ({attempt.record["kind"]}, reconnected): files synced, '
                  f'status {"set to " + Launch.finish if Launch.finish else "unchanged"}')
        return
    for row in rows:
        resume = Launch.kind == 'resume'
        attempt_id = Launch.attempt or new_attempt_id()
        out = HERE / 'runs' / row['slug'] / attempt_id
        checkpoint = HERE / 'runs' / row['slug'] / Launch.parent / 'ckpt' / f'{Launch.step}.json' if resume else None
        if resume and not checkpoint.exists():
            raise SystemExit(f'checkpoint {checkpoint} does not exist')
        # Exactly what runs, recorded as run: the receipt and reproduce.sh carry this argv and cwd.
        cmd = [sys.executable, 'train.py', *[arg for key, value in row['params'].items() for arg in (f'--{key.replace("_", "-")}', str(value))],
               '--train.out', str(out), '--train.fail-at', str(Launch.fail_at), *(['--train.resume', str(checkpoint)] if resume else [])]
        attempt = folder.attempt(row['slug'], kind=Launch.kind, attempt_id=attempt_id, parent=Launch.parent or None,
                                 checkpoint=str(checkpoint) if resume else None, step=Launch.step if resume else None,
                                 params=row['params'], config=row['config'], seed=row['seed'], dashrc=HERE / '.dashrc',
                                 command=cmd, cwd=str(HERE)).open()
        if attempt.reconnected:
            raise SystemExit(f'attempt {attempt_id} already exists; use --launch.sync-only to finish uploading it, '
                             'or start a new attempt')
        code = subprocess.run(cmd, cwd=HERE).returncode
        upload_outputs(attempt, row['slug'], out)
        attempt.finish('COMPLETED' if code == 0 else 'FAILED', error=None if code == 0 else f'train.py exited {code}')
        print(f'{row["slug"]} attempt {attempt.attempt_id} ({Launch.kind}): {"completed" if code == 0 else "failed"}')


if __name__ == '__main__':
    main()
