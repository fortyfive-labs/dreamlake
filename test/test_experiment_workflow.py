"""Experiment workflow SDK: plan slugs, attempt records, idempotent uploads. The selection rule itself is the
server's (dreamlake-server src/services/experimentWorkflow.ts) and is not re-tested here."""
import pytest

from dreamlake.experiments.workflow import ExperimentFolder, ExperimentWorkflowError, grid_rows


class Missing(Exception):
    status = 404


class FakeProject:
    """The few native routes the workflow calls, in memory."""

    def __init__(self):
        self.runs_by_path, self.metrics_rows, self.appended = {}, {}, []
        self.folders = type('F', (), {'create': lambda _, path: {'id': 'f' * 24, 'path': path}})()
        self.parameters = type('P', (), {'set': lambda *_: None})()
        self.files = type('Files', (), {'upload': lambda *_, **__: None})()
        project = self
        self.metrics = type('M', (), {
            'list': lambda _, run_id: [{'name': name, 'count': len(rows)} for (rid, name), rows in project.metrics_rows.items() if rid == run_id],
            'append': lambda _, run_id, name, points: project.metrics_rows.setdefault((run_id, name), []).extend(points)})()

    def by_path(self, path):
        if path not in self.runs_by_path:
            raise Missing()
        return {'kind': 'run', 'experiment': self.runs_by_path[path]}

    def _request(self, method, path, body=None, **_):
        assert method == 'PUT' and path.startswith('/run-paths/')
        run = {'id': f'{len(self.runs_by_path) + 1:024x}', 'metadata': body['metadata']}
        self.runs_by_path[path[len('/run-paths/'):]] = run
        return {'experiment': run}


def folder():
    return ExperimentFolder(FakeProject(), 'ppo/lr-sweep')


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
    record = f.attempt('a-s0', kind='resume', parent='p1', checkpoint='ckpt/30.pt', step=30, provenance={'command': 'x'}).record
    assert record['resume'] == {'step': 30, 'checkpoint': 'ckpt/30.pt'}


def test_reconnect_reopens_the_same_run_and_never_duplicates_points():
    f = folder()
    rows = [{'step': s, 'return': s} for s in range(10)]
    first = f.attempt('a-s0', attempt_id='job-1', provenance={'command': 'x', 'cwd': '/w'}).open()
    assert first.sync_metric('eval', rows[:4]) == 4
    again = f.attempt('a-s0', attempt_id='job-1', provenance={'command': 'x', 'cwd': '/w'}).open()
    assert again.reconnected and again.run_id == first.run_id
    assert again.sync_metric('eval', rows) == 6 and again.sync_metric('eval', rows) == 0
    assert f.project.metrics_rows[(first.run_id, 'eval')] == rows
    with pytest.raises(ExperimentWorkflowError):
        f.attempt('a-s0', attempt_id='job-1', kind='resume', parent='p', checkpoint='c', step=1, provenance={'command': 'x'}).open()


def test_an_existing_metric_without_a_readable_count_is_never_treated_as_empty():
    f = folder()
    attempt = f.attempt('a-s0', provenance={'command': 'x', 'cwd': '/w'}).open()
    f.project.metrics.list = lambda run_id: [{'name': 'eval'}]
    with pytest.raises(ExperimentWorkflowError):
        attempt.sync_metric('eval', [{'step': 0}])
