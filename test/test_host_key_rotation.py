"""Durable client boundaries with real local key files/helper (not SSH auth proof)."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from dreamlake import host_key_rotation as rotation
from dreamlake import host_key_remote
from dreamlake.host_key_journal import PHASES, load_journal
from dreamlake.host_key_state import validate_state, validate_transition
from dreamlake.vault import VaultError, VaultWriteError

ORIGINAL_REMOTE = rotation._remote


class FixtureVault:
    origin = 'https://example.test'
    account = 'alice'

    def __init__(self, old):
        self.entries = {'alice/old': dict(id='old', name='alice/old', type='string', revision=1, value=old)}
        self.binding = dict(id='a'*8+'-'+'a'*4+'-'+'a'*4+'-'+'a'*4+'-'+'a'*12,
                            hostId='a'*24, enrollmentId='b'*24, role='target', endpoint='target', kind='private_key', entryId='old', entryRevision=1)
        self.operation = None
        self.write_calls = 0
        self.confirmations = []
        self.drop_write = False
        self.drop_supersede = False
        self.drop_confirm = False

    def host_credential(self, binding_id):
        assert binding_id == self.binding['id']
        entry = next(e for e in self.entries.values() if e['id'] == self.binding['entryId'])
        return dict(binding=deepcopy(self.binding), entry={k:v for k,v in entry.items() if k != 'value'})

    def get(self, name):
        return self.entries[name]['value']

    def add(self, name, value, request_id):
        self.write_calls += 1
        if name in self.entries:
            assert self.entries[name]['value'] == value
        self.entries[name] = dict(id='new', name=name, type='string', revision=1, value=value)
        if self.drop_write:
            self.drop_write = False
            raise VaultWriteError(request_id)
        return {k:v for k,v in self.entries[name].items() if k != 'value'}

    def write_status(self, request_id):
        return {'entry': {k:v for k,v in self.entries['alice/new'].items() if k != 'value'}}

    def supersede_host_credential(self, **kwargs):
        self.binding.update(entryId=kwargs['replacement_entry_id'], entryRevision=kwargs['replacement_entry_revision'])
        self.operation = dict(operationId=kwargs['operation_id'], bindingId=kwargs['binding_id'],
            expectedEntryId=kwargs['expected_entry_id'], expectedEntryRevision=kwargs['expected_entry_revision'],
            replacementEntryId=kwargs['replacement_entry_id'], replacementEntryRevision=kwargs['replacement_entry_revision'],
            **{k:self.binding[k] for k in ('hostId', 'enrollmentId', 'role', 'endpoint', 'kind')},
            state='cleanup_pending', createdAt=rotation.now())
        if self.drop_supersede:
            self.drop_supersede = False
            raise VaultError('Lost response')
        return self.operation

    def host_credential_operation(self, operation_id):
        assert operation_id == self.operation['operationId']
        return self.operation

    def confirm_host_credential_cleanup(self, operation_id, **kwargs):
        if self.confirmations:
            assert self.confirmations[0] == (operation_id, kwargs)
        self.confirmations.append((operation_id, deepcopy(kwargs)))
        if self.drop_confirm:
            self.drop_confirm = False
            raise VaultError('Lost response')
        return dict(self.operation, state='cleanup_confirmed')


def setup(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    old = tmp_path / 'source'
    subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(old)], check=True)
    public = rotation._public(old)
    home = tmp_path / 'remote-home'
    home.mkdir(mode=0o700)
    ssh = home / '.ssh'
    ssh.mkdir(mode=0o700)
    auth = ssh / 'authorized_keys'
    auth.write_bytes(b'# unrelated exact bytes\xff\n' + public.encode() + b' old-comment\n')
    auth.chmod(0o600)
    vault = FixtureVault(old.read_text())
    monkeypatch.setattr(rotation, '_PinnedVault', lambda v:v)
    calls = []

    def remote(state, selected, action='inspect', expect_denied=False, initial=False):
        calls.append((selected, action))
        selected_public = state['oldPublicKey'] if selected == 'old' else state['newPublicKey']
        authorized = selected_public.encode() in auth.read_bytes()
        if expect_denied:
            if authorized:
                raise ValueError('Still authorized')
            return 'publickey-denied'
        if not authorized:
            raise ValueError('Selected identity unavailable')
        request = dict(action=action, operationId=state['operationId'], oldPublicKey=state['oldPublicKey'], newPublicKey=state['newPublicKey'])
        if not initial:
            request['expectedIdentity'] = state['remoteIdentity']
        return host_key_remote.operate(request, home=home, machine_id='fixture-machine')

    monkeypatch.setattr(rotation, '_remote', remote)
    args = dict(binding_id=vault.binding['id'], operation_file=tmp_path/'operation.json', new_entry='alice/new',
                ssh=dict(host='target.example', user='fixture', port=22, knownHostsFile=str(tmp_path/'known_hosts')))
    return vault, args, auth, calls


def test_full_lifecycle_preserves_unrelated_bytes_and_removes_only_local_keys(tmp_path, monkeypatch):
    vault, args, auth, calls = setup(tmp_path, monkeypatch)
    vault.drop_write = vault.drop_supersede = True
    result = rotation.rotate_host_key(vault, **args)
    assert result['phase'] == 'cleanup_confirmed'
    assert auth.read_bytes().startswith(result['newPublicKey'].encode())
    assert b'# unrelated exact bytes\xff\n' in auth.read_bytes()
    assert result['oldPublicKey'].encode() not in auth.read_bytes()
    assert not Path(result['oldKeyPath']).exists()
    assert not Path(result['newKeyPath']).exists()
    assert 'alice/old' in vault.entries  # shared old entry is never retired.
    assert ('new', 'inspect') in calls and ('new', 'remove_old') in calls
    assert rotation.rotate_host_key(vault, **args) == result
    serialized = args['operation_file'].read_text() + ''.join(p.read_text() for p in Path(str(args['operation_file'])+'.records').glob('*.json'))
    assert 'PRIVATE KEY' not in serialized


@pytest.mark.parametrize('phase', PHASES[1:])
def test_process_interruption_at_every_durable_phase_resumes(tmp_path, monkeypatch, phase):
    vault, args, auth, calls = setup(tmp_path, monkeypatch)
    append = rotation.append_phase
    def interrupted(path, current, following, *rest):
        result = append(path, current, following, *rest)
        if following['phase'] == phase:
            raise KeyboardInterrupt()
        return result
    monkeypatch.setattr(rotation, 'append_phase', interrupted)
    with pytest.raises(KeyboardInterrupt): rotation.rotate_host_key(vault, **args)
    stored = load_journal(args['operation_file'], validate_state, validate_transition)[0]
    assert stored['phase'] == phase
    if PHASES.index(phase) < 5:
        assert stored['oldPublicKey'].encode() in auth.read_bytes()
    monkeypatch.setattr(rotation, 'append_phase', append)
    assert rotation.rotate_host_key(vault, **args)['phase'] == 'cleanup_confirmed'


def test_lost_confirmation_response_retries_exact_durable_attestation(tmp_path, monkeypatch):
    vault, args, auth, calls = setup(tmp_path, monkeypatch)
    vault.drop_confirm = True
    with pytest.raises(VaultError): rotation.rotate_host_key(vault, **args)
    stored = load_journal(args['operation_file'], validate_state, validate_transition)[0]
    assert stored['phase'] == 'old_login_denied_and_new_verified'
    assert Path(stored['newKeyPath']).exists()
    assert rotation.rotate_host_key(vault, **args)['phase'] == 'cleanup_confirmed'
    assert vault.confirmations[0] == vault.confirmations[1]


def test_new_key_probe_failure_preserves_old_and_blocks_supersede(tmp_path, monkeypatch):
    vault, args, auth, calls = setup(tmp_path, monkeypatch)
    remote = rotation._remote
    def failed(state, selected, *a, **kw):
        if selected == 'new': raise ValueError('Probe failed')
        return remote(state, selected, *a, **kw)
    monkeypatch.setattr(rotation, '_remote', failed)
    with pytest.raises(VaultError): rotation.rotate_host_key(vault, **args)
    stored = load_journal(args['operation_file'], validate_state, validate_transition)[0]
    assert stored['oldPublicKey'].encode() in auth.read_bytes()
    assert vault.binding['entryId'] == 'old'
    assert stored['phase'] == 'new_key_installed'


def test_changed_binding_after_supersede_blocks_remote_removal(tmp_path, monkeypatch):
    vault, args, auth, calls = setup(tmp_path, monkeypatch)
    append = rotation.append_phase
    def changed(path, current, following, *rest):
        result = append(path, current, following, *rest)
        if following['phase'] == 'binding_superseded':
            vault.binding['releasedAt'] = rotation.now()
        return result
    monkeypatch.setattr(rotation, 'append_phase', changed)
    with pytest.raises(VaultError): rotation.rotate_host_key(vault, **args)
    stored = load_journal(args['operation_file'], validate_state, validate_transition)[0]
    assert stored['oldPublicKey'].encode() in auth.read_bytes()
    assert ('new', 'remove_old') not in calls


def test_session_and_origin_stay_pinned_after_http_client_config_change():
    import base64
    import httpx
    encoded = base64.urlsafe_b64encode(b'{"sub":"alice"}').decode().rstrip('=')
    calls = []
    with httpx.Client(base_url='https://original.example', headers={'Authorization': 'Bearer x.'+encoded+'.x'},
                      transport=httpx.MockTransport(lambda req: (calls.append(req), httpx.Response(200, json={}))[1])) as http:
        pinned = rotation._PinnedVault(rotation.Vault(http))
        http.base_url = 'https://other.example'
        http.headers['Authorization'] = 'Bearer changed'
        pinned._request('GET', '/probe')
    assert str(calls[0].url) == 'https://original.example/probe'
    assert calls[0].headers['Authorization'] == 'Bearer x.'+encoded+'.x'


def test_same_path_modified_key_cannot_be_used_as_old_denial_evidence(tmp_path, monkeypatch):
    vault, args, auth, calls = setup(tmp_path, monkeypatch)
    append = rotation.append_phase
    def paused(path, current, following, *rest):
        result = append(path, current, following, *rest)
        if following['phase'] == 'old_key_removed':
            raise KeyboardInterrupt()
        return result
    monkeypatch.setattr(rotation, 'append_phase', paused)
    with pytest.raises(KeyboardInterrupt): rotation.rotate_host_key(vault, **args)
    state = load_journal(args['operation_file'], validate_state, validate_transition)[0]
    # Test the actual transport adapter's preflight; it must reject before SSH.
    Path(state['oldKeyPath']).write_bytes(Path(state['newKeyPath']).read_bytes())
    monkeypatch.setattr(rotation, 'run_helper', lambda **_: pytest.fail('must not execute SSH with changed selected key'))
    with pytest.raises(ValueError, match='Selected local key changed'):
        ORIGINAL_REMOTE(state, 'old', expect_denied=True)
