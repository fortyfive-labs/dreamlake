"""Native DreamLake recording, sessions and file downloads at the HTTP boundary."""
import json
import os
import httpx
import pytest
from dreamlake import Experiment, NativeConnection
ROOT = "/native-experiments/ns/lab/projects/research"
RUN_ID = "a" * 24

def test_native_experiment_writes_only_native_routes_with_project_sessions(monkeypatch):
    calls, issued, rejected = [], [0], []

    def handle(request):
        path, auth = request.url.path, request.headers.get('authorization')
        body = json.loads(request.content) if request.headers.get('content-type') == 'application/json' else None
        calls.append((request.method, request.url.host, path, auth, body))
        if path == ROOT + '/session':
            issued[0] += 1
            return httpx.Response(200, json={'token': f'session-{issued[0]}', 'expiresAt': 0,
                                             'apiUrl': 'https://dreamlake.test' + ROOT,
                                             'workloadId': body.get('workloadId') or body['idempotencyKey'], 'generation': 1})
        if path.endswith('/parameters') and not rejected:
            rejected.append(auth)  # an expired session: refused before any write
            return httpx.Response(401, json={'error': {'code': 'NATIVE_LOGIN_REQUIRED'}})
        if request.method == 'PUT':
            return httpx.Response(200, json={'experiment': {'id': RUN_ID, **(body or {})}})
        return httpx.Response(200, json={'count': 1})

    real_client = httpx.Client
    monkeypatch.setattr('dreamlake.experiments._native.native.httpx.Client',
                        lambda **kw: real_client(**{**kw, 'transport': httpx.MockTransport(handle)}))
    connection = NativeConnection('https://dreamlake.test', 'lab', 'research', lambda: 'user-login')
    with Experiment(prefix='lab/research/probe', managed=connection, readme='', tags=['lr', 'v2']).run as exp:
        exp.params.set(lr=0.001)
        exp.log('hello')
        exp.metrics('train').log(step=0, loss=0.5)

    assert all(host == 'dreamlake.test' and path.startswith(ROOT + '/') for _, host, path, _, _ in calls)
    # The user's login only mints project sessions; data routes carry a session.
    assert {auth for _, _, path, auth, _ in calls if path.endswith('/session')} == {'Bearer user-login'}
    assert all(auth.startswith('Bearer session-') for _, _, path, auth, _ in calls if not path.endswith('/session'))
    put = next(c for c in calls if c[0] == 'PUT')
    assert put[2] == ROOT + '/runs/probe' and put[4] == {'description': '', 'tags': ['lr', 'v2']}
    # The 401 parameters write was retried once with a fresh session and then succeeded.
    params = [c for c in calls if c[2].endswith('/parameters')]
    assert [c[3] for c in params] == ['Bearer session-1', 'Bearer session-2']
    assert params[1][4] == {'data': {'lr': 0.001}}
    written = {c[2].rsplit('/', 1)[-1]: c[4] for c in calls if c[0] == 'POST' and RUN_ID in c[2]}
    assert written['logs']['entries'][0]['message'] == 'hello'
    assert written['train']['dataPoints'][0]['loss'] == 0.5
    assert written['status'] == {'status': 'COMPLETED'}
    # One writer per client, as ManagedConnection: registered once, then renewed by its id.
    sessions = [c[4] for c in calls if c[2].endswith('/session')]
    assert set(sessions[0]) == {'mode', 'idempotencyKey'} and sessions[1] == {'mode': 'write', 'workloadId': sessions[0]['idempotencyKey']}

