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
    folder.attach("lr3e-4-s0", "<attempt>")                                     # reconnect: the same run, stored link
    attempt.evaluation(checkpoint="ckpt/100000.json", step=100_000).start().finish("eval/summary.json")
    folder.publish_analysis("analysis/out/<id>", report)                       # report.json last, inputs from folder.inputs()

Command line (template, status and export): ``python -m dreamlake.experiments.workflow --help``.
"""
from __future__ import annotations

import hashlib
import math
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
EVALUATION_SCHEMA = 1
ANALYSIS_SCHEMA = 'dreamlake.analysis/v1'
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
    """What ran: the exact argv and working directory, commit, tracked changes (diff stored up to 1 MiB), untracked
    files (names only: they are NOT captured), host, job id, Python. ``exact`` says whether the commit plus the stored
    diff reproduce the source tree."""
    commit = (_git('rev-parse', 'HEAD', cwd=cwd) or '').strip() or None
    status = _git('status', '--porcelain', '--untracked-files=all', cwd=cwd) or ''
    untracked = [line[3:] for line in status.splitlines() if line.startswith('?? ')]
    tracked_dirty = any(not line.startswith('?? ') for line in status.splitlines())
    diff = _git('diff', 'HEAD', cwd=cwd) if tracked_dirty else ''
    stored = diff if diff and len(diff.encode()) <= _MAX_DIFF_BYTES else None
    job = next(((name, os.environ[name]) for name in _JOB_ENV if os.environ.get(name)), None)
    return {'commit': commit, 'dirty': bool(status), 'diff_sha256': hashlib.sha256(diff.encode()).hexdigest() if diff else None,
            'untracked': untracked[:200], 'untracked_count': len(untracked),
            'exact': commit is not None and not untracked and (not tracked_dirty or stored is not None),
            'command': shlex.join(command if command is not None else [sys.executable, *sys.argv])[:4000],
            'cwd': str(Path(cwd or os.getcwd()).resolve()), 'host': socket.gethostname(),
            'job': {'kind': job[0], 'id': job[1]} if job else None, 'python': sys.version.split()[0],
            '_diff': stored}


def reproduce_script(provenance: Mapping[str, Any]) -> str:
    exact = provenance.get('exact')
    lines = ['#!/usr/bin/env bash',
             '# Re-run this attempt as recorded (argv and working directory as executed). Generated by dreamlake.experiments.workflow.',
             '# ' + ('Exact: commit plus the stored diff reproduce the source tree.' if exact else
                     'NOT an exact reproduction: see the warnings below.'),
             'set -euo pipefail', f'cd {shlex.quote(provenance["cwd"])}']
    if provenance.get('commit'):
        lines.append(f'git checkout {provenance["commit"]}')
    else:
        lines.append('# WARNING: not a git checkout; the source is not recorded')
    if provenance.get('_diff'):
        lines.append('git apply experiment/code.diff  # tracked changes, stored next to this file')
    elif provenance.get('diff_sha256'):
        lines.append('# WARNING: tracked changes were larger than 1 MiB and were not stored')
    if provenance.get('untracked_count'):
        lines.append(f'# WARNING: {provenance["untracked_count"]} untracked file(s) were present and are NOT captured: '
                     + ', '.join(provenance.get('untracked', [])[:20]))
    lines.append(provenance['command'])
    return '\n'.join(lines) + '\n'


def new_attempt_id() -> str:
    """Sortable for people (UTC time first), unique by its random suffix. Order on the server is creation order."""
    return time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + secrets.token_hex(3)


def _sha256_file(path: str | os.PathLike) -> str:
    """SHA-256 of a file, streamed (checkpoints and videos are never read into memory whole)."""
    with open(path, 'rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


# ── Folder and attempts ──────────────────────────────────────────────────────────────────────────────────────

class ExperimentFolder:
    """One experiment folder of a native project (``DreamLakeClient().experiments.project(ns, project)``)."""

    def __init__(self, project, path: str, *, folder_id: Optional[str] = None):
        """``folder_id``: the experiment folder's id when it is already known (a worker's bound credentials cannot
        create folders); the folder is then never created or looked up."""
        self.project = project
        self.path = path.strip('/')
        self._folder: Optional[dict] = {'id': folder_id, 'path': self.path} if folder_id else None

    @property
    def id(self) -> str:
        return self.folder()['id']

    def folder(self) -> dict:
        """The folder (created when missing). Its id is the experiment's identity; later renames keep it."""
        if self._folder is None:
            self._folder = self.project.folders.create(self.path)
        return self._folder

    def current_path(self) -> str:
        """The folder's path NOW, read by its immutable id: a rename or move since this object was made is followed, never
        recreated at the old path. (Creates the folder only when this object has never resolved it.)"""
        if self._folder is None:
            return self.folder()['path'].strip('/')
        self.path = self.project.folders.get(self.id)['path'].strip('/')
        return self.path

    def _child(self, parent_id: str, name: str) -> dict:
        """The child folder ``name`` of folder ``parent_id`` (created if missing), addressed by the parent's id."""
        child = self.project._request('POST', '/folders', body={'parentId': parent_id, 'name': name})['folder']
        if child.get('parentId') not in (None, parent_id):
            raise ExperimentWorkflowError(f'folder {name} was created under {child.get("parentId")}, not {parent_id}; '
                                          'the experiment folder moved meanwhile: publish again')
        return child

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

    def inputs(self) -> dict:
        """The server's fingerprint of what an analysis can read now (chains, states, plan, evaluations, metric
        revisions). Copy it verbatim into a report; the server compares it later and marks a changed report stale."""
        return self.project._request('GET', f'/experiments/{self.id}', params={'inputs': '1'})['contract']['inputs']

    def publish_analysis(self, generation: str | os.PathLike, report: Mapping[str, Any]) -> dict:
        """Publish one analysis generation: the files of ``generation`` into the folder's ``analysis/<id>/``, its
        ``report.json`` last there, then ``analysis/report.json`` (the pointer) LAST, so a reader never sees a report
        naming files that are not uploaded. A generation is immutable: publishing the same id again with the same files
        and report reconciles (nothing is re-uploaded); different content under an existing id is refused (use a new
        id), and the pointer is left as it was. Every report and figure the report names must be in ``generation``.

        Returns ``{'report', 'published': True, 'state', 'verified'}``: ``state`` is the server's analysis state after
        publishing (``current``, ``stale``, ...) when ``verified``; when the read after publishing fails, the generation
        IS published and ``verified`` is False with ``error`` (never retried as another record)."""
        report = {'schema': ANALYSIS_SCHEMA, **report}
        gid = str(report.get('id', ''))
        if not _ATTEMPT.match(gid):
            raise ValueError('report["id"] must be a generation id (new_attempt_id())')
        generation = Path(generation)
        local = {path.name: path for path in sorted(generation.iterdir()) if path.is_file() and path.name != 'report.json'}
        declared = [name for name in [report.get('report'), *(f.get('file') for f in report.get('figures') or [])] if name]
        missing = [name for name in declared if name not in local]
        if missing:
            raise ExperimentWorkflowError(f'the report names files that are not in {generation}: {", ".join(missing)}')
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / 'report.json'
            body.write_text(json.dumps(report, indent=2, sort_keys=True))
            files = {**local, 'report.json': body}
            digests = {name: _sha256_file(path) for name, path in files.items()}
            # By the experiment folder's id, never its cached path: a renamed or moved experiment keeps its analysis.
            analysis_folder = self._child(self.id, 'analysis')
            own = self._child(analysis_folder['id'], gid)
            held = {f['filename']: f for f in self.project.project_files.list(folder_id=own['id'])}  # a failed listing raises
            differs = sorted(name for name, f in held.items() if digests.get(name) != f.get('checksum'))
            if differs:
                raise ExperimentWorkflowError(f'analysis generation {gid} already holds other content ({", ".join(differs)}); '
                                              'publish it under a new id (the published report is unchanged)')
            for name in sorted(files, key=lambda n: n == 'report.json'):
                if name not in held:
                    self.project.project_files.upload(files[name], folder_id=own['id'], name=name,
                                                      **({'content_type': 'application/json'} if name == 'report.json' else {}))
            pointer = next((f for f in self.project.project_files.list(folder_id=analysis_folder['id']) if f['filename'] == 'report.json'), None)
            if pointer is None:
                self.project.project_files.upload(body, folder_id=analysis_folder['id'], name='report.json', content_type='application/json')
            elif pointer.get('checksum') != digests['report.json']:
                self.project.project_files.replace(pointer['id'], body, content_type='application/json')
        try:
            analysis = self.contract().get('analysis') or {}
        except Exception as error:  # published, but its state could not be read back: say so, never re-publish
            return {'report': report, 'published': True, 'verified': False, 'state': None, 'error': f'{type(error).__name__}: {error}'}
        mine = (analysis.get('report') or {}).get('id') == gid
        return {'report': report, 'published': True, 'verified': True,
                'state': analysis.get('state') if mine else 'replaced', 'reason': analysis.get('reason')}

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
                command: Optional[list[str]] = None, cwd: Optional[str] = None,
                dashrc: str | os.PathLike | None = None) -> 'Attempt':
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
                       provenance=dict(provenance) if provenance is not None else collect_provenance(command=command, cwd=cwd),
                       dashrc=dashrc)

    def attach(self, run: str, attempt_id: str, *, dashrc: str | os.PathLike | None = None) -> 'Attempt':
        """Reconnect to an EXISTING attempt by id, taking its kind, parent, resume, config, seed and params from the
        stored record (so a resume stays a resume). Raises when no such attempt exists; never creates one."""
        path = f'{self.current_path()}/{run}.{attempt_id}'
        try:
            found = self.project.by_path(path)
        except Exception as error:
            if getattr(error, 'status', None) == 404:
                raise ExperimentWorkflowError(f'no attempt {attempt_id} of {run} exists to reconnect to') from None
            raise
        stored = (found.get('experiment', {}).get('metadata') or {}).get('experiment') if found.get('kind') == 'run' else None
        if not stored:
            raise ExperimentWorkflowError(f'{path} is not an attempt of this experiment')
        return Attempt(self, run=run, attempt=attempt_id, kind=stored.get('kind'), parent=stored.get('parent'),
                       resume=stored.get('resume'), params=dict(stored.get('params') or {}), config=stored.get('config'),
                       seed=stored.get('seed'), provenance=dict(stored.get('provenance') or {}), dashrc=dashrc)

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
        self._nonce = secrets.token_hex(8)

    @classmethod
    def existing(cls, folder: 'ExperimentFolder', *, run: str, attempt_id: str, run_id: str) -> 'Attempt':
        """An attempt already recorded (its run id known), for writes that need no reopen: e.g. a worker holding a write
        session bound to that run records its evaluation (``.evaluation(...)``). Nothing is created or read."""
        attempt = cls(folder, run=run, attempt=attempt_id, kind=None, parent=None, resume=None, params={}, config=None,
                      seed=None, provenance={}, dashrc=None)
        attempt.run_id, attempt.reconnected = run_id, True
        return attempt

    # The attempt's identity: a reconnect must match every one of these. Provenance (host, process) may differ.
    IDENTITY = ('v', 'experiment', 'run', 'attempt', 'kind', 'parent', 'resume', 'config', 'seed', 'params')

    def _metadata(self) -> dict:
        r = self.record
        provenance = {k: v for k, v in r['provenance'].items() if not k.startswith('_')}
        # Who created the run: the one request whose nonce is stored (create-only PUT) writes the provenance files.
        provenance.setdefault('opened_by', self._nonce)
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
        """Create the attempt's run, or attach to it when it exists with the SAME identity. Uses the server's
        create-only PUT: only the request that creates the run writes its record, so concurrent callers never
        overwrite each other; a conflicting identity is refused, never merged."""
        # The folder's current path by its id: a run is never created under a path the folder has left.
        path = f'{self.folder.current_path()}/{self.run_name}'
        mine = self._metadata()
        if self.record['kind'] == 'resume':
            # The parent must be an attempt of this logical run; the server reports orphans, but refusing early is clearer.
            try:
                runs = self.folder.contract()['runs']
            except Exception as error:  # a job or writer session may not read the contract; the server still reports orphans
                if getattr(error, 'status', None) not in (403, 409):
                    raise
                runs = None
            parents = [a for r in runs or [] if r['run'] == self.run for a in r['attempts']]
            if runs is not None and not any(a['attempt'] == self.record['parent'] for a in parents):
                raise ExperimentWorkflowError(f'parent attempt {self.record["parent"]} is not an attempt of {self.run}')
        run = self.project._request('PUT', '/run-paths/' + quote(path, safe='/'),
                                    body={'metadata': {'experiment': mine}, 'createOnly': True})['experiment']
        stored = (run.get('metadata') or {}).get('experiment')
        if not isinstance(stored, dict):
            raise ExperimentWorkflowError(f'{path} already exists and is not an attempt of this experiment')
        conflicts = [key for key in self.IDENTITY if stored.get(key) != mine.get(key)]
        if conflicts:
            raise ExperimentWorkflowError(f'{path} already holds attempt {self.attempt_id} with a different '
                                          f'{", ".join(conflicts)}; refusing to reuse it (start a new attempt instead)')
        self.run_id = run['id']
        created = (stored.get('provenance') or {}).get('opened_by') == self._nonce
        self.reconnected = not created
        self.record['provenance'] = {**(stored.get('provenance') or {}), **{k: v for k, v in self.record['provenance'].items() if k.startswith('_')}}
        if created:
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
        same append-only file never duplicates points. One writer per attempt (concurrent writers are not supported).
        Rows must be objects of finite numbers, strings or booleans; the stored row ``n-1`` must equal ``rows[n-1]``
        (otherwise the file is not the one that was uploaded). Returns the rows appended."""
        for at, row in enumerate(rows):
            if not isinstance(row, Mapping) or not row or any(
                    not isinstance(key, str) or isinstance(value, float) and not math.isfinite(value) or
                    not isinstance(value, (int, float, str, bool)) for key, value in row.items()):
                raise ExperimentWorkflowError(f'{name} row {at} is not an object of finite numbers, strings or booleans: {row!r}'[:300])
        held = next((m for m in self.project.metrics.list(self.run_id) if m.get('name') == name), None)
        have = 0 if held is None else held.get('count')
        if not isinstance(have, int) or isinstance(have, bool) or have < 0:
            # Never assume zero for a metric that exists: that is exactly how a reconnect would duplicate points.
            raise ExperimentWorkflowError(f'{self.run_name} metric {name} has no readable count; not appending')
        if have > len(rows):
            raise ExperimentWorkflowError(f'{self.run_name} already holds {have} {name} rows, more than the {len(rows)} given')
        if have:
            page = self.project._api.read_metric(self.project.namespace, self.project.project, self.run_id, name,
                                                 start_index=have - 1, limit=1)
            last = (page.get('rows') or [None])[0]
            expected = rows[have - 1]
            if not isinstance(last, Mapping) or any(last.get(key) != value for key, value in expected.items()):
                raise ExperimentWorkflowError(f'{self.run_name} metric {name}: stored row {have - 1} differs from the given rows; '
                                              'this is not the same append-only file, nothing appended')
        for start in range(have, len(rows), batch):
            self.project.metrics.append(self.run_id, name, [dict(row) for row in rows[start:start + batch]])
        return len(rows) - have

    def sync_file(self, path: str | os.PathLike, *, prefix: str = '/') -> bool:
        """Upload a file unless the run already holds it under ``prefix`` (same name and SHA-256): a reconnect does not
        duplicate it. A same-named file with other content is refused. Returns whether it uploaded."""
        path = Path(path)
        digest = _sha256_file(path)
        listed = self.project.files.list(self.run_id, prefix=prefix)
        held = [f for f in (listed.get('files', []) if isinstance(listed, dict) else listed) if f.get('filename') == path.name]
        if held:
            if any(f.get('checksum') == digest for f in held):
                return False
            raise ExperimentWorkflowError(f'{self.run_name} already holds {prefix.rstrip("/")}/{path.name} with other content')
        self.project.files.upload(self.run_id, path, prefix=prefix)
        return True

    def set_status(self, status: str):
        self.project.runs.set_status(self.run_id, status)

    def evaluation(self, *, checkpoint: str | os.PathLike, step: int | float, config: str = 'play',
                   overrides: Optional[Mapping[str, Any]] = None, command: Optional[list[str]] = None,
                   cwd: Optional[str] = None, evaluation_id: Optional[str] = None, replaces: Optional[str] = None) -> 'Evaluation':
        """An evaluation of this attempt's checkpoint at absolute ``step``: see :class:`Evaluation`."""
        return Evaluation(self, checkpoint=checkpoint, step=step, config=config, overrides=overrides, command=command,
                          cwd=cwd, evaluation_id=evaluation_id, replaces=replaces)

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


