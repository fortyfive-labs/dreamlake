"""Experiment workflow on DreamLake-native Dash: plan (grid), attempts, receipts.

An experiment is a project run folder whose metadata carries ``experiment`` (the manifest). Its identity is
the folder's immutable id; the path is only where it is shown, so a rename or move keeps every link. The plan
is the committed grid uploaded into that folder, each attempt is an ordinary native run in the folder, and
the server reconciles them into one contract (``GET experiments/:folderId``). This module writes those
records and reads the contract; it never decides which attempt is active. See
``docs/experiment-workflow.md``.

    folder = ExperimentFolder(DreamLakeClient().experiments.project("team", "rl"), "ppo/lr-sweep")
    folder.publish(manifest, grid="grids/lr.jsonl", readme="README.md")
    with folder.attempt("lr3e-4-s0", params=row["params"]) as attempt:      # fresh
        attempt.sync_metric("eval", rows)                                      # idempotent: only new rows
    folder.attempt("lr3e-4-s0", kind="resume", parent="<attempt>", checkpoint="ckpt/3000.pt", step=3000)
    folder.attempt("lr3e-4-s0", attempt_id="<attempt>")                         # reconnect: the same run
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shlex
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional
from urllib.parse import quote

MANIFEST_SCHEMA = 'dreamlake.experiment/v1'
ATTEMPT_SCHEMA = 1
GRID_FILE = 'grid.jsonl'
_SLUG = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
_ATTEMPT = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
_JOB_ENV = ('DREAMLAKE_JOB_ID', 'SLURM_JOB_ID', 'PBS_JOBID', 'LSB_JOBID', 'JOB_ID')
_MAX_DIFF_BYTES = 1 << 20


class ExperimentWorkflowError(RuntimeError):
    """A workflow record that would be ambiguous or would overwrite another attempt."""


# ── Plan ─────────────────────────────────────────────────────────────────────────────────────────────────────

def _short(value: Any) -> str:
    text = repr(value) if isinstance(value, float) else str(value)
    return re.sub(r'[^A-Za-z0-9.]+', '', text.replace('e-0', 'e-').replace('e+0', 'e'))[:24] or 'x'


def config_slug(params: Mapping[str, Any], *, changed: Iterable[str], seed_key: str) -> str:
    """A stable slug for one configuration: the changed values, readable, plus a short hash of the whole config.

    Derived only from the parameters (never a date or a scheduler id), so regenerating the same grid gives the
    same slugs and two different configurations never share one.
    """
    config = {k: v for k, v in params.items() if k != seed_key}
    digest = hashlib.sha256(json.dumps(config, sort_keys=True, default=str).encode()).hexdigest()[:6]
    readable = '_'.join(f'{key.rsplit(".", 1)[-1]}{_short(params[key])}' for key in changed if key in params)
    return f'{readable}-{digest}' if readable else digest


def grid_rows(configs: Iterable[Mapping[str, Any]], *, changed: Iterable[str], seed_key: str) -> list[dict]:
    """Grid rows ``{slug, config, seed, params}`` from flat (dotted) parameter dicts, e.g. a params-proto
    ``Sweep(...).list``. ``slug`` = ``<config slug>-s<seed>``. Duplicate configurations are refused."""
    changed = list(changed)
    rows, seen = [], set()
    for params in configs:
        params = dict(params)
        if seed_key not in params:
            raise ValueError(f'every grid row needs the seed key {seed_key!r}')
        config = config_slug(params, changed=changed, seed_key=seed_key)
        slug = f'{config}-s{_short(params[seed_key])}'
        if slug in seen:
            raise ValueError(f'two grid rows produce the slug {slug!r}; make the configurations differ')
        seen.add(slug)
        rows.append({'slug': slug, 'config': config, 'seed': params[seed_key], 'params': params})
    return rows


def write_grid(path: str | os.PathLike, rows: Iterable[Mapping[str, Any]]) -> Path:
    """Write the grid as JSONL, one row per line, keys sorted: the committed plan."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(row, sort_keys=True) + '\n' for row in rows))
    return path


def read_grid(path: str | os.PathLike, *, only: Optional[Iterable[str]] = None) -> list[dict]:
    """The grid rows, optionally only the named slugs (unknown names are refused, never skipped)."""
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    if only is None:
        return rows
    wanted = list(only)
    by_slug = {row['slug']: row for row in rows}
    missing = [slug for slug in wanted if slug not in by_slug]
    if missing:
        raise ValueError(f'not in the grid: {", ".join(missing)}')
    return [by_slug[slug] for slug in wanted]