def _writer_server(monkeypatch, state):
    """A native server double for writer sessions: generation, retirement and the bound run."""
    calls = []

    def handle(request):
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, path, request.headers.get('authorization'), body))
        if path == ROOT + '/session':
            writer = body.get('workloadId') or body.get('idempotencyKey')
            if not state['active']:
                return httpx.Response(403, json={'error': {'code': 'NATIVE_CREDENTIAL_RETIRED'}})
            return httpx.Response(200, json={'token': f'gen-{state["generation"]}', 'expiresAt': 0,
                                             'apiUrl': 'https://dreamlake.test' + ROOT, 'workloadId': writer,
                                             'generation': state['generation'], **({'runId': body['runId']} if 'runId' in body else {})})
        if request.headers.get('authorization') == state.get('expired'):
            return httpx.Response(401, json={'error': {'code': 'NATIVE_SESSION_EXPIRED'}})
        if request.headers.get('authorization') != f'Bearer gen-{state["generation"]}' or not state['active']:
            return httpx.Response(403, json={'error': {'code': 'NATIVE_CREDENTIAL_RETIRED'}})
        if request.method in ('GET', 'PATCH') and path == f'{ROOT}/runs/{RUN_ID}':
            return httpx.Response(200, json={'experiment': {'id': RUN_ID, 'name': 'renamed'}})
        if request.method == 'GET' and path == f'{ROOT}/runs/{RUN_ID}/metrics':
            return httpx.Response(200, json={'metrics': []})
        return httpx.Response(200, json={'count': 1})

    real_client = httpx.Client
    monkeypatch.setattr('dreamlake.experiments._native.native.httpx.Client', lambda **kw: real_client(**{**kw, 'transport': httpx.MockTransport(handle)}))
    return calls

def test_native_writer_renews_after_rotation_and_is_never_replaced_once_retired(monkeypatch):
    from dreamlake.experiments._native.native import NativeExperimentError
    state = {'generation': 1, 'active': True}
    calls = _writer_server(monkeypatch, state)
    writer = '0b0b0b0b-0000-4000-8000-000000000001'
    client = NativeConnection('https://dreamlake.test', 'lab', 'research', lambda: 'ci-login', workload_id=writer).client()
    client.set_parameters(RUN_ID, {'lr': 1})
    state['generation'] = 2  # rotated: the old session is refused once, the next call renews under generation 2
    with pytest.raises(NativeExperimentError) as refused:
        client.set_parameters(RUN_ID, {'lr': 2})
    assert refused.value.code == 'NATIVE_CREDENTIAL_RETIRED'
    client.set_parameters(RUN_ID, {'lr': 3})
    state['active'] = False  # retired
    with pytest.raises(NativeExperimentError):
        client.set_parameters(RUN_ID, {'lr': 4})
    with pytest.raises(NativeExperimentError) as ended:
        client.set_parameters(RUN_ID, {'lr': 5})
    assert ended.value.code == 'NATIVE_CREDENTIAL_RETIRED'
    sessions = [c[3] for c in calls if c[1].endswith('/session')]
    # Every session names the given writer; none registers a replacement.
    assert sessions and all(s == {'mode': 'write', 'workloadId': writer} for s in sessions)
    assert {'data': {'lr': 3}} in [c[3] for c in calls if c[1].endswith('/parameters')]

def test_native_explicit_writer_read_renews_its_writer_session_once_on_401(monkeypatch):
    state = {'generation': 1, 'active': True}
    calls = _writer_server(monkeypatch, state)
    writer = '0b0b0b0b-0000-4000-8000-000000000001'
    client = NativeConnection('https://dreamlake.test', 'lab', 'research', lambda: 'ci-login', workload_id=writer).client()
    client.set_parameters(RUN_ID, {'lr': 1})
    # The writer's cached session expires: its read is refused (401) once, renews that writer's session, then succeeds.
    state.update(expired='Bearer gen-1', generation=2)
    del calls[:]
    assert client.list_metrics(RUN_ID) == []
    metrics = f'{ROOT}/runs/{RUN_ID}/metrics'
    assert [(c[0], c[1], c[2]) for c in calls] == [('GET', metrics, 'Bearer gen-1'), ('POST', ROOT + '/session', 'Bearer ci-login'),
                                                   ('GET', metrics, 'Bearer gen-2')]
    assert calls[1][3] == {'mode': 'write', 'workloadId': writer}

