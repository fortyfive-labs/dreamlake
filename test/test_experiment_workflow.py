"""Experiment workflow SDK: plan slugs, attempt records, idempotent uploads. The selection rule itself is the
server's (dreamlake-server src/services/experimentWorkflow.ts) and is not re-tested here."""
import threading

import pytest

from dreamlake.experiments.workflow import ExperimentFolder, ExperimentWorkflowError, grid_rows, reproduce_script
from dreamlake.experiments.workflow.__main__ import main as cli


class Missing(Exception):
    status = 404


class FakeProject:
    """The few native routes the workflow calls, in memory; `PUT run-paths` with createOnly as the server does it."""

    def __init__(self):
        self.namespace, self.project = 'ns', 'p'
        self.runs_by_path, self.metrics_rows, self.files_by_run, self.lock = {}, {}, {}, threading.Lock()
        self.folders = type('F', (), {'create': lambda _, path: {'id': 'f' * 24, 'path': path}})()
        self.parameters = type('P', (), {'set': lambda *_: None})()
        project = self
        self.files = type('Files', (), {
            'upload': lambda _, run_id, path, **kw: project.files_by_run.setdefault(run_id, []).append(
                {'filename': kw.get('name') or path.name, 'prefix': kw.get('prefix', '/')}),
            'list': lambda _, run_id, **kw: {'files': project.files_by_run.get(run_id, [])}})()
        self.metrics = type('M', (), {
            'list': lambda _, run_id: [{'name': name, 'count': len(rows)} for (rid, name), rows in project.metrics_rows.items() if rid == run_id],
            'append': lambda _, run_id, name, points: project.metrics_rows.setdefault((run_id, name), []).extend(points)})()
        self._api = type('A', (), {'read_metric': lambda _, ns, p, run_id, name, start_index, limit:
                                   {'rows': project.metrics_rows[(run_id, name)][start_index:start_index + limit]}})()

    def by_path(self, path):
        if path not in self.runs_by_path:
            raise Missing()
        return {'kind': 'run', 'experiment': self.runs_by_path[path]}

    def _request(self, method, path, body=None, **_):
        assert method == 'PUT' and path.startswith('/run-paths/') and body['createOnly'] is True
        key = path[len('/run-paths/'):]
        with self.lock:  # the server's name uniqueness: one request creates, every other one reads it unchanged
            if key not in self.runs_by_path:
                self.runs_by_path[key] = {'id': f'{len(self.runs_by_path) + 1:024x}', 'metadata': body['metadata']}
            return {'experiment': self.runs_by_path[key]}


def folder():
    return ExperimentFolder(FakeProject(), 'ppo/lr-sweep')


PROV = {'command': 'x', 'cwd': '/w'}


def test_slugs_come_from_the_configuration_only_and_collisions_are_refused():
    rows = grid_rows([{'train.lr': 3e-4, 'train.seed': s, 'train.steps': 10} for s in (0, 1)], changed=['train.lr'], seed_key='train.seed')
    again = grid_rows([{'train.steps': 10, 'train.seed': 0, 'train.lr': 3e-4}], changed=['train.lr'], seed_key='train.seed')
    assert [r['slug'] for r in rows] == [rows[0]['config'] + '-s0', rows[0]['config'] + '-s1'] and rows[0]['config'] == rows[1]['config']
    assert again[0]['slug'] == rows[0]['slug'] and rows[0]['config'].startswith('lr0.0003-')
    other = grid_rows([{'train.lr': 3e-4, 'train.seed': 0, 'train.steps': 20}], changed=['train.lr'], seed_key='train.seed')
    assert other[0]['config'] != rows[0]['config']
    with pytest.raises(ValueError):
        grid_rows([{'train.lr': 1, 'train.seed': 0}] * 2, changed=['train.lr'], seed_key='train.seed')


def test_a_resume_needs_parent_checkpoint_and_absolute_step_and_a_fresh_attempt_has_none():
    f = folder()
    with pytest.raises(ValueError):
        f.attempt('a-s0', kind='resume', parent='p1', checkpoint='ckpt/30.pt')
    with pytest.raises(ValueError):
        f.attempt('a-s0', parent='p1')
    assert f.attempt('a-s0', kind='resume', parent='p1', checkpoint='ckpt/30.pt', step=30, provenance=PROV).record['resume'] == \
        {'step': 30, 'checkpoint': 'ckpt/30.pt'}


