"""Credential warnings describe storage, never ordinary reads or logout."""
import json
from pathlib import Path

import httpx

from dreamlake.auth import Auth
from dreamlake.auth import token_storage
from dreamlake.auth.exceptions import StorageError


def test_plaintext_reads_and_deletes_are_quiet_but_storage_warns(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(token_storage.PlaintextFileStorage, '_warning_shown', False)
    (tmp_path / 'tokens.json').write_text(json.dumps({'existing': 'secret', 'retained': 'other'}))
    storage = token_storage.PlaintextFileStorage(tmp_path)
    assert storage.load('existing') == 'secret'
    storage.delete('existing')
    storage.delete('missing')
    assert storage.load('existing') is None
    assert storage.load('retained') == 'other'
    assert capsys.readouterr().out == ''
    assert not token_storage.PlaintextFileStorage._warning_shown
    storage.store('new', 'replacement-secret')
    output = capsys.readouterr().out
    assert 'Storing tokens in plaintext' in output
    assert 'replacement-secret' not in output
    assert storage.load('new') == 'replacement-secret'
    storage.store('another', 'another-secret')
    assert capsys.readouterr().out == ''


def test_auth_logout_plaintext_fallback_deletes_credentials_without_warning(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(token_storage.PlaintextFileStorage, '_warning_shown', False)
    for key in ('DREAMLAKE_API_KEY', 'DREAMLAKE_REMOTE', 'DREAMLAKE_IDENTITY'):
        monkeypatch.delenv(key, raising=False)
    def unavailable(*args):
        raise StorageError('optional secure backend not installed')
    monkeypatch.setattr(token_storage, 'KeyringStorage', unavailable)
    monkeypatch.setattr(token_storage, 'EncryptedFileStorage', unavailable)
    auth = Auth(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
        'id': 'account', 'namespace': {'slug': 'personal'}})))
    auth.login('private-token')
    auth.logout()
    assert json.loads((tmp_path / '.dreamlake' / 'tokens.json').read_text()) == {}
    assert auth.status()['authenticated'] is False
    assert capsys.readouterr().out == ''
    assert not token_storage.PlaintextFileStorage._warning_shown


def test_logout_legacy_cleanup_does_not_delete_successor_projection(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(token_storage.PlaintextFileStorage, '_warning_shown', False)
    for key in ('DREAMLAKE_API_KEY', 'DREAMLAKE_REMOTE', 'DREAMLAKE_IDENTITY'):
        monkeypatch.delenv(key, raising=False)
    def unavailable(*args):
        raise StorageError('optional secure backend not installed')
    monkeypatch.setattr(token_storage, 'KeyringStorage', unavailable)
    monkeypatch.setattr(token_storage, 'EncryptedFileStorage', unavailable)
    auth = Auth(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
        'id': 'account', 'namespace': {'slug': 'personal'}})))
    auth.login('first-token', profile='first')
    auth.login('second-token', profile='second')
    auth.logout()
    assert auth.token() == 'first-token'
    assert auth.status()['profile'] == 'first'
    assert capsys.readouterr().out == ''
