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

def test_management_metadata_never_exposes_unsolicited_values():
    from dreamlake.vault import _management_metadata
    receipt = dict(operationId='a'*8+'-'+ 'b'*4+'-'+ 'c'*4+'-'+ 'd'*4+'-'+ 'e'*12, state='pending', value='SYNTHETIC_PRIVATE', encrypted={'ciphertext':'SYNTHETIC_PRIVATE'})
    assert 'SYNTHETIC_PRIVATE' not in str(_management_metadata(receipt))
    assert 'SYNTHETIC_PRIVATE' not in str(_management_metadata({'events':[{'action':'secret.copy.preview','value':'SYNTHETIC_PRIVATE'}]}))
    assert _management_metadata({'revision':1,'exportEnabled':False,'value':'SYNTHETIC_PRIVATE'}) == {'revision':1,'exportEnabled':False}

def test_copy_recovery_and_policy_remain_in_original_scope():
    seen=[]
    operation_id='a'*8+'-'+ 'b'*4+'-'+ 'c'*4+'-'+ 'd'*4+'-'+ 'e'*12
    def handler(request):
        seen.append(request)
        if request.url.path.endswith('/policy'):
            return httpx.Response(200,json={'revision':1,'exportEnabled':False})
        return httpx.Response(200,json={'operationId':operation_id,'state':'pending','value':'SYNTHETIC_PRIVATE'})
    vault=Vault(httpx.Client(base_url='https://fixture.test',transport=httpx.MockTransport(handler)),scope=SCOPE)
    vault.secret_copy_preview('org/token',destination_scope=None,destination_name='alice/token',expected_revision=1,request_id='fixture')
    vault.secret_copy(operation_id,action='commit')
    vault.secret_copy_recover('fixture')
    vault.policy(export_enabled=False,expected_revision=0,request_id='policy')
    assert all(request.headers['x-vault-scope']==SCOPE for request in seen)
