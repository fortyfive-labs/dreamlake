"""Client-owned SSH key rotation with durable, cross-client phase recovery."""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid
from urllib.parse import urlsplit

from .host_key_journal import PHASES, _directory, append_phase, canonical_json, load_journal, publish_record, read_record
from .host_key_state import fingerprint, validate_state, validate_transition
from .host_key_transport import run_helper, validate_profile
from .vault import Vault, VaultError, VaultWriteError, _entry_name


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


class _PinnedVault(Vault):
    def __init__(self, original):
        super().__init__(original._http)
        url = urlsplit(str(original._http.base_url))
        self.origin = f'{url.scheme}://{url.netloc}'
        if (url.username or url.password or url.query or url.fragment
                or url.scheme not in ('https', 'http')
                or url.scheme == 'http' and url.hostname not in ('localhost', '127.0.0.1', '::1')):
            raise VaultError('Rotation requires a secure API origin')
        self.authorization = original._http.headers.get('Authorization', '')
        if not self.authorization.startswith('Bearer '):
            raise VaultError('Rotation requires an account session')
        try:
            # This pins the existing authenticated session identity only. Server
            # verifies JWT/authentication for every API request independently.
            encoded = self.authorization[7:].split('.')[1]
            self.account = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))['sub']
        except Exception:
            raise VaultError('Rotation requires an account session') from None

    def _request(self, method, path, **kwargs):
        kwargs['headers'] = {**kwargs.get('headers', {}), 'Authorization': self.authorization}
        kwargs['auth'] = None
        return super()._request(method, self.origin + path, **kwargs)


def _public(path):
    read_record(Path(path))
    result = subprocess.run(['ssh-keygen', '-y', '-P', '', '-f', str(path)],
                            capture_output=True, timeout=10)
    if result.returncode != 0 or len(result.stdout) > 8192:
        raise ValueError('Unsupported selected private key')
    # ssh-keygen may retain a trailing comment; journals contain key material only.
    key = ' '.join(result.stdout.decode('ascii').strip().split()[:2])
    fingerprint(key)
    return key


def _current(vault, state, replacement):
    current = vault.host_credential(state['bindingId'])
    binding, entry = current['binding'], current['entry']
    identity_key = 'replacementEntryId' if replacement else 'expectedEntryId'
    revision_key = 'replacementEntryRevision' if replacement else 'expectedEntryRevision'
    name_key = 'replacementEntryName' if replacement else 'oldEntryName'
    if (any(binding[k] != state[k] for k in ('hostId', 'enrollmentId', 'role', 'endpoint', 'kind'))
            or binding.get('releasedAt') is not None
            or binding['entryId'] != state[identity_key] or binding['entryRevision'] != state[revision_key]
            or not entry or entry['id'] != state[identity_key] or entry['revision'] != state[revision_key]
            or entry['name'] != state[name_key] or entry['type'] != 'string'
            or entry.get('deleteAt') or entry.get('expiresAt') and entry['expiresAt'] <= now()):
        raise ValueError('Current binding or credential changed')
    return current


def _remote(state, selected, action='inspect', *, expect_denied=False, initial=False):
    payload = dict(action=action, operationId=state['operationId'], oldPublicKey=state['oldPublicKey'], newPublicKey=state['newPublicKey'])
    if not initial:
        payload['expectedIdentity'] = state['remoteIdentity']
    key = state['newKeyPath'] if selected == 'new' else state['oldKeyPath']
    expected_public = state['newPublicKey'] if selected == 'new' else state['oldPublicKey']
    if _public(key) != expected_public:
        raise ValueError('Selected local key changed')
    config = str(Path(key).parent / f'{selected}-ssh-config')
    return run_helper(profile=state['sshProfile'], selected_key=key,
                      config_file=config, request=payload, expect_denied=expect_denied)


