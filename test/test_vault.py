import json
import subprocess
import httpx
import pytest
from dreamlake.vault import Vault, VaultError, resolve_selector


def test_names():
    assert resolve_selector('token.value', 'ge') == 'ge/token.value'
    assert resolve_selector('/other/token', 'ge') == 'other/token'
    assert resolve_selector('TOKEN=token.value', 'ge') == 'TOKEN=ge/token.value'
    with pytest.raises(VaultError):
        resolve_selector('../token')


def test_read_env_list_and_errors():
    secret = "quote'\n$(printf PWNED)`printf PWNED`\\end"
    entry = dict(name='ge/token', type='string', value=secret, env='TOKEN')
    def handle(request):
        if request.method == 'POST':
            assert json.loads(request.content)['selectors'] == ['ge/token']
        return httpx.Response(200, json={'entries': [entry]})
    vault = Vault(httpx.Client(base_url='http://test', transport=httpx.MockTransport(handle)))
    assert vault.get('token', prefix='ge') == secret
    exports = vault.get('ge/token', to_envs=True)
    assert subprocess.check_output(['sh', '-c', exports + '\nprintf %s "$TOKEN"'], text=True) == secret
    assert 'value' not in vault.list(prefix='ge')[0]
    assert json.loads(vault.get('ge/token', to_json=True)) == secret
    with pytest.raises(VaultError):
        vault.get('ge/token', to_json=True, to_envs=True)


def test_error_body_not_exposed():
    vault = Vault(httpx.Client(base_url='http://test', transport=httpx.MockTransport(lambda r: httpx.Response(403, text='TOP SECRET'))))
    with pytest.raises(VaultError, match='HTTP 403') as error:
        vault.get('ge/token')
    assert 'TOP SECRET' not in str(error.value)


@pytest.mark.parametrize('body', [[], {}, {'entries':[None]}, {'entries':[{'name':'ge/token','type':'string','value':['SECRET']}]}, {'entries':[{'name':'other/token','type':'string','value':'SECRET'}]}])
def test_malformed_response(body):
    vault = Vault(httpx.Client(base_url='http://test', transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))))
    with pytest.raises(VaultError) as error:
        vault.get('ge/token')
    assert 'SECRET' not in str(error.value)


def test_mutation_contract_and_redaction():
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={'entry': {'name':'ge/token','type':'string','revision':1,'value':'SHOULD_NOT_RETURN'}})
    v = Vault(httpx.Client(base_url='http://test', transport=httpx.MockTransport(handle)))
    assert 'value' not in v.add('token', 'SECRET', prefix='ge', env='TOKEN')
    assert json.loads(calls[-1].content)['value'] == 'SECRET'
    assert 'If-Match' not in calls[-1].headers
    v.add('ge/token', {'key':'SECRET'}, if_match=1)
    assert calls[-1].headers['If-Match'] == '1'
    v.show('ge/token')
    assert calls[-1].method == 'GET'
    v.delete('ge/token', if_match=2)
    assert calls[-1].method == 'DELETE' and calls[-1].headers['If-Match'] == '2'
    v.restore('ge/token', if_match=3)
    assert calls[-1].method == 'POST' and calls[-1].headers['If-Match'] == '3'
    before = len(calls)
    for value in [None, [], {}, {'bad':42}]:
        with pytest.raises(VaultError):
            v.add('ge/token', value)
    with pytest.raises(VaultError):
        v.add('ge/token', 'SECRET', if_match=True)
    assert len(calls) == before


def test_access_key_contract_and_repr():
    from dreamlake.vault_keys import duration_seconds
    assert duration_seconds('1h') == 3600
    for value in [True, 0, '1.5h']:
        with pytest.raises(VaultError):
            duration_seconds(value)
    metadata = dict(id='00000000-0000-0000-0000-000000000000',scopes=[],createdAt='2030-01-01T00:00:00Z',expiresAt='2030-01-01T01:00:00Z',maxTtlSeconds=3600,renewable=True,renewUntil=None,oneTime=False,consumedAt=None,revokedAt=None)
    token='dlv1_'+'A'*43
    calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(200,json={'key':metadata,'token':token,'keys':[dict(metadata,token=token)]})
    v=Vault(httpx.Client(base_url='http://test',transport=httpx.MockTransport(handle)))
    issued=v.keys.create('token',prefix='ge',ttl='1h',renewable=True)
    assert issued.token == token and token not in repr(issued)
    assert json.loads(calls[-1].content)['selectors']==['ge/token']
    assert 'token' not in v.keys.list()[0]
    v.keys.renew(issued.key['id'],ttl='30m')
    assert json.loads(calls[-1].content)=={'ttlSeconds':1800}
    v.keys.revoke(issued.key['id'])
    assert calls[-1].method=='DELETE'
    before=len(calls)
    with pytest.raises(VaultError):
        v.keys.create('ge/token',ttl='1h',one_time=True,renewable=True)
    assert len(calls)==before


def test_expiry_omitted_preserved_and_explicit_clear():
    calls=[]
    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json={'entry':{'name':'ge/token','type':'string','revision':1}})
    v=Vault(httpx.Client(base_url='http://test',transport=httpx.MockTransport(handle)))
    v.add('ge/token','SYNTHETIC')
    assert 'expiresAt' not in calls[-1]
    v.add('ge/token','SYNTHETIC',expires_at=None)
    assert calls[-1]['expiresAt'] is None
    v.add('ge/token','SYNTHETIC',expires_at='2030-01-01T00:00:00Z')
    assert calls[-1]['expiresAt']=='2030-01-01T00:00:00Z'
    with pytest.raises(VaultError):
        v.add('ge/token','SYNTHETIC',expires_at='2030-02-30T00:00:00Z')


def test_access_key_replay_cannot_fall_back_to_owner_auth():
    metadata=dict(id='00000000-0000-0000-0000-000000000000',scopes=[],createdAt='2030-01-01T00:00:00Z',expiresAt='2030-01-01T01:00:00Z',maxTtlSeconds=3600,renewable=False,renewUntil=None,oneTime=False,consumedAt=None,revokedAt=None,requestId='request-1')
    def handle(request):
        assert request.headers['Idempotency-Key']=='request-1'
        return httpx.Response(200,json={'key':metadata,'replayed':True,'tokenUnavailable':True})
    v=Vault(httpx.Client(base_url='http://test',transport=httpx.MockTransport(handle)))
    result=v.keys.create('ge/token',ttl='1h',request_id='request-1')
    assert result.replayed
    with pytest.raises(VaultError,match='unavailable'):
        result.token
