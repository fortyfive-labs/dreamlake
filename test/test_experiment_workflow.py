"""Experiment workflow SDK: plan slugs, attempt records, idempotent uploads. The selection rule itself is the
server's (dreamlake-server src/services/experimentWorkflow.ts) and is not re-tested here."""
import threading

import pytest

from dreamlake.experiments.workflow import Attempt, ExperimentFolder, ExperimentWorkflowError, grid_rows, reproduce_script
from dreamlake.experiments.workflow.__main__ import main as cli


class Missing(Exception):
    status = 404


class FakeProject:
    """The few native routes the workflow calls, in memory; `PUT run-paths` with createOnly as the server does it."""

    def __init__(self):
        self.namespace, self.project = 'ns', 'p'
        self.runs_by_path, self.metrics_rows, self.files_by_run, self.lock = {}, {}, {}, threading.Lock()
        self.folder_paths = {}
        self.folders = type('F', (), {'create': lambda _, path: self.folder_paths.setdefault('f' * 24, {'id': 'f' * 24, 'path': path}),
                                      'get': lambda _, folder_id: self.folder_paths[folder_id]})()
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
    assert {'train.py', 'play.py', 'analysis.py', 'ppo_lr_generate.py', 'ppo_experiment_lr_launch.py', 'README.md', '.dashrc'} <= {p.name for p in (tmp_path / 'sweep').iterdir()}


class Refused(Exception):
    status = 400


class EvalProject(FakeProject):
    """Adds the evaluation PUT, project folders/files (with sha256 checksums, in upload order) and the contract."""

    def __init__(self, refuse_checks=False):
        super().__init__()
        import hashlib
        self.puts, self.order, self.held, self.refuse_checks, self.contract_fails = [], [], {}, refuse_checks, False
        project = self
        digest = lambda path: hashlib.sha256(open(path, 'rb').read()).hexdigest()
        def upload(_, path, folder_id, name, **kw):
            project.order.append((folder_id, name))
            project.held[(folder_id, name)] = {'filename': name, 'id': f'{folder_id}/{name}', 'checksum': digest(path)}
        def replace(_, file_id, path, **kw):
            project.order.append(('replace', file_id))
            folder_id, name = file_id.rsplit('/', 1)
            project.held[(folder_id, name)]['checksum'] = digest(path)
        self.folders = type('F', (), {'create': lambda _, path: {'id': path, 'path': path},
                                      'get': lambda _, folder_id: {'id': folder_id, 'path': folder_id}})()
        self.project_files = type('PF', (), {'list': lambda _, folder_id: [f for (fid, _n), f in project.held.items() if fid == folder_id],
                                             'upload': upload, 'replace': replace})()
        self.files = type('Files', (), {'upload': lambda _, run_id, path, **kw: {'file': {'id': 'a' * 24, 'contentType': 'image/png'}},
                                        'list': lambda _, run_id, **kw: {'files': []}})()

    def _request(self, method, path, body=None, params=None, **_):
        if path.endswith('/evaluation'):
            if self.refuse_checks and body['evaluation'].get('checks'):
                raise Refused('checks[0].result is "ok"')
            self.puts.append(body)
            return {'evaluation': body['evaluation'], 'changed': True}
        if method == 'POST' and path == '/folders':  # child folders by parent id
            return {'folder': {'id': f"{body['parentId']}/{body['name']}", 'parentId': body['parentId']}}
        if method == 'GET' and path.startswith('/experiments/'):
            if self.contract_fails:
                raise ConnectionError('API unreachable')
            return {'contract': {'inputs': {'key': 'k1'}, 'analysis': {'state': 'current', 'report': {'id': 'g1'}}}}
        return super()._request(method, path, body=body)


def test_an_evaluation_records_its_checkpoint_bytes_and_a_refused_summary_is_an_error_not_a_verdict(tmp_path):
    ckpt = tmp_path / 'ckpt.json'
    ckpt.write_text('{"step": 10}')
    summary = tmp_path / 'summary.json'
    summary.write_text('{"checks": [{"name": "x", "result": "ok", "reason": "r"}]}')
    f = ExperimentFolder(EvalProject(refuse_checks=True), 'ppo/lr-sweep')
    attempt = f.attempt('s0', params={}, provenance=PROV).open()
    evaluation = attempt.evaluation(checkpoint=ckpt, step=10).start()
    evaluation.finish(summary)
    running, error = f.project.puts
    assert running['evaluation']['status'] == 'running' and len(running['evaluation']['checkpoint']['sha256']) == 64
    assert error['evaluation']['status'] == 'error' and 'refused' in error['evaluation']['error']
    # Missing evidence on disk closes it as an error too, never leaving it running.
    evaluation.finish(summary, evidence=[tmp_path / 'missing.mp4'])
    assert f.project.puts[-1]['evaluation']['status'] == 'error' and 'could not be uploaded' in f.project.puts[-1]['evaluation']['error']