def test_native_run_bound_writer_opens_its_run_by_id(monkeypatch):
    state = {'generation': 1, 'active': True}
    calls = _writer_server(monkeypatch, state)
    connection = NativeConnection('https://dreamlake.test', 'lab', 'research', lambda: 'ci-login', run_id=RUN_ID)
    client = connection.client()
    assert client.create_or_update_experiment('research', 'probe', prefix='lab/research/probe')['experiment']['id'] == RUN_ID
    assert not any(c[0] == 'PUT' for c in calls)
    session = next(c[3] for c in calls if c[1].endswith('/session'))
    assert session['runId'] == RUN_ID and set(session) == {'mode', 'idempotencyKey', 'runId'}
    with pytest.raises(ValueError):
        NativeConnection('https://dreamlake.test', 'lab', 'research', lambda: 'x', workload_id='not-a-uuid')

def test_native_explicit_writer_never_asks_for_protection(monkeypatch):
    """An explicit writer's write_protected is refused before any request: never the person's login behind it."""
    state = {'generation': 1, 'active': True}
    calls = _writer_server(monkeypatch, state)
    writer = '0b0b0b0b-0000-4000-8000-000000000002'
    for options in ({'workload_id': writer}, {'idempotency_key': writer}, {'run_id': RUN_ID}):
        connection = NativeConnection('https://dreamlake.test', 'lab', 'research', lambda: 'ci-login', **options)
        with pytest.raises(ValueError, match="person's action"):
            Experiment(prefix='lab/research/folder/probe', managed=connection, _write_protected=True).run.start()
    # Anonymous: no login to act as a person; refused cleanly, also before any request.
    from dreamlake.experiments._native.native import NativeExperimentError
    anonymous = NativeConnection.anonymous('https://dreamlake.test', 'lab', 'research').client()
    with pytest.raises(NativeExperimentError) as refused:
        anonymous.create_or_update_experiment('research', 'probe', prefix='lab/research/folder/probe', write_protected=True)
    assert refused.value.code == 'NATIVE_LOGIN_REQUIRED'
    assert calls == []

def test_native_protected_create_uses_one_person_session_pinned_to_its_server(monkeypatch):
    """Interactive write_protected: one plain person's session, minted for the create only, pinned to the project route."""
    calls, advertised = [], ['https://dreamlake.test' + ROOT]

    def handle(request):
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.raw_path.decode(), request.headers.get('authorization'), body))
        if request.url.path == ROOT + '/session':
            return httpx.Response(200, json={'token': 'person-session', 'expiresAt': 0, 'apiUrl': advertised[0]})
        return httpx.Response(200, json={'experiment': {'id': RUN_ID, **body}})

    real_client = httpx.Client
    monkeypatch.setattr('dreamlake.experiments._native.native.httpx.Client', lambda **kw: real_client(**{**kw, 'transport': httpx.MockTransport(handle)}))
    client = NativeConnection('https://dreamlake.test', 'lab', 'research', lambda: 'user-login').client()
    result = client.create_or_update_experiment('research', '实验 1', prefix='lab/research/2026-11-05/实验 1',
                                                write_protected=True, metadata={'seed': 1})
    assert result['experiment']['writeProtected'] is True
    assert calls[0] == ('POST', ROOT + '/session', 'Bearer user-login', {'mode': 'write'})
    assert calls[1][:3] == ('PUT', ROOT + '/run-paths/2026-11-05/%E5%AE%9E%E9%AA%8C%201', 'Bearer person-session')
    assert calls[1][3] == {'metadata': {'seed': 1}, 'writeProtected': True}
    # A session advertised for another server is never used.
    from dreamlake.experiments._native.native import NativeExperimentError
    advertised[0] = 'https://elsewhere.test' + ROOT
    with pytest.raises(NativeExperimentError):
        client.create_or_update_experiment('research', 'p', prefix='lab/research/p', write_protected=True)
    assert [c[0] for c in calls[2:]] == ['POST']

