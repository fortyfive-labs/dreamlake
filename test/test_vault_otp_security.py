"""F1 deterministic regressions; synthetic secrets and MockTransport only."""
import json

import httpx
import pytest

import dreamlake.pass_import as pi
from dreamlake.vault import Vault, VaultError

URI = 'otpauth://totp/Synthetic?secret=JBSWY3DPEHPK3PXP'


def setup_import(tmp_path, client, select=None, decryptor=None):
    select = select or ['login']
    for name in select:
        (tmp_path / (name + '.gpg')).write_bytes(b'SYNTHETIC_CIPHERTEXT')
    return Vault(client), dict(source='pass-otp', store=tmp_path.resolve(),
                              prefix='alice', select=select,
                              decryptor=decryptor or (lambda _: URI))


def success(request):
    body = json.loads(request.content)
    assert json.loads(body['value'])['seed'] == 'JBSWY3DPEHPK3PXP'
    return httpx.Response(200, json={'entry': {
        'name': body['name'], 'type': 'totp', 'revision': 1}})


def test_final_read_mutation_cannot_change_prepared_dispatch(tmp_path, monkeypatch):
    """Same interleaving as reviewer python_probes.py, plus auth/cookie/hooks."""
    calls, unsafe_calls = [], []

    def handle(request):
        calls.append(request)
        return success(request)

    def unsafe(request):
        unsafe_calls.append(request)
        request.url = httpx.URL('https://changed.invalid/leak')
        request.headers['Authorization'] = 'Bearer SYNTHETIC_B'
        return request

    with httpx.Client(base_url='https://original.invalid',
                      headers={'Authorization': 'Bearer SYNTHETIC_A'},
                      cookies={'session': 'SYNTHETIC_COOKIE_A'},
                      transport=httpx.MockTransport(handle)) as client:
        vault, options = setup_import(tmp_path, client)
        original = pi.read_pass_ciphertext
        reads = 0

        def raced_read(*args):
            nonlocal reads
            reads += 1
            data = original(*args)
            if reads == 3:  # after the last binding comparison
                client.base_url = 'https://changed.invalid'
                client.headers['Authorization'] = 'Bearer SYNTHETIC_B'
                client.cookies.set('session', 'SYNTHETIC_COOKIE_B')
                client.auth = unsafe
                client.event_hooks['request'].append(unsafe)
                client.event_hooks['response'].append(lambda _: unsafe_calls.append('response'))
                vault._http = httpx.Client(base_url='https://replacement.invalid',
                                          transport=httpx.MockTransport(unsafe))
            return data

        monkeypatch.setattr(pi, 'read_pass_ciphertext', raced_read)
        try:
            result = vault.import_entries(**options)
        finally:
            if vault._http is not client:
                vault._http.close()
        assert result['uploaded'] and reads == 3
        assert len(calls) == 1 and not unsafe_calls
        assert str(calls[0].url) == 'https://original.invalid/v1/vault/entry'
        assert calls[0].headers['Authorization'] == 'Bearer SYNTHETIC_A'
        assert calls[0].headers['cookie'] == 'session=SYNTHETIC_COOKIE_A'
        assert calls[0].headers['host'] == 'original.invalid'


@pytest.mark.parametrize('setting', ['basic-auth', 'callable-auth', 'request-hook', 'response-hook'])
def test_unpinnable_configuration_fails_before_source_read(tmp_path, monkeypatch, setting):
    effects = []
    with httpx.Client(base_url='https://original.invalid',
                      transport=httpx.MockTransport(lambda req: effects.append(req))) as client:
        if setting == 'basic-auth':
            client.auth = ('synthetic', 'SYNTHETIC_PASSWORD')
        elif setting == 'callable-auth':
            client.auth = lambda request: effects.append(request)
        else:
            client.event_hooks[setting.split('-')[0]].append(lambda item: effects.append(item))
        vault, options = setup_import(tmp_path, client, decryptor=lambda _: effects.append('decrypt'))
        monkeypatch.setattr(pi, 'read_pass_ciphertext', lambda *args: effects.append('read'))
        with pytest.raises(VaultError, match='header authentication and no client hooks'):
            vault.import_entries(**options)
        assert not effects


def test_response_cookies_do_not_rebind_later_upload_and_transport_stays_open(tmp_path):
    calls = []

    class Transport(httpx.MockTransport):
        closed = False

        def close(self):
            self.closed = True

    def handle(request):
        calls.append(request)
        response = success(request)
        response.headers['set-cookie'] = 'session=SYNTHETIC_CHANGED; Path=/'
        return response

    transport = Transport(handle)
    with httpx.Client(base_url='https://original.invalid',
                      cookies={'session': 'SYNTHETIC_ORIGINAL'},
                      transport=transport) as client:
        vault, options = setup_import(tmp_path, client, select=['one', 'two'])
        assert vault.import_entries(**options)['uploaded']
        assert len(calls) == 2
        assert all(r.headers['cookie'] == 'session=SYNTHETIC_ORIGINAL' for r in calls)
        assert client.cookies['session'] == 'SYNTHETIC_ORIGINAL'
        assert not transport.closed
    assert transport.closed


def test_redirect_is_not_followed(tmp_path):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(307, headers={'location': 'https://changed.invalid'})

    with httpx.Client(base_url='https://original.invalid', follow_redirects=True,
                      transport=httpx.MockTransport(handle)) as client:
        vault, options = setup_import(tmp_path, client)
        result = vault.import_entries(**options)
        assert not result['uploaded'] and result['entries'][0]['status'] == 'unknown'
        assert len(calls) == 1


def test_cookie_scope_and_duplicate_names_preserved(tmp_path):
    calls = []

    def handle(request):
        calls.append(request)
        return success(request)

    with httpx.Client(base_url='https://original.invalid', transport=httpx.MockTransport(handle)) as client:
        client.cookies.set('session', 'SYNTHETIC_RIGHT', domain='original.invalid', path='/v1')
        client.cookies.set('session', 'SYNTHETIC_WRONG_HOST', domain='changed.invalid', path='/')
        client.cookies.set('session', 'SYNTHETIC_WRONG_PATH', domain='original.invalid', path='/other')
        vault, options = setup_import(tmp_path, client)
        assert vault.import_entries(**options)['uploaded']
        assert calls[0].headers['cookie'] == 'session=SYNTHETIC_RIGHT'
