import base64
import json
import os
from pathlib import Path
import struct
import pytest
from dreamlake import host_key_remote as remote


def key(byte):
    alg = b'ssh-ed25519'
    return 'ssh-ed25519 ' + base64.b64encode(struct.pack('>I', len(alg)) + alg + struct.pack('>I', 32) + bytes([byte]) * 32).decode()


@pytest.fixture
def setup(tmp_path):
    folder = tmp_path / '.ssh'; folder.mkdir(mode=0o700)
    path = folder / 'authorized_keys'
    original = b'# unrelated\r\nfrom="127.0.0.1",no-agent-forwarding ' + key(1).encode() + b' old-comment\r\n# binary preserved \x80'
    path.write_bytes(original); path.chmod(0o640)
    payload = dict(action='inspect', operationId='same-operation', oldPublicKey=key(1), newPublicKey=key(2))
    def call(action, **changes):
        return remote.operate({**payload, 'action': action, **changes}, home=tmp_path, machine_id='fixture-machine')
    payload['expectedIdentity'] = call('inspect')['identity']
    return tmp_path, path, original, payload, call


def test_additive_replay_exact_removal_and_unrelated_bytes_modes(setup):
    home, path, original, payload, call = setup
    for _ in range(2): assert call('install')['newPresent']
    assert path.read_bytes().endswith(original)
    assert path.stat().st_mode & 0o777 == 0o640
    assert path.read_bytes().startswith(b'from="127.0.0.1",no-agent-forwarding ' + key(2).encode())
    result = call('remove_old'); assert not result['oldPresent'] and result['newPresent']
    assert call('remove_old') == result
    assert b'# unrelated\r\n' in path.read_bytes() and path.read_bytes().endswith(b'# binary preserved \x80')
    with pytest.raises(remote.Refused, match='OPERATION_TERMINAL'): call('install')
    with pytest.raises(remote.Refused): call('remove_new')


def test_rollback_restores_exact_original_bytes_and_cannot_reinstall(setup):
    _, path, original, _, call = setup
    call('install'); call('remove_new'); call('remove_new')
    assert path.read_bytes() == original
    with pytest.raises(remote.Refused, match='OPERATION_TERMINAL'): call('install')


@pytest.mark.parametrize('state,action', [('installed','install'), ('old_removed','remove_old')])
def test_crash_after_authorization_write_reconciles_durable_intent(setup, monkeypatch, state, action):
    _, path, _, _, call = setup
    if action == 'remove_old': call('install')
    original_atomic = remote.atomic
    def fail_after(path, value, *args, **kwargs):
        if path.suffix == '.json' and json.loads(value)['state'] == state:
            raise RuntimeError('simulated process death')
        return original_atomic(path, value, *args, **kwargs)
    monkeypatch.setattr(remote, 'atomic', fail_after)
    with pytest.raises(RuntimeError): call(action)
    monkeypatch.setattr(remote, 'atomic', original_atomic)
    result = call(action)
    assert result['newPresent'] and result['oldPresent'] == (action == 'install')
    assert path.read_bytes().count(key(2).encode()) == 1


def test_missing_old_without_removal_intent_refused(setup):
    _, path, _, _, call = setup
    call('install')
    path.write_bytes(b''.join(x for x in path.read_bytes().splitlines(keepends=True) if key(1).encode() not in x))
    with pytest.raises(remote.Refused, match='OLD_KEY_NOT_FOUND'): call('remove_old')


@pytest.mark.parametrize('change', ['duplicate', 'unsupported', 'identity', 'symlink', 'malformed_journal'])
def test_unsafe_inputs_do_not_change_authorization(setup, change):
    home, path, original, payload, call = setup
    if change == 'duplicate': path.write_bytes(original + b'\n' + key(1).encode())
    if change == 'unsupported': path.write_bytes(b'cert-authority ' + key(1).encode())
    if change == 'identity': payload['expectedIdentity']['machineId'] = 'reused-machine'
    if change == 'symlink':
        real = home / 'preserved'; path.rename(real); path.symlink_to(real)
    if change == 'malformed_journal':
        journal = home / '.ssh/.dreamlake-vault-rotations'; journal.mkdir(mode=0o700)
        f = journal / 'same-operation.json'; f.write_text('{}'); f.chmod(0o600)
    before = path.read_bytes()
    with pytest.raises((remote.Refused, ValueError)): call('install')
    assert path.read_bytes() == before


@pytest.mark.parametrize('change', ['content', 'symlink'])
def test_pre_replace_guard_preserves_external_changes(setup, monkeypatch, change):
    home, path, original, _, call = setup
    reader = remote.read_checked; reads = 0
    victim = home / 'unrelated'; victim.write_bytes(b'UNTOUCHED')
    def race(name, fd, *args, **kwargs):
        nonlocal reads
        if name == 'authorized_keys':
            reads += 1
            if reads == 2:
                if change == 'content': path.write_bytes(original + b'\n# concurrent external edit\n')
                else: path.unlink(); path.symlink_to(victim)
        return reader(name, fd, *args, **kwargs)
    monkeypatch.setattr(remote, 'read_checked', race)
    with pytest.raises(remote.Refused): call('install')
    assert victim.read_bytes() == b'UNTOUCHED'
    if change == 'content': assert path.read_bytes() == original + b'\n# concurrent external edit\n'


@pytest.mark.parametrize('target', ['authorized_keys', '.dreamlake-vault-rotation.lock', 'journal'])
def test_fifo_substitution_is_rejected_without_blocking(setup, target):
    import subprocess
    import sys
    home, path, _, payload, call = setup
    if target == 'journal':
        call('install')
        target_path = home / '.ssh/.dreamlake-vault-rotations/same-operation.json'
    else:
        target_path = home / '.ssh' / target
    target_path.unlink(); os.mkfifo(target_path, 0o600)
    code = '''import json,sys
from dreamlake.host_key_remote import operate
try: operate(json.loads(sys.argv[1]),home=sys.argv[2],machine_id='fixture-machine')
except Exception: print('refused')
else: raise SystemExit(2)
'''
    result = subprocess.run([sys.executable, '-c', code, json.dumps({**payload, 'action':'install'}), str(home)], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0 and result.stdout.strip() == 'refused'