def _prepare(vault, path, binding_id, new_entry, profile):
    _directory(path.parent)
    existing = vault.host_credential(binding_id)
    bound, old = existing['binding'], existing['entry']
    if (bound['kind'] != 'private_key' or bound.get('releasedAt') is not None
            or not old or old['type'] != 'string' or old.get('deleteAt')
            or old.get('expiresAt') and old['expiresAt'] <= now()
            or old['revision'] != bound['entryRevision'] or old['name'] == new_entry):
        raise ValueError('Selected binding is not a current private key')
    operation_id = str(uuid.uuid4())
    folder = path.parent / ('.rotation-' + operation_id)
    folder.mkdir(mode=0o700)
    old_path, new_path = folder / 'old-key', folder / 'new-key'
    retain = False
    try:
        value = vault.get(old['name'])
        if not isinstance(value, str):
            raise ValueError('Private key must be a string')
        publish_record(old_path, value.encode('utf-8'))
        value = None
        result = subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(new_path)],
                                capture_output=True, timeout=10)
        if result.returncode != 0:
            raise ValueError('Key generation failed')
        os.chmod(new_path, 0o600)
        read_record(new_path)  # fsync generated key before referring to it.
        old_public, new_public = _public(old_path), _public(new_path)
        state = dict(schema='dreamlake.host-key-rotation/v1', operationId=operation_id,
            origin=vault.origin, account=vault.account, bindingId=binding_id,
            **{k: bound[k] for k in ('hostId', 'enrollmentId', 'role', 'endpoint', 'kind')},
            expectedEntryId=old['id'], expectedEntryRevision=old['revision'], oldEntryName=old['name'],
            replacementEntryName=new_entry, writeRequestId='rotate_' + operation_id.replace('-', ''),
            sshProfile=deepcopy(profile), oldKeyPath=str(old_path), newKeyPath=str(new_path),
            oldPublicKey=old_public, newPublicKey=new_public,
            oldFingerprint=fingerprint(old_public), newFingerprint=fingerprint(new_public),
            phase='prepared', timestamps={'prepared': now()}, verification={})
        _current(vault, state, False)
        reply = _remote(state, 'old', initial=True)
        if not reply['oldPresent'] or reply['newPresent']:
            raise ValueError('Remote authorization is not uniquely prepared')
        state['remoteIdentity'] = reply['identity']
        validate_state(state)
        retain = True  # Publication failure may still have committed the manifest.
        winner = json.loads(publish_record(path, canonical_json(state)).decode('utf-8'))
        retain = winner.get('operationId') == operation_id
        return validate_state(winner)
    finally:
        # Only this caller's fresh random directory, before any remote mutation.
        # Crash leftovers are intentionally retained for explicit operator cleanup.
        if not retain:
            shutil.rmtree(folder)


