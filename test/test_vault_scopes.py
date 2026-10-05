import httpx
import pytest
from dreamlake.vault import Vault, VaultError

SCOPE = 'team:' + 'a' * 24

def test_scoped_requests_and_recovery_never_fall_back():
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(403, json={'error': {'code': 'VAULT_SCOPE_FORBIDDEN'}})
    personal = Vault(httpx.Client(base_url='https://fixture.test', transport=httpx.MockTransport(handler)))
    shared = personal.scoped(SCOPE)
    for path in ['/v1/vault/entries', '/v1/vault/write-operations/request']:
        with pytest.raises(VaultError):
            shared._request('GET', path)
    assert len(seen) == 2
    assert all(r.headers['x-vault-scope'] == SCOPE for r in seen)
    assert personal.scope is None
    with pytest.raises(AttributeError):
        shared.scope = None

@pytest.mark.parametrize('path', ['/v1/vault/otp', '/v1/vault/kms', '/v1/vault/keys', '/v1/vault/host-credentials'])
def test_advanced_shared_operations_rejected_before_transport(path):
    def handler(request):
        pytest.fail('unsupported operation dispatched')
    vault = Vault(httpx.Client(base_url='https://fixture.test', transport=httpx.MockTransport(handler)), scope=SCOPE)
    with pytest.raises(VaultError, match='unsupported'):
        vault._request('POST', path)

def test_malformed_and_ambient_scopes_rejected():
    client = httpx.Client(base_url='https://fixture.test', headers={'X-Vault-Scope': SCOPE})
    with pytest.raises(VaultError, match='Invalid vault scope'):
        Vault(client, scope='team:bad')
    with pytest.raises(VaultError, match='scope-bound'):
        Vault(client)._request('GET', '/v1/vault/entries')