def test_native_default_namespace_and_project_create_refusal(tmp_path, monkeypatch):
    """The login's namespace comes from the active profile (not the OS user); a refused create raises."""
    from dreamlake.experiments._native.native import NativeExperimentError
    monkeypatch.delenv('ML_DASH_NATIVE_NAMESPACE', raising=False)
    (tmp_path / 'config.json').write_text(json.dumps({'remote_url': 'https://dreamlake.test', 'namespace': 'team-lab'}))
    (tmp_path / 'tokens.json').write_text(json.dumps({'dreamlake-token': 'user-login'}))
    assert NativeConnection.default_namespace(config_dir=str(tmp_path)) == 'team-lab'

    def handle(request):
        if request.url.path == '/namespaces/team-lab/projects/scratch':
            return httpx.Response(404, json={'error': 'not found'})  # missing: only then is a create sent
        if request.url.path == '/namespaces/team-lab/projects':
            return httpx.Response(403, json={'error': 'forbidden'})
        raise AssertionError('nothing else may be called after a refused create: ' + request.url.path)

    real_client = httpx.Client
    monkeypatch.setattr('dreamlake.experiments._native.native.httpx.Client', lambda **kw: real_client(**{**kw, 'transport': httpx.MockTransport(handle)}))
    connection = NativeConnection('https://dreamlake.test', 'team-lab', 'scratch', lambda: 'user-login', auto_create_project=True)
    with pytest.raises(NativeExperimentError) as refused:
        connection.client().set_parameters('a' * 24, {'x': 1})
    assert refused.value.status == 403

def test_native_project_bootstrap_uses_an_existing_project_and_never_runs_for_an_explicit_writer(monkeypatch):
    """A WRITE collaborator (no namespace create) writes to the existing project with no create; an explicit writer
    never uses the login to look up or create a project, even with auto_create_project."""
    calls = []

    def handle(request):
        calls.append((request.method, request.url.path, request.headers.get('authorization')))
        if request.url.path == '/namespaces/team-lab/projects/scratch':
            return httpx.Response(200, json={'id': 'f' * 24, 'name': 'scratch'})
        if request.url.path.startswith('/namespaces/'):
            return httpx.Response(403, json={'error': 'no namespace create'})
        if request.url.path.endswith('/session'):
            body = json.loads(request.content)
            return httpx.Response(200, json={'token': 'w', 'expiresAt': 0, 'apiUrl': 'https://dreamlake.test/native-experiments/ns/team-lab/projects/scratch',
                                             'workloadId': body.get('workloadId') or body['idempotencyKey'], 'generation': 1})
        return httpx.Response(200, json={'parameters': {}})

    real_client = httpx.Client
    monkeypatch.setattr('dreamlake.experiments._native.native.httpx.Client', lambda **kw: real_client(**{**kw, 'transport': httpx.MockTransport(handle)}))
    NativeConnection('https://dreamlake.test', 'team-lab', 'scratch', lambda: 'user-login', auto_create_project=True) \
        .client().set_parameters('a' * 24, {'x': 1})
    assert [c[:2] for c in calls if c[1].startswith('/namespaces/')] == [('GET', '/namespaces/team-lab/projects/scratch')]
    assert calls[-1][:2] == ('POST', '/native-experiments/ns/team-lab/projects/scratch/runs/' + 'a' * 24 + '/parameters')

    calls.clear()
    writer = '0b0b0b0b-0000-4000-8000-000000000003'
    NativeConnection('https://dreamlake.test', 'team-lab', 'scratch', lambda: 'user-login', auto_create_project=True,
                     workload_id=writer).client().set_parameters('a' * 24, {'x': 2})
    assert not [c for c in calls if c[1].startswith('/namespaces/')]
    assert [c[1].rsplit('/', 1)[-1] for c in calls] == ['session', 'parameters']

