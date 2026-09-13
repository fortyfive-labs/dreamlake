import json
import tempfile
import shutil
from pathlib import Path
import subprocess
import pytest
from dreamlake.client import RemoteClient
from dreamlake.pass_store import PassStore


def test_real_gpg_preview(tmp_path):
    root = Path(tempfile.mkdtemp(prefix="vault-pass-test-", dir="/tmp")).resolve()
    home, store = root / 'gnupg', root / 'store'
    home.mkdir(mode=0o700)
    store.mkdir()
    base = ['gpg', '--no-options', '--homedir', str(home), '--batch', '--no-tty']
    try:
        subprocess.run(base + ['--pinentry-mode', 'loopback', '--passphrase', '', '--quick-generate-key', 'vault-test@example.test', 'rsa2048', 'encr', '0'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        uri = 'otpauth://totp/Test?secret=JBSWY3DPEHPK3PXP'
        for name in ['login', 'github.com']:
            subprocess.run(base + ['--trust-model', 'always', '--recipient', 'vault-test@example.test', '--output', str(store / (name + '.gpg')), '--encrypt'], input=('ADJACENT_PASSWORD\n' + uri).encode(), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for name, data in [('oversized', b'A' * 1048577), ('invalid-utf8', bytes([255]))]:
            subprocess.run(base + ['--trust-model', 'always', '--recipient', 'vault-test@example.test', '--output', str(store / (name + '.gpg')), '--encrypt'], input=data, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        (store / 'broken.gpg').write_bytes(b'NOT_CIPHERTEXT')
        # Public SDK property; local preview must never contact this URL.
        client = RemoteClient('http://127.0.0.1:1', api_key='synthetic-unused')
        result = client.vault.pass_store.sync(store=store, gpg_home=home, prefix='ge', otp=True, dry_run=True)
        assert (result['ready'], result['mappingRequired'], result['failed']) == (1, 1, 3)
        assert result['uploaded'] is False
        assert 'JBSWY3DPEHPK3PXP' not in json.dumps(result)
        assert 'ADJACENT_PASSWORD' not in json.dumps(result)
        with pytest.raises(ValueError, match='no upload'):
            PassStore().sync(store=store, otp=True)
        (store / 'escape.gpg').symlink_to(store / 'login.gpg')
        with pytest.raises(ValueError, match='Symlinks'):
            PassStore().sync(store=store, otp=True, dry_run=True, gpg_home=home)
    finally:
        subprocess.run(['gpgconf', '--homedir', str(home), '--kill', 'gpg-agent'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # gpg-agent can unlink its sockets concurrently after --kill returns.
        def cleanup_error(_function, _path, exc_info):
            if not isinstance(exc_info[1], FileNotFoundError):
                raise exc_info[1]
        shutil.rmtree(root, onerror=cleanup_error)


def test_explicit_decryptor_errors_redacted(tmp_path):
    store = tmp_path.resolve()
    (store / 'one.gpg').write_bytes(b'ciphertext')
    def failure(data):
        raise RuntimeError('SECRET_DIAGNOSTIC')
    result = PassStore().sync(store=store, otp=True, dry_run=True, decryptor=failure)
    assert result['failed'] == 1
    assert 'SECRET_DIAGNOSTIC' not in json.dumps(result)
