"""Real loopback broker: native SDK recording and renewal without a user login."""
import json
import hashlib
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from dreamlake import Experiment, NativeConnection
from dreamlake.experiments._native.native_job import JobNativeConnection

@pytest.fixture
def broker(monkeypatch):
    # 'refuse': (status, body) answers for the next data POSTs, before any write.
    state = {'calls': [], 'override': {}, 'status': 200, 'refuse': []}
    artifact = {'id': 'b'*24, 'filename': 'model.txt', 'prefix': '/models',
                'checksum': hashlib.sha256(b'model state').hexdigest()}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get('content-length', '0')))
            data = raw if self.path.endswith('/files') else json.loads(raw or b'{}')
            state['calls'].append((self.path, data, self.headers.get('authorization')))
            if self.path == '/session':
                assert data == {}
                assert self.headers.get('authorization') == 'Bearer synthetic-broker'
                result = dict(apiUrl=state['url'] + '/native-experiments/ns/lab/projects/research', experimentUrl=state['url'] + '/lab/projects/research/experiments?run=aaaaaaaaaaaaaaaaaaaaaaaa',
                    namespace='lab', project='research', runKey='job-attempt', runId='aaaaaaaaaaaaaaaaaaaaaaaa',
                    instanceId='dreamlake-native-v1', projectId='cccccccccccccccccccccccc', workloadId='workload-one', generation=1,
                    token='synthetic-lease', expiresAt=time.time()+60)
                result.update(state['override'])
                status = state['status']
            else:
                assert self.headers.get('authorization') == 'Bearer synthetic-lease'
                result, status = (artifact if self.path.endswith('/files') else {'count': 1}), 200
                if state['refuse']:
                    status, result = state['refuse'].pop(0)
            payload = json.dumps(result).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            state['calls'].append((self.path, None, self.headers.get('authorization')))
            assert self.headers.get('authorization') == 'Bearer synthetic-lease'
            if self.path.endswith('/files'):
                data = json.dumps({'files': [artifact], 'hasMore': False}).encode()
            elif self.path.endswith('/files/' + artifact['id']):
                data = json.dumps({'file': artifact}).encode()
            elif self.path.endswith('/files/' + artifact['id'] + '/download'):
                data = b'model state'
            elif self.path.endswith('/parameters'):
                data = json.dumps({'parameters': {'data': {'lr': 0.001}}}).encode()
            else:
                raise AssertionError(self.path)
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    state['url'] = f'http://127.0.0.1:{server.server_port}'
    monkeypatch.setenv('DREAMLAKE_EXPERIMENT_BROKER', state['url'])
    monkeypatch.setenv('DREAMLAKE_EXPERIMENT_BROKER_TOKEN', 'synthetic-broker')
    monkeypatch.setenv('ML_DASH_NO_VERSION_CHECK', '1')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_native_job_uses_existing_run_and_reads_writes_artifacts(broker, monkeypatch, tmp_path):
    broker['override'].update(instanceId='dreamlake-native-v1',
        apiUrl=broker['url'] + '/native-experiments/ns/lab/projects/research')
    monkeypatch.setenv('ML_DASH_BUFFER_ENABLED', 'false')
    with Experiment().run as exp:
        exp.params.set(lr=0.001)
        assert exp.params.get()['lr'] == 0.001
        exp.metrics('loss').log(value=0.5)
        exp.files('models').save_text('model state', to='model.txt')
        assert [file['id'] for file in exp.files('models').list()] == ['b'*24]
        path = exp.files('models/model.txt').download(to=str(tmp_path / 'model.txt'))
        assert path == str(tmp_path / 'model.txt')
        assert (tmp_path / 'model.txt').read_text() == 'model state'
    calls = broker['calls']
    assert all(path == '/session' or path.startswith('/native-experiments/ns/lab/projects/research/runs/aaaaaaaaaaaaaaaaaaaaaaaa/')
               for path, _, _ in calls)
    assert not any(path.endswith('/status') for path, _, _ in calls)


@pytest.mark.parametrize('denied', [401, 403])
def test_multiple_renewals_then_revocation_never_fall_back(broker, denied):
    c = JobNativeConnection()
    client = c.client()
    for _ in range(3):
        client._credential['expiresAt'] = 0
        client.set_parameters('aaaaaaaaaaaaaaaaaaaaaaaa', {'x': 1})
    assert sum(p == '/session' for p, _, _ in broker['calls']) == 4
    client._credential['expiresAt'] = 0
    broker['status'] = denied
    broker['override']['message'] = 'never-print-synthetic-secret'
    with pytest.raises(ValueError) as error:
        client.set_parameters('aaaaaaaaaaaaaaaaaaaaaaaa', {})
    assert 'synthetic' not in str(error.value)
    assert all(p == '/session' or p.startswith('/native-experiments/ns/lab/projects/research/runs/aaaaaaaaaaaaaaaaaaaaaaaa/') for p, _, _ in broker['calls'])
    client.close()


@pytest.mark.parametrize('field,value', [('runId','another-run'), ('runKey','another-key'), ('namespace','elsewhere'), ('project','other'), ('workloadId','other-workload'), ('generation',2), ('apiUrl','https://other.test/api/managed'), ('experimentUrl','https://other.test/view')])
def test_changed_pins_fail_before_write(broker, field, value):
    c = JobNativeConnection()
    c.client()._credential['expiresAt'] = 0
    broker['override'][field] = value
    with pytest.raises(ValueError, match='Job experiment session'):
        c.client().set_parameters('aaaaaaaaaaaaaaaaaaaaaaaa', {})
    assert all(p == '/session' for p, _, _ in broker['calls'])
    c.client().close()


def test_missing_secret_and_missing_endpoint_fail_closed(monkeypatch):
    monkeypatch.setenv('DREAMLAKE_EXPERIMENT_BROKER', 'http://127.0.0.1:1234')
    monkeypatch.delenv('DREAMLAKE_EXPERIMENT_BROKER_TOKEN', raising=False)
    with pytest.raises(ValueError):
        Experiment()
    monkeypatch.delenv('DREAMLAKE_EXPERIMENT_BROKER')
    monkeypatch.setenv('DREAMLAKE_EXPERIMENT_BROKER_TOKEN', 'synthetic-broker')
    with pytest.raises(ValueError):
        Experiment()


def test_explicit_scope_and_standalone_conflicts_rejected(broker):
    for options in [dict(prefix='lab/research/other'), dict(dash_url=True), dict(dash_root='/tmp/other'), dict(managed=NativeConnection('https://example.test','lab','research',lambda:'unused'))]:
        with pytest.raises(ValueError):
            Experiment(**options)
    c = JobNativeConnection()
    with pytest.raises(ValueError):
        c.client().set_parameters('other-run', {})
    with pytest.raises(ValueError):
        c.client().create_or_update_experiment('research','other',prefix='lab/research/other')
    with pytest.raises(ValueError, match='compute receipt'):
        c.client().update_experiment_status('aaaaaaaaaaaaaaaaaaaaaaaa', 'CANCELLED')
    c.client().close()
