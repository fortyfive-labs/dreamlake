import json
import httpx
import pytest
from dreamlake import DreamLakeClient
from dreamlake.host_credentials import HostCredential, save_enrollment_credentials
from dreamlake.vault import Vault
import base64
NAME = "fortyfive/bos14/bos14-ctrl"
KEY = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
GRANT = "synthetic-bootstrap-secret"


def test_secret_repr_and_selection_gate(tmp_path):
    credential = HostCredential('target', 'target', 'alice/target', 'password', password='PRIVATE_SENTINEL')
    assert 'PRIVATE_SENTINEL' not in repr(credential)
    vault = Vault(httpx.Client(base_url='https://example.test', transport=httpx.MockTransport(lambda r: pytest.fail('unselected write'))))
    assert save_enrollment_credentials(vault, {}, [], 'r')['status'] == 'needs_input'
    assert save_enrollment_credentials(vault, {}, [credential, credential], 'r')['status'] == 'failed'


@pytest.mark.parametrize('failure', ['none', 'entry', 'binding', 'cancel', 'account-change'])
def test_enrollment_preserved_and_target_jump_separated(monkeypatch, failure):
    def remote(args, p):
        if failure == 'account-change':
            client._token = 'changed'; client.dl_url = 'https://changed.invalid'
        return {'unixUser': 'ge', 'publicKey': KEY} if p['action'] == 'probe' else {'started': True}
    monkeypatch.setattr('dreamlake.api.hosts._remote', remote)
    writes, bindings = [], []
    def handler(req):
        assert req.headers['Authorization'] == 'Bearer synthetic'
        assert req.url.host != 'changed.invalid'
        body = json.loads(req.content) if req.content else None
        if req.url.path.endswith('/hosts'):
            return httpx.Response(200, json={'hosts': []})
        if req.url.path.endswith('/enrollments'):
            return httpx.Response(200, json={'host': {'id': 'a'*24, 'name': NAME}, 'enrollment': {'id': 'b'*24, 'machineId': 'm1'}, 'operationId': 'c'*24, 'bootstrap': {'namespace': 'distinct-controlplane', 'controlPlaneUrl': 'https://cp.example', 'token': GRANT, 'expiresAt': '2099-01-01T00:00:00.000Z'}})
        if '/hosts/' in req.url.path:
            return httpx.Response(200, json={'enrollments': [{'id': 'b'*24, 'machineId': 'm1', 'state': 'online', 'statusVerified': True}]})
        if req.url.path.endswith('/entry'):
            writes.append(body)
            if failure == 'entry': raise httpx.ReadTimeout('PRIVATE_SENTINEL')
            if failure == 'cancel': raise KeyboardInterrupt()
            meta = dict(id='entry'+str(len(writes)), name=body['name'], type='string', revision=1, deleteAt=None, purgeAt=None)
            return httpx.Response(200, json={'entry': meta, 'replayed': False, 'operation': {'requestId': req.headers['Idempotency-Key'], 'state': 'committed', 'entry': meta, 'committedAt': '2030-01-01T00:00:00.000Z', 'retainUntil': '2030-02-01T00:00:00.000Z'}})
        if req.url.path.endswith('/host-credentials'):
            bindings.append(body)
            if failure == 'binding': return httpx.Response(503, text='PRIVATE_SENTINEL')
            return httpx.Response(200, json={'binding': {**body, 'id': 'binding'+str(len(bindings))}})
        raise AssertionError(req.url.path)
    client = DreamLakeClient(token='synthetic', transport=httpx.MockTransport(handler))
    result = client.hosts.enroll(NAME, ssh='-J jump target', request_id='save-test', save_credentials=True, credentials=[
        HostCredential('target', 'target', 'alice/target', 'password', password='PRIVATE_SENTINEL'),
        HostCredential('jump', 'jump', 'alice/jump', 'password', password='JUMP_SENTINEL'),
    ])
    assert result['enrolled'] is True
    assert 'PRIVATE_SENTINEL' not in json.dumps(result) and 'JUMP_SENTINEL' not in json.dumps(result)
    assert result['credentials']['status'] == {'none': 'saved', 'entry': 'unknown', 'binding': 'partial', 'cancel': 'cancelled', 'account-change': 'saved'}[failure]
    if failure == 'none':
        assert [b['role'] for b in bindings] == ['target', 'jump']
        assert [w['value'] for w in writes] == ['PRIVATE_SENTINEL', 'JUMP_SENTINEL']
        assert len({e['requestId'] for e in result['credentials']['entries']}) == 2


def test_password_never_accepted_in_enrollment_configuration():
    with pytest.raises(ValueError):
        DreamLakeClient(token='synthetic').hosts.plan(config={'name': NAME, 'ssh': 'target', 'password': 'PRIVATE_SENTINEL'})


def test_binding_response_loss_replays_metadata_only():
    bodies = []
    def handler(req):
        assert req.url.path == '/v1/vault/host-credentials'
        data = json.loads(req.content); bodies.append(data)
        if len(bodies) == 1: raise httpx.ReadTimeout('PRIVATE_DIAGNOSTIC')
        return httpx.Response(200, json={'binding': {**data, 'id': 'binding'}})
    vault = Vault(httpx.Client(base_url='https://example.test', transport=httpx.MockTransport(handler)))
    args = dict(host_id='a'*24,enrollment_id='b'*24,role='target',endpoint='target',kind='password',entry_id='entry',entry_revision=1)
    from dreamlake.vault import VaultError
    with pytest.raises(VaultError) as error: vault.bind_host_credential(**args)
    assert 'PRIVATE_DIAGNOSTIC' not in str(error.value)
    assert vault.bind_host_credential(**args)['id'] == 'binding'
    assert bodies[0] == bodies[1] and 'value' not in bodies[0]


def test_invalid_binding_lookup_never_reaches_transport():
    vault = Vault(httpx.Client(base_url='https://example.test', transport=httpx.MockTransport(lambda r: pytest.fail('transport effect'))))
    from dreamlake.vault import VaultError
    with pytest.raises(VaultError):
        vault.host_credentials(host_id='PRIVATE_SENTINEL', enrollment_id='b'*24)