# ── Provenance ───────────────────────────────────────────────────────────────────────────────────────────────

def _git(*args: str, cwd: Optional[str] = None) -> Optional[str]:
    try:
        return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, timeout=10, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def collect_provenance(*, command: Optional[list[str]] = None, cwd: Optional[str] = None) -> dict:
    """What ran: commit, whether the tree was dirty (with the diff's hash), command, host, job id, Python."""
    commit = (_git('rev-parse', 'HEAD', cwd=cwd) or '').strip() or None
    status = _git('status', '--porcelain', cwd=cwd)
    diff = _git('diff', 'HEAD', cwd=cwd) if status else ''
    job = next(((name, os.environ[name]) for name in _JOB_ENV if os.environ.get(name)), None)
    return {'commit': commit, 'dirty': bool(status), 'diff_sha256': hashlib.sha256(diff.encode()).hexdigest() if diff else None,
            'command': shlex.join(command if command is not None else [sys.executable, *sys.argv])[:2000],
            'cwd': str(Path(cwd or os.getcwd()).resolve()), 'host': socket.gethostname(),
            'job': {'kind': job[0], 'id': job[1]} if job else None, 'python': sys.version.split()[0],
            '_diff': diff if diff and len(diff.encode()) <= _MAX_DIFF_BYTES else None}


def reproduce_script(provenance: Mapping[str, Any]) -> str:
    lines = ['#!/usr/bin/env bash', '# Re-run this attempt exactly as recorded. Generated by dreamlake.experiments.workflow.', 'set -euo pipefail',
             f'cd {shlex.quote(provenance["cwd"])}']
    if provenance.get('commit'):
        lines.append(f'git checkout {provenance["commit"]}')
    if provenance.get('dirty'):
        lines.append('git apply experiment/code.diff  # the tree was dirty; the diff is stored next to this file' if provenance.get('_diff')
                     else '# WARNING: the tree was dirty and the diff was too large to store; this is not an exact reproduction')
    lines.append(provenance['command'])
    return '\n'.join(lines) + '\n'


def new_attempt_id() -> str:
    """Sortable for people (UTC time first), unique by its random suffix. Order on the server is creation order."""
    return time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + secrets.token_hex(3)


# ── Folder and attempts ──────────────────────────────────────────────────────────────────────────────────────