def test_native_metric_names_follow_ml_dash_single_segment_rule(monkeypatch):
    """ml-dash 0.13.0 sent any name as one percent-encoded path segment; `/` and dot segments never routed."""
    seen = []

    def handle(request):
        seen.append(request.url.raw_path.decode())
        if request.url.path.endswith('/session'):
            body = json.loads(request.content)  # a read session, or the writer session contract: echo the writer asked for
            writer = {} if body['mode'] == 'read' else {'workloadId': body.get('workloadId') or body['idempotencyKey'], 'generation': 1}
            return httpx.Response(200, json={'token': 's', 'expiresAt': 0, 'apiUrl': 'https://dreamlake.test' + ROOT, **writer})
        return httpx.Response(200, json={'metric': {'name': 'x'}})

    real_client = httpx.Client
    monkeypatch.setattr('dreamlake.experiments._native.native.httpx.Client', lambda **kw: real_client(**{**kw, 'transport': httpx.MockTransport(handle)}))
    client = NativeConnection('https://dreamlake.test', 'lab', 'research', lambda: 'login').client()
    for name in ('训练 loss', 'a' * 256, 'acc?x#y'):
        client.get_metric_stats(RUN_ID, name)
    assert seen[-3:] == [f'{ROOT}/runs/{RUN_ID}/metrics/%E8%AE%AD%E7%BB%83%20loss/stats',
                         f'{ROOT}/runs/{RUN_ID}/metrics/{"a" * 256}/stats', f'{ROOT}/runs/{RUN_ID}/metrics/acc%3Fx%23y/stats']
    for bad in ('', 'a/b', '.', '..', 'a' * 257, None):
        with pytest.raises(ValueError):
            client.get_metric_stats(RUN_ID, bad)

def test_native_graphql_uses_an_explicit_writers_session(monkeypatch):
    """With an explicit writer, raw GraphQL is sent with that writer's scoped session, never the user's login."""
    seen = []

    def handle(request):
        if request.url.path.endswith('/session'):
            body = json.loads(request.content)
            return httpx.Response(200, json={'token': 'writer-session', 'expiresAt': 0, 'apiUrl': 'https://dreamlake.test' + ROOT,
                                             'workloadId': body.get('workloadId'), 'generation': 1})
        seen.append((request.url.path, request.headers.get('authorization')))
        return httpx.Response(200, json={'data': {'me': None}})

    real_client = httpx.Client
    monkeypatch.setattr('dreamlake.experiments._native.native.httpx.Client', lambda **kw: real_client(**{**kw, 'transport': httpx.MockTransport(handle)}))
    writer = '0b7f3a9e-1c2d-4e5f-8a9b-0c1d2e3f4a5b'
    scoped = NativeConnection('https://dreamlake.test', 'lab', 'research', lambda: 'user-login', workload_id=writer).client()
    scoped.graphql_query('query { me { id } }')
    human = NativeConnection('https://dreamlake.test', 'lab', 'research', lambda: 'user-login').client()
    human.graphql_query('query { me { id } }')
    assert seen == [('/native-dash/graphql', 'Bearer writer-session'), ('/native-dash/graphql', 'Bearer user-login')]

