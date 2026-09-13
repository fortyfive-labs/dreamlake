import json
import httpx
import pytest
from dreamlake.vault import Vault, VaultError

URI = 'otpauth://totp/Synthetic?secret=JBSWY3DPEHPK3PXP'


def fixture(tmp_path, handler):
    (tmp_path / 'login.gpg').write_bytes(b'ciphertext')
    return Vault(httpx.Client(base_url='http://127.0.0.1', transport=httpx.MockTransport(handler)))


def test_selected_only_redaction_and_explicit_reveal(tmp_path):
    calls = []
    def handle(req):
        data = json.loads(req.content); calls.append(data)
        return httpx.Response(200, json={'entry': dict(name=data['name'], type='totp', revision=1, value='SERVER_SECRET')})
    v = fixture(tmp_path, handle)
    (tmp_path / 'unselected.gpg').write_bytes(b'NEVER_DECRYPT')
    def decrypt(data):
        assert data == b'ciphertext'
        return 'ADJACENT_PASSWORD\n' + URI
    result = v.import_entries(source='pass-otp', prefix='alice/sub', store=tmp_path, select=['login'], decryptor=decrypt)
    assert result['uploaded'] and calls[0]['name'] == 'alice/sub/login'
    assert json.loads(calls[0]['value'])['seed'] == 'JBSWY3DPEHPK3PXP'
    assert 'ADJACENT_PASSWORD' not in json.dumps(calls)
    assert all(secret not in json.dumps(result) for secret in ['JBSWY3DPEHPK3PXP', 'SERVER_SECRET', 'ADJACENT_PASSWORD'])


@pytest.mark.parametrize('content', ['password-only', URI + '\n' + URI, URI.replace('totp', 'hotp') + '&counter=invalid', 'otpauth://totp/Bad?secret=BAD_SECRET'])
def test_batch_validation_before_writes(tmp_path, content):
    calls = []
    v = fixture(tmp_path, lambda req: calls.append(req))
    result = v.import_entries(source='pass-otp', prefix='alice', store=tmp_path, select=['login'], decryptor=lambda _: content)
    assert not result['uploaded'] and not calls


def test_source_changed_during_decrypt_fails_before_write(tmp_path):
    calls = []
    v = fixture(tmp_path, lambda req: calls.append(req))
    def decrypt(data):
        (tmp_path / 'login.gpg').write_bytes(b'new source')
        return URI
    result = v.import_entries(source='pass-otp', prefix='alice', store=tmp_path, select=['login'], decryptor=decrypt)
    assert not result['uploaded'] and not calls


@pytest.mark.parametrize('status, expected', [(409, 'conflict'), (403, 'denied'), (503, 'unknown'), (302, 'unknown')])
def test_stops_on_failure_without_retry(tmp_path, status, expected):
    calls = []
    def handle(req):
        calls.append(req)
        return httpx.Response(status, text='SECRET_SENTINEL')
    v = fixture(tmp_path, handle)
    (tmp_path / 'later.gpg').write_bytes(b'ciphertext')
    result = v.import_entries(source='pass-otp', prefix='alice', store=tmp_path, select=['login', 'later'], decryptor=lambda _: URI)
    assert [e['status'] for e in result['entries']] == [expected, 'not-attempted']
    assert len(calls) == 1 and 'SECRET_SENTINEL' not in str(result)


def test_unknown_connection_and_symlink(tmp_path):
    calls = []
    def handle(req):
        calls.append(req)
        raise httpx.ReadError('SECRET_SENTINEL')
    v = fixture(tmp_path, handle)
    result = v.import_entries(source='pass-otp', prefix='alice', store=tmp_path, select=['login'], decryptor=lambda _: URI)
    assert result['entries'][0]['status'] == 'unknown' and len(calls) == 1
    (tmp_path / 'link.gpg').symlink_to(tmp_path / 'login.gpg')
    result = v.import_entries(source='pass-otp', prefix='alice', store=tmp_path, select=['link'], decryptor=lambda _: URI)
    assert not result['uploaded'] and len(calls) == 1


@pytest.mark.parametrize('select', [None, [], ['../escape'], ['/absolute'], ['login', 'login'], ['github.com'], [1]])
def test_invalid_selection_never_reads(tmp_path, select):
    v = fixture(tmp_path, lambda _: pytest.fail('network'))
    with pytest.raises(VaultError):
        v.import_entries(source='pass-otp', prefix='alice', store=tmp_path, select=select)


def test_otp_validates_and_redacts_response():
    calls = []
    response = {'code': '123456', 'validUntil': '2099-01-01T00:00:00.000Z', 'seed': 'SECRET_SENTINEL'}
    def handle(req):
        calls.append(json.loads(req.content))
        return httpx.Response(200, json=response)
    v = Vault(httpx.Client(base_url='http://127.0.0.1', transport=httpx.MockTransport(handle)))
    assert v.otp('login', prefix='alice') == '123456'
    assert 'SECRET_SENTINEL' not in v.otp('login', prefix='alice', to_json=True)
    assert calls[0] == {'name': 'alice/login'}
    for invalid in ['12345', 'SECRET_SENTINEL', None]:
        response['code'] = invalid
        with pytest.raises(VaultError, match='Invalid or expired'): v.otp('alice/login')
    response.update(code='123456', validUntil='2000-01-01T00:00:00.000Z')
    with pytest.raises(VaultError): v.otp('alice/login')
    count = len(calls)
    with pytest.raises(VaultError): v.otp('alice/login.seed')
    assert len(calls) == count


def test_interruption_reports_unknown_inflight_and_preserves_remaining(tmp_path):
    def handle(req):
        raise KeyboardInterrupt()
    v = fixture(tmp_path, handle)
    (tmp_path / 'later.gpg').write_bytes(b'ciphertext')
    result = v.import_entries(source='pass-otp', prefix='alice', store=tmp_path, select=['login', 'later'], decryptor=lambda _: URI)
    assert result['cancelled']
    assert [e['status'] for e in result['entries']] == ['unknown', 'not-attempted']


def test_destination_change_during_source_read_fails_without_write(tmp_path):
    calls = []
    v = fixture(tmp_path, lambda req: calls.append(req))
    def decrypt(_):
        v._http.headers['Authorization'] = 'Bearer DIFFERENT_SYNTHETIC_ACCOUNT'
        return URI
    result = v.import_entries(source='pass-otp', prefix='alice', store=tmp_path, select=['login'], decryptor=decrypt)
    assert not result['uploaded'] and not calls
