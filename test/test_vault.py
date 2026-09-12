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