def test_native_delete_project_uses_dreamlakes_project_delete_with_native_purge(monkeypatch):
    """delete_project: DreamLake's project delete, pinned to the project id read first, with the native data purged."""
    from dreamlake.experiments._native.native import NativeExperimentError
    project_id = 'b' * 24
    calls = []

    def handle(request):
        calls.append((request.method, request.url.path, dict(request.url.params), request.headers.get('authorization')))
        if request.method == 'GET' and request.url.path == '/namespaces/lab/projects/research':
            return httpx.Response(200, json={'id': project_id, 'slug': 'research'})
        if request.method == 'GET' and request.url.path == '/namespaces/lab/projects/missing':
            return httpx.Response(404, json={'error': 'not found'})
        if request.method == 'DELETE' and request.url.path == '/namespaces/lab/projects/research':
            return httpx.Response(200, json={'deleted': True, 'project': {'id': project_id}, 'nativeCleanup': {'accepted': True, 'homes': []}})
        raise AssertionError(f'unexpected {request.method} {request.url.path}')

    real_client = httpx.Client
    monkeypatch.setattr('dreamlake.experiments._native.native.httpx.Client', lambda **kw: real_client(**{**kw, 'transport': httpx.MockTransport(handle)}))
    client = NativeConnection('https://dreamlake.test', 'lab', 'research', lambda: 'user-login').client()
    result = client.delete_project('research')
    assert result['projectId'] == project_id and result['deleted'] is True and result['nativeCleanup'] == {'accepted': True, 'homes': []}
    assert calls[-1][:3] == ('DELETE', '/namespaces/lab/projects/research', {'expectedProjectId': project_id, 'nativeExperiments': 'purge'})
    assert calls[-1][3] == 'Bearer user-login'
    with pytest.raises(NativeExperimentError) as missing:
        client.delete_project('missing')
    assert missing.value.status == 404
    # A selected writer or an anonymous connection never deletes a project, and calls nothing.
    calls.clear()
    for connection in (NativeConnection('https://dreamlake.test', 'lab', 'research', lambda: 'user-login', workload_id='00000000-0000-4000-8000-000000000001'),
                       NativeConnection.anonymous('https://dreamlake.test', 'lab', 'research')):
        with pytest.raises(NativeExperimentError) as refused:
            connection.client().delete_project()
        assert refused.value.status == 403
    assert calls == []

def test_native_public_client_methods_download_stream_current_user_and_index_reads(monkeypatch, tmp_path):
    """RemoteClient's download_file_streaming, get_current_user, get_metric_data and download_metric_chunk on native."""
    import hashlib

    from dreamlake.experiments._native.auth.exceptions import AuthenticationError
    from dreamlake.experiments._native.exceptions import ConfigurationError, StorageError

    payload = os.urandom(300_000)
    state = {'checksum': hashlib.sha256(payload).hexdigest(), 'expired': True}
    calls = []

    def handle(request):
        path, auth = request.url.path, request.headers.get('authorization')
        calls.append((request.method, path, auth))
        if path.endswith('/session'):
            body = json.loads(request.content)
            writer = {} if body['mode'] == 'read' else {'workloadId': body.get('workloadId') or body['idempotencyKey'], 'generation': 1}
            return httpx.Response(200, json={'token': f'session-{len(calls)}', 'expiresAt': 0, 'apiUrl': 'https://dreamlake.test' + ROOT, **writer})
        if path == '/auth/me':
            assert auth == 'Bearer user-login'
            return httpx.Response(200, json={'id': 'u1', 'sub': 's', 'email': 'a@b.c', 'name': 'Ada', 'given_name': 'Ada',
                                             'family_name': None, 'picture': None, 'namespace': {'id': 'n1', 'slug': 'ada'}})
        if path.endswith('/download'):
            if state['expired']:  # an expired session, refused before any byte: renewed once
                state['expired'] = False
                return httpx.Response(401, json={'error': {'code': 'NATIVE_LOGIN_REQUIRED'}})
            return httpx.Response(200, stream=httpx.ByteStream(payload))
        return httpx.Response(200, json={'file': {'id': 'f' * 24, 'filename': 'w.bin', 'checksum': state['checksum']}})

    real_client = httpx.Client
    monkeypatch.setattr('dreamlake.experiments._native.native.httpx.Client', lambda **kw: real_client(**{**kw, 'transport': httpx.MockTransport(handle)}))
    client = NativeConnection('https://dreamlake.test', 'lab', 'research', lambda: 'user-login').client()

    dest = tmp_path / 'weights.bin'
    assert client.download_file_streaming(RUN_ID, 'f' * 24, str(dest)) == str(dest)
    assert dest.read_bytes() == payload and [p.name for p in tmp_path.iterdir()] == ['weights.bin']
    downloads = [c for c in calls if c[1].endswith('/download')]
    assert len(downloads) == 2 and downloads[0][2] != downloads[1][2]  # renewed session after the 401
    state['checksum'] = '0' * 64
    with pytest.raises(StorageError):
        client.download_file_streaming(RUN_ID, 'f' * 24, str(tmp_path / 'bad.bin'))
    assert [p.name for p in tmp_path.iterdir()] == ['weights.bin']  # nothing left behind

    assert client.get_current_user() == {'id': 'u1', 'username': 'ada', 'email': 'a@b.c', 'name': 'Ada', 'given_name': 'Ada',
                                         'family_name': None, 'picture': None}
    calls.clear()
    with pytest.raises(AuthenticationError):
        NativeConnection.anonymous('https://dreamlake.test', 'lab', 'research').client().get_current_user()
    for method, args in (('get_metric_data', (RUN_ID, 'loss')), ('download_metric_chunk', (RUN_ID, 'loss', 0))):
        with pytest.raises(ConfigurationError):
            getattr(client, method)(*args)
    assert calls == []