def _remove_local_keys(state):
    for key, public in (('oldKeyPath', 'oldPublicKey'), ('newKeyPath', 'newPublicKey')):
        path = Path(state[key])
        try:
            before = path.lstat()
            if _public(path) != state[public]:
                raise ValueError('Local credential changed')
            current = path.lstat()
            if (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
                raise ValueError('Local credential replaced')
            path.unlink()
        except FileNotFoundError:
            pass  # Concurrent completed callers may already clean the same file.


def resume(vault, path):
    """Internal orchestration; every persisted boundary is independently resumable."""
    current = load_journal(path, validate_state, validate_transition)
    while current[0]['phase'] != 'cleanup_confirmed':
        state = current[0]
        index = PHASES.index(state['phase'])
        next_state = deepcopy(state)
        if index == 0:
            _current(vault, state, False)
            if _public(state['newKeyPath']) != state['newPublicKey']:
                raise ValueError('Replacement key changed')
            try:
                entry = vault.add(state['replacementEntryName'], read_record(Path(state['newKeyPath'])).decode('utf-8'), request_id=state['writeRequestId'])
            except VaultWriteError:
                entry = vault.write_status(request_id=state['writeRequestId'])['entry']
            if entry['name'] != state['replacementEntryName'] or entry['id'] == state['expectedEntryId'] or entry['type'] != 'string':
                raise ValueError('Replacement write identity mismatch')
            next_state.update(replacementEntryId=entry['id'], replacementEntryRevision=entry['revision'])
        elif index == 1:
            _current(vault, state, False)
            reply = _remote(state, 'old', 'install')
            if not reply['oldPresent'] or not reply['newPresent']:
                raise ValueError('Additive installation unconfirmed')
        elif index == 2:
            reply = _remote(state, 'new')
            if not reply['oldPresent'] or not reply['newPresent']:
                raise ValueError('Replacement authentication unconfirmed')
            next_state['verification']['newLoginVerifiedAt'] = now()
        elif index == 3:
            if not _remote(state, 'new')['newPresent']:
                raise ValueError('Replacement re-verification unconfirmed')
            arguments = dict(binding_id=state['bindingId'], operation_id=state['operationId'], expected_entry_id=state['expectedEntryId'], expected_entry_revision=state['expectedEntryRevision'], replacement_entry_id=state['replacementEntryId'], replacement_entry_revision=state['replacementEntryRevision'])
            try:
                operation = vault.supersede_host_credential(**arguments)
            except VaultError:
                operation = vault.host_credential_operation(state['operationId'])
            if operation['state'] != 'cleanup_pending':
                raise ValueError('Unexpected replacement lifecycle')
            next_state['receipt'] = operation
        elif index == 4:
            _current(vault, state, True)
            if not _remote(state, 'new')['newPresent']:
                raise ValueError('Replacement re-verification unconfirmed')
            reply = _remote(state, 'new', 'remove_old')
            if reply['oldPresent'] or not reply['newPresent']:
                raise ValueError('Old authorization removal unconfirmed')
        elif index == 5:
            _current(vault, state, True)
            _remote(state, 'old', expect_denied=True)
            next_state['verification']['oldLoginDeniedAt'] = now()
            reply = _remote(state, 'new')
            if reply['oldPresent'] or not reply['newPresent']:
                raise ValueError('Replacement re-verification unconfirmed')
            next_state['verification']['replacementReverifiedAt'] = now()
        elif index == 6:
            vault.confirm_host_credential_cleanup(state['operationId'], binding_id=state['bindingId'],
                expected_entry_id=state['expectedEntryId'], expected_entry_revision=state['expectedEntryRevision'],
                replacement_entry_id=state['replacementEntryId'], replacement_entry_revision=state['replacementEntryRevision'],
                verification={**state['verification'], 'oldFingerprint': state['oldFingerprint'],
                              'newFingerprint': state['newFingerprint'], 'remoteIdentity': state['remoteIdentity']})
        next_state['phase'] = PHASES[index+1]
        next_state['timestamps'][next_state['phase']] = now()
        current = append_phase(path, current, next_state, validate_state, validate_transition)
    _remove_local_keys(current[0])
    return current[0]


def rotate_host_key(vault, *, binding_id, operation_file, new_entry, ssh):
    """Never prompts; caller explicitly authorizes this selected remote key change."""
    try:
        pinned = _PinnedVault(vault)
        path = Path(operation_file).absolute()
        _directory(path.parent)
        profile = validate_profile(deepcopy(ssh))
        new_entry = _entry_name(new_entry)
        try:
            state = load_journal(path, validate_state, validate_transition)[0]
        except FileNotFoundError:
            state = _prepare(pinned, path, binding_id, new_entry, profile)
        candidate = path.parent / ('.rotation-' + state['operationId'])
        if state['oldKeyPath'] != str(candidate / 'old-key') or state['newKeyPath'] != str(candidate / 'new-key'):
            raise ValueError('Rotation key paths do not match owned operation directory')
        if (state['origin'] != pinned.origin or state['account'] != pinned.account
                or state['bindingId'] != binding_id or state['replacementEntryName'] != new_entry
                or state['sshProfile'] != profile):
            raise ValueError('Rotation invocation does not match persisted intent')
        return resume(pinned, path)
    except Exception:
        raise VaultError('Key rotation incomplete; retain the original operation file and resume with the same arguments') from None