class ExperimentFolder:
    """One experiment folder of a native project (``DreamLakeClient().experiments.project(ns, project)``)."""

    def __init__(self, project, path: str):
        self.project = project
        self.path = path.strip('/')
        self._folder: Optional[dict] = None

    @property
    def id(self) -> str:
        return self.folder()['id']

    def folder(self) -> dict:
        """The folder (created when missing). Its id is the experiment's identity; later renames keep it."""
        if self._folder is None:
            self._folder = self.project.folders.create(self.path)
        return self._folder

    def publish(self, manifest: Mapping[str, Any], *, grid: str | os.PathLike, readme: str | os.PathLike | None = None) -> dict:
        """Make the folder an experiment: set its manifest and upload the plan (and the README).

        Re-publishing replaces those files' content in place (same file ids) and the manifest. The README is the
        experiment's single summary and conclusion text; link a Memo through ``manifest['memo']`` for analysis.
        """
        manifest = {'schema': MANIFEST_SCHEMA, 'grid': GRID_FILE, **manifest}
        folder = self.folder()
        current = self.project.folders.get(folder['id'])
        metadata = {**(current.get('metadata') or {}), 'experiment': manifest}
        self._folder = self.project.folders.update(folder['id'], metadata=metadata)
        existing = {file['filename']: file for file in self.project.project_files.list(folder_id=folder['id'])}
        for name, source in ((manifest['grid'], grid), ('README.md', readme)):
            if source is None:
                continue
            if name in existing:
                self.project.project_files.replace(existing[name]['id'], source)
            else:
                self.project.project_files.upload(source, folder_id=folder['id'], name=name)
        return self._folder

    def contract(self) -> dict:
        """The server's reconciled contract (plan, logical runs, chains, segments, counts, diagnostics)."""
        return self.project._request('GET', f'/experiments/{self.id}')['contract']

    def series(self, metric: int = 0, *, configs: Optional[list[str]] = None, bins: Optional[int] = None) -> dict:
        """Effective curves and per-config statistics, composed by the server from the contract's selection."""
        body: dict[str, Any] = {'metric': metric}
        if configs is not None:
            body['configs'] = configs
        if bins is not None:
            body['bins'] = bins
        return self.project._request('POST', f'/experiments/{self.id}/series', body=body)

    def attempt(self, run: str, *, kind: str = 'fresh', attempt_id: Optional[str] = None, parent: Optional[str] = None,
                checkpoint: Optional[str] = None, step: Optional[int | float] = None, params: Optional[Mapping[str, Any]] = None,
                config: Optional[str] = None, seed: Any = None, provenance: Optional[Mapping[str, Any]] = None,
                command: Optional[list[str]] = None, dashrc: str | os.PathLike | None = None) -> 'Attempt':
        """Open one attempt of logical run ``run`` (the plan slug) as a native run in this folder.

        * ``kind='fresh'`` starts a new attempt; earlier ones keep their data and become superseded.
        * ``kind='resume'`` continues ``parent`` (an attempt id) from ``checkpoint`` at absolute ``step``.
        * ``dashrc`` is uploaded as the run's own ``.dashrc`` (Dash's per-run chart configuration).
        * ``attempt_id=`` reconnects: if that attempt exists, the same run is reopened (nothing is created or
          overwritten); a conflicting record is refused. Pre-assigning ids (e.g. from a job id) makes retries idempotent.
        """
        if not _SLUG.match(run):
            raise ValueError(f'run {run!r} is not a plan slug')
        if kind not in ('fresh', 'resume'):
            raise ValueError("kind must be 'fresh' or 'resume'")
        if kind == 'resume':
            if not parent or not _ATTEMPT.match(parent) or not checkpoint or not isinstance(step, (int, float)) or isinstance(step, bool):
                raise ValueError('a resume needs parent (attempt id), checkpoint and the absolute step it continues from')
        elif parent is not None or checkpoint is not None or step is not None:
            raise ValueError('a fresh attempt has no parent, checkpoint or resume step')
        attempt_id = attempt_id or new_attempt_id()
        if not _ATTEMPT.match(attempt_id):
            raise ValueError(f'attempt id {attempt_id!r} is invalid')
        return Attempt(self, run=run, attempt=attempt_id, kind=kind, parent=parent,
                       resume={'step': step, 'checkpoint': checkpoint} if kind == 'resume' else None,
                       params=dict(params or {}), config=config, seed=seed,
                       provenance=dict(provenance) if provenance is not None else collect_provenance(command=command),
                       dashrc=dashrc)

    def export_receipts(self, path: str | os.PathLike) -> Path:
        """``results/runs.jsonl``: one line per attempt, derived from the server contract. An export for review and
        diffs; the run records stay the source of truth, so concurrent writers never race on this file."""
        contract = self.contract()
        lines = []
        for run in contract['runs']:
            for attempt in run['attempts']:
                lines.append({'run': run['run'], 'planned': run['planned'], 'state': run['state'], 'config': run['config'],
                              'seed': run['seed'], **{k: attempt.get(k) for k in ('attempt', 'runId', 'runName', 'kind', 'parent', 'resume',
                              'role', 'segment', 'status', 'createdAt', 'endedAt', 'params', 'provenance')}})
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(line, sort_keys=True) + '\n' for line in lines))
        return path


