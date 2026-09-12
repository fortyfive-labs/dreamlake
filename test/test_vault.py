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