class Evaluation:
    """One evaluation execution of an attempt's checkpoint, recorded on that attempt only
    (``PUT experiments/:folderId/attempts/:runId/evaluation``; no other metadata of the run is touched).

    ``start()`` records it running; ``finish(summary)`` uploads the evaluation's own files under ``/eval/<id>/`` and
    records the named checks from ``summary`` (``{"checks": [{name, id?, result: pass|fail|inconclusive, value?,
    threshold?, reason}]}``, written by your plain evaluation script); ``fail(error)`` records a technical error.
    A failed check is a result, not an error, and neither changes the training run's status. The checkpoint's
    SHA-256 is recorded when the file is readable here; a path alone is shown as unverified."""

    def __init__(self, attempt: Attempt, *, checkpoint, step, config='play', overrides=None, command=None, cwd=None,
                 evaluation_id=None, replaces=None):
        if attempt.run_id is None:
            raise ExperimentWorkflowError('open the attempt before evaluating it')
        if not isinstance(step, (int, float)) or isinstance(step, bool):
            raise ValueError('step must be the absolute step of the checkpoint')
        self.attempt, self.id, self.replaces = attempt, evaluation_id or new_attempt_id(), replaces
        path = Path(checkpoint)
        self.checkpoint: dict[str, Any] = {'step': step, 'path': str(checkpoint)}
        if path.is_file():
            self.checkpoint.update(sha256=_sha256_file(path), size=path.stat().st_size)
        self.base: dict[str, Any] = {'v': EVALUATION_SCHEMA, 'id': self.id, 'attempt': attempt.attempt_id, 'checkpoint': self.checkpoint,
                                     'config': {'name': config, **({'overrides': dict(overrides)} if overrides else {})}}
        if command is not None:
            self.base['command'] = [str(arg) for arg in command]
        if cwd is not None:
            self.base['cwd'] = str(cwd)
        self.started = None

    def _put(self, record: dict) -> dict:
        body: dict[str, Any] = {'evaluation': record}
        if self.replaces:
            body['replaces'] = self.replaces
        return self.attempt.project._request(
            'PUT', f'/experiments/{self.attempt.folder.id}/attempts/{self.attempt.run_id}/evaluation', body=body)

    @staticmethod
    def _now() -> str:
        return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

    def start(self) -> 'Evaluation':
        self.started = self._now()
        self._put({**self.base, 'status': 'running', 'startedAt': self.started})
        # A later write of the same id continues this one; it never needs `replaces` again.
        self.replaces = None
        return self

    def _upload(self, files: Iterable[str | os.PathLike]) -> list[dict]:
        out = []
        for path in files:
            path = Path(path)
            uploaded = self.attempt.project.files.upload(self.attempt.run_id, path, prefix=f'/eval/{self.id}')
            view = uploaded.get('file', uploaded) if isinstance(uploaded, dict) else {}
            entry = {'label': path.name, 'path': f'/eval/{self.id}/{path.name}', 'sha256': _sha256_file(path)}
            if isinstance(view.get('id'), str):
                entry['fileId'] = view['id']
            if isinstance(view.get('contentType'), str):
                entry['contentType'] = view['contentType']
            out.append(entry)
        return out

    def finish(self, summary: str | os.PathLike, *, evidence: Iterable[str | os.PathLike] = ()) -> dict:
        """Upload ``summary`` and ``evidence`` (videos, images) and record the checks. A summary the server refuses
        (unknown result word, missing reason) is recorded as an evaluation ERROR with the server's reason."""
        summary = Path(summary)
        try:
            checks = json.loads(summary.read_text())['checks']
        except Exception as error:  # the evaluation script's output is unreadable: a technical error, not a verdict
            return self.fail(f'{summary.name} could not be read: {type(error).__name__}: {error}'[:2000])
        try:
            files = self._upload([summary, *evidence])
        except Exception as error:  # a local file or upload failed: close the evaluation as a technical error
            return self.fail(f'evaluation evidence could not be uploaded: {type(error).__name__}: {error}'[:2000])
        record = {**self.base, 'status': 'done', 'checks': checks, 'files': files, 'endedAt': self._now(),
                  **({'startedAt': self.started} if self.started else {})}
        try:
            return self._put(record)
        except Exception as error:
            if getattr(error, 'status', None) != 400:
                raise
            return self.fail(f'the evaluation summary was refused: {error}'[:2000], files=files)

    def fail(self, error: str, *, files: Optional[list[dict]] = None) -> dict:
        """Record a technical error. If the server cannot be reached to record it, raise saying so: the evaluation may
        still read as running there, and nothing here claims otherwise."""
        try:
            return self._put({**self.base, 'status': 'error', 'error': str(error)[:2000] or 'unknown error', 'files': files or [],
                              'endedAt': self._now(), **({'startedAt': self.started} if self.started else {})})
        except Exception as unreachable:
            raise ExperimentWorkflowError(f'evaluation {self.id} failed ({error}) and that could not be recorded '
                                          f'({type(unreachable).__name__}: {unreachable}); it may still show as running') from unreachable


def _flat(params: Mapping[str, Any], prefix: str = '') -> dict:
    out: dict[str, Any] = {}
    for key, value in params.items():
        name = f'{prefix}{key}'
        if isinstance(value, Mapping):
            out.update(_flat(value, name + '.'))
        else:
            out[name] = value
    return out


TEMPLATE = Path(__file__).parent / 'template'

__all__ = ['TEMPLATE', 'MANIFEST_SCHEMA', 'ANALYSIS_SCHEMA', 'ExperimentFolder', 'Attempt', 'Evaluation', 'ExperimentWorkflowError', 'grid_rows', 'write_grid', 'read_grid',
           'config_slug', 'collect_provenance', 'reproduce_script', 'new_attempt_id']