class Attempt:
    """One attempt: an existing-native-run record plus idempotent writes. Use as a context manager or call
    ``open()`` / ``finish(status)`` yourself. Metric uploads append only rows the server does not have yet."""

    def __init__(self, folder: ExperimentFolder, **record):
        self.folder, self.project = folder, folder.project
        self.record = record
        self.run = record['run']
        self.attempt_id = record['attempt']
        self.run_name = f'{self.run}.{self.attempt_id}'
        self.run_id: Optional[str] = None
        self.reconnected = False

    def _metadata(self) -> dict:
        r = self.record
        provenance = {k: v for k, v in r['provenance'].items() if not k.startswith('_')}
        meta = {'v': ATTEMPT_SCHEMA, 'experiment': self.folder.id, 'run': r['run'], 'attempt': r['attempt'], 'kind': r['kind'],
                'params': _flat(r['params']), 'provenance': provenance}
        if r['config'] is not None:
            meta['config'] = r['config']
        if r['seed'] is not None:
            meta['seed'] = r['seed']
        if r['kind'] == 'resume':
            meta['parent'], meta['resume'] = r['parent'], r['resume']
        return meta

    def open(self) -> 'Attempt':
        path = f'{self.folder.path}/{self.run_name}'
        try:
            found = self.project.by_path(path)
        except Exception as error:  # 404: not created yet
            if getattr(error, 'status', None) != 404:
                raise
            found = None
        if found and found.get('kind') == 'run':
            stored = (found['experiment'].get('metadata') or {}).get('experiment') or {}
            mine = self._metadata()
            same = all(stored.get(k) == mine.get(k) for k in ('experiment', 'run', 'attempt', 'kind', 'parent', 'resume'))
            if not same:
                raise ExperimentWorkflowError(f'{path} already holds a different attempt record; refusing to overwrite it')
            self.run_id, self.reconnected = found['experiment']['id'], True
            return self
        if found:
            raise ExperimentWorkflowError(f'{path} is a {found.get("kind")}, not a run')
        if self.record['kind'] == 'resume':
            # The parent must be an attempt of this logical run; the server reports orphans, but refusing early is clearer.
            try:
                runs = self.folder.contract()['runs']
            except Exception as error:  # a job or writer session may not read the contract; the server still reports orphans
                if getattr(error, 'status', None) != 403:
                    raise
                runs = None
            parents = [a for r in runs or [] if r['run'] == self.run for a in r['attempts']]
            if runs is not None and not any(a['attempt'] == self.record['parent'] for a in parents):
                raise ExperimentWorkflowError(f'parent attempt {self.record["parent"]} is not an attempt of {self.run}')
        run = self.project._request('PUT', '/run-paths/' + quote(path, safe='/'), body={'metadata': {'experiment': self._metadata()}})
        self.run_id = run['experiment']['id']
        if self.record['params']:
            self.project.parameters.set(self.run_id, self.record['params'])
        self._write_provenance_files()
        return self

    def _write_provenance_files(self):
        provenance = self.record['provenance']
        with tempfile.TemporaryDirectory() as tmp:
            files = {'reproduce.sh': reproduce_script(provenance)}
            if provenance.get('_diff'):
                files['code.diff'] = provenance['_diff']
            for name, text in files.items():
                (Path(tmp) / name).write_text(text)
                self.project.files.upload(self.run_id, Path(tmp) / name, prefix='/experiment')
        if self.record.get('dashrc'):
            self.project.files.upload(self.run_id, self.record['dashrc'], prefix='/', name='.dashrc')

    def sync_metric(self, name: str, rows: list[Mapping[str, Any]], *, batch: int = 500) -> int:
        """Append ``rows[n:]`` where ``n`` is what the run already holds for ``name``: a reconnect that re-reads the
        same append-only file never duplicates points. One writer per attempt. Returns the rows appended."""
        held = next((m for m in self.project.metrics.list(self.run_id) if m.get('name') == name), None)
        have = 0 if held is None else held.get('count')
        if not isinstance(have, int) or isinstance(have, bool) or have < 0:
            # Never assume zero for a metric that exists: that is exactly how a reconnect would duplicate points.
            raise ExperimentWorkflowError(f'{self.run_name} metric {name} has no readable count; not appending')
        if have > len(rows):
            raise ExperimentWorkflowError(f'{self.run_name} already holds {have} {name} rows, more than the {len(rows)} given')
        for start in range(have, len(rows), batch):
            self.project.metrics.append(self.run_id, name, [dict(row) for row in rows[start:start + batch]])
        return len(rows) - have

    def set_status(self, status: str):
        self.project.runs.set_status(self.run_id, status)

    def finish(self, status: str, *, error: Optional[str] = None, extra: Optional[Mapping[str, Any]] = None):
        """Set the run's status and store ``experiment/receipt.json`` (an export of the record and outcome)."""
        self.set_status(status)
        receipt = {'attempt': self._metadata(), 'run_id': self.run_id, 'run_name': self.run_name, 'status': status,
                   'error': error, 'reconnected': self.reconnected, 'finished_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                   **(dict(extra) if extra else {})}
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'receipt.json'
            target.write_text(json.dumps(receipt, indent=2, sort_keys=True, default=str))
            self.project.files.upload(self.run_id, target, prefix='/experiment')

    def __enter__(self) -> 'Attempt':
        return self.open()

    def __exit__(self, exc_type, exc, tb):
        if self.run_id is None:
            return False
        self.finish('COMPLETED' if exc_type is None else 'FAILED', error=None if exc is None else f'{exc_type.__name__}: {exc}'[:2000])
        return False


def _flat(params: Mapping[str, Any], prefix: str = '') -> dict:
    out: dict[str, Any] = {}
    for key, value in params.items():
        name = f'{prefix}{key}'
        if isinstance(value, Mapping):
            out.update(_flat(value, name + '.'))
        else:
            out[name] = value
    return out


__all__ = ['MANIFEST_SCHEMA', 'ExperimentFolder', 'Attempt', 'ExperimentWorkflowError', 'grid_rows', 'write_grid', 'read_grid',
           'config_slug', 'collect_provenance', 'reproduce_script', 'new_attempt_id']