def test_native_stream_refusal_whose_body_cannot_be_read_is_unavailable(monkeypatch, tmp_path):
    """A refused streamed download whose body breaks mid-read raises the native 'unavailable' error, never ResponseNotRead."""
    from dreamlake.experiments._native.native import NativeExperimentError

    class Broken(httpx.SyncByteStream):
        def __iter__(self):
            raise httpx.ReadError('connection reset')

    def handle(request):
        if request.url.path.endswith('/session'):
            body = json.loads(request.content)
            writer = {} if body['mode'] == 'read' else {'workloadId': body.get('workloadId') or body['idempotencyKey'], 'generation': 1}
            return httpx.Response(200, json={'token': 's', 'expiresAt': 0, 'apiUrl': 'https://dreamlake.test' + ROOT, **writer})
        if request.url.path.endswith('/download'):
            return httpx.Response(503, stream=Broken())
        return httpx.Response(200, json={'file': {'id': 'f' * 24, 'filename': 'w.bin', 'checksum': '0' * 64}})

    real_client = httpx.Client
    monkeypatch.setattr('dreamlake.experiments._native.native.httpx.Client', lambda **kw: real_client(**{**kw, 'transport': httpx.MockTransport(handle)}))
    client = NativeConnection('https://dreamlake.test', 'lab', 'research', lambda: 'user-login').client()
    with pytest.raises(NativeExperimentError) as failed:
        client.download_file_streaming(RUN_ID, 'f' * 24, str(tmp_path / 'w.bin'))
    assert failed.value.status is None and 'unavailable' in str(failed.value)
    assert list(tmp_path.iterdir()) == []


def test_native_stream_upload_preserves_filename_and_reopens_after_session_expiry(tmp_path):
    from email import policy
    from email.parser import BytesParser
    import hashlib
    payload = b'\x00\xff\r\n'
    source = tmp_path / '模型,2"%22.bin'
    source.write_bytes(payload)
    received = []
    def handle(request):
        if request.url.path.endswith('/session'):
            body = json.loads(request.content)
            return httpx.Response(200, json={'token': 'session', 'apiUrl': 'https://dreamlake.test'+ROOT,
                'workloadId': body.get('workloadId') or body['idempotencyKey'], 'generation': 1})
        message = BytesParser(policy=policy.default).parsebytes(
            ('Content-Type: '+request.headers['content-type']+'\r\n\r\n').encode()+request.content)
        file = list(message.iter_parts())[-1]
        from urllib.parse import quote
        assert ("filename*=UTF-8''" + quote(source.name, safe='')).encode() in request.content
        received.append(file.get_payload(decode=True))
        return httpx.Response(401 if len(received)==1 else 200, json={'id': 'b'*24})
    client = NativeConnection('https://dreamlake.test', 'lab', 'research', lambda:'login',
                              transport=httpx.MockTransport(handle)).client()
    try:
        client.upload_file(RUN_ID, source, '/', source.name, checksum=hashlib.sha256(payload).hexdigest())
        assert received == [payload] * 2
    finally:
        client.close()