def test_a_worker_records_its_own_attempt_with_known_ids_and_creates_no_folder(tmp_path):
    project = EvalProject()
    project.folders = None  # a bound worker credential cannot create folders: any attempt would fail here
    summary = tmp_path / 'summary.json'
    summary.write_text('{"checks": [{"name": "x", "result": "pass", "reason": "r"}]}')
    folder = ExperimentFolder(project, 'ppo/lr-sweep', folder_id='f' * 24)
    attempt = Attempt.existing(folder, run='s0', attempt_id='a1', run_id='b' * 24)
    attempt.evaluation(checkpoint=tmp_path / 'absent.ckpt', step=10).start().finish(summary)
    assert project.puts[-1]['evaluation']['status'] == 'done'
    assert 'sha256' not in project.puts[-1]['evaluation']['checkpoint']  # path only: the server shows it unverified


def _generation(tmp_path, text='# r'):
    gen = tmp_path / 'gen'
    gen.mkdir(exist_ok=True)
    (gen / 'report.md').write_text(text)
    (gen / 'endpoint.svg').write_text('<svg/>')
    return gen, {'id': 'g1', 'status': 'complete', 'criteria': [], 'report': 'report.md',
                 'figures': [{'name': 'e', 'label': 'e', 'file': 'endpoint.svg'}], 'inputs': {'key': 'k1'}}


def test_publish_analysis_uploads_the_generation_first_and_the_pointer_last_and_reconciles_a_retry(tmp_path):
    f = ExperimentFolder(EvalProject(), 'ppo/lr-sweep')
    gen, report = _generation(tmp_path)
    out = f.publish_analysis(gen, report)
    names = [name for _, name in f.project.order]
    assert names[-1] == 'report.json' and f.project.order[-1][0] == 'ppo/lr-sweep/analysis'
    assert {'report.md', 'endpoint.svg'} <= set(names[:-1]) and out == {**out, 'verified': True, 'state': 'current'}
    uploads = len(f.project.order)
    f.publish_analysis(gen, report)  # the same id, files and report: nothing re-uploaded, pointer untouched
    assert len(f.project.order) == uploads


def test_a_generation_is_immutable_and_declared_files_must_exist(tmp_path):
    f = ExperimentFolder(EvalProject(), 'ppo/lr-sweep')
    gen, report = _generation(tmp_path)
    f.publish_analysis(gen, report)
    uploads = len(f.project.order)
    (gen / 'report.md').write_text('# other text')
    with pytest.raises(ExperimentWorkflowError, match='new id'):
        f.publish_analysis(gen, report)
    with pytest.raises(ExperimentWorkflowError, match='new id'):
        (gen / 'report.md').write_text('# r')
        f.publish_analysis(gen, {**report, 'criteria': [{'name': 'x', 'result': 'pass', 'reason': 'r'}]})
    (gen / 'endpoint.svg').unlink()
    with pytest.raises(ExperimentWorkflowError, match='not in'):
        f.publish_analysis(gen, {**report, 'id': 'g2'})
    assert len(f.project.order) == uploads  # nothing uploaded and the pointer still names g1


def test_a_failed_read_after_publishing_is_reported_unverified_not_republished(tmp_path):
    project = EvalProject()
    f = ExperimentFolder(project, 'ppo/lr-sweep')
    gen, report = _generation(tmp_path)
    project.contract_fails = True
    out = f.publish_analysis(gen, report)
    assert out['published'] and out['verified'] is False and 'unreachable' in out['error']


def test_a_renamed_experiment_folder_is_followed_by_id_never_recreated_at_its_old_path():
    project = FakeProject()
    f = ExperimentFolder(project, 'ppo/lr-sweep')
    f.attempt('s0', params={}, provenance=PROV).open()
    project.folder_paths['f' * 24]['path'] = 'archive/lr-sweep'  # renamed and moved elsewhere, same id
    f.attempt('s1', params={}, provenance=PROV).open()
    assert 'archive/lr-sweep/s1.' in next(k for k in project.runs_by_path if '/s1.' in k)
    assert not any(k.startswith('ppo/lr-sweep/s1') for k in project.runs_by_path)