def test_concurrent_opens_of_one_attempt_id_make_one_run_and_refuse_a_conflicting_identity():
    f = folder()
    results, errors = [], []

    def open_(params, host):
        try:
            results.append(f.attempt('a-s0', attempt_id='job-1', params=params, provenance={**PROV, 'host': host}).open())
        except ExperimentWorkflowError as error:
            errors.append(str(error))
    threads = [threading.Thread(target=open_, args=({'lr': 1}, f'h{i}')) for i in range(4)] + \
              [threading.Thread(target=open_, args=({'lr': 2}, 'other'))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stored = f.project.runs_by_path['ppo/lr-sweep/a-s0.job-1']['metadata']['experiment']
    if stored['params'] == {'lr': 1}:
        assert len(results) == 4 and len(errors) == 1 and 'params' in errors[0]
    else:  # the conflicting request happened to create it: then all four equal ones are refused
        assert len(results) == 1 and len(errors) == 4
    assert len({a.run_id for a in results}) == 1 and sum(not a.reconnected for a in results) == 1
    assert len(f.project.runs_by_path) == 1


def test_reconnect_keeps_the_stored_link_and_provenance_and_never_duplicates_points():
    f = folder()
    rows = [{'step': s, 'return': s} for s in range(10)]
    first = f.attempt('a-s0', attempt_id='r1', kind='resume', parent='p', checkpoint='/abs/ckpt/3.json', step=3, provenance=PROV)
    first.folder.contract = lambda: {'runs': [{'run': 'a-s0', 'attempts': [{'attempt': 'p'}]}]}
    first.open()
    assert first.sync_metric('eval', rows[:4]) == 4
    again = f.attach('a-s0', 'r1').open()   # no kind/parent/step given: they come from the stored record
    assert again.reconnected and again.run_id == first.run_id and again.record['kind'] == 'resume'
    assert again.record['provenance']['opened_by'] == first.record['provenance']['opened_by']  # the creator's, kept
    assert again.sync_metric('eval', rows) == 6 and again.sync_metric('eval', rows) == 0
    assert f.project.metrics_rows[(first.run_id, 'eval')] == rows
    with pytest.raises(ExperimentWorkflowError):
        f.attach('a-s0', 'nope')


def test_sync_refuses_invalid_values_a_different_file_and_an_unreadable_count():
    f = folder()
    attempt = f.attempt('a-s0', provenance=PROV).open()
    with pytest.raises(ExperimentWorkflowError):
        attempt.sync_metric('eval', [{'step': 0, 'return': float('nan')}])
    attempt.sync_metric('eval', [{'step': 0, 'return': 1.0}])
    with pytest.raises(ExperimentWorkflowError, match='not the same append-only file'):
        attempt.sync_metric('eval', [{'step': 0, 'return': 2.0}, {'step': 1, 'return': 3.0}])
    f.project.metrics.list = lambda run_id: [{'name': 'eval'}]
    with pytest.raises(ExperimentWorkflowError):
        attempt.sync_metric('eval', [{'step': 0, 'return': 1.0}])


def test_reproduce_script_does_not_claim_exactness_with_untracked_files(tmp_path):
    text = reproduce_script({'cwd': '/w', 'commit': 'abc', 'command': 'python train.py --train.out /w/runs/x', 'exact': False,
                             'untracked_count': 1, 'untracked': ['helper.py']})
    assert 'NOT an exact reproduction' in text and 'helper.py' in text and text.rstrip().endswith('--train.out /w/runs/x')


def test_init_copies_the_template(tmp_path):
    assert cli(['init', str(tmp_path / 'sweep')]) == 0
    assert {'train.py', 'ppo_lr_generate.py', 'ppo_experiment_lr_launch.py', 'README.md', '.dashrc'} <= {p.name for p in (tmp_path / 'sweep').iterdir()}
