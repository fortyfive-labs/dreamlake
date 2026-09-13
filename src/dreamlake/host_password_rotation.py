"""Metadata-only password reservation and explicit owner recovery reads; no SSH."""
from copy import deepcopy
from datetime import datetime
import re
from .vault import VaultError

INTENT = ('expectedEntryId', 'expectedEntryRevision', 'replacementEntryId',
          'replacementEntryRevision', 'remoteIdentity', 'recoveryKeyFingerprint',
          'recoveryVerifiedAt')
PROOF = ('method', 'remoteIdentity', 'recoveryKeyFingerprint', 'newLoginVerifiedAt',
         'oldLoginDeniedAt', 'replacementReverifiedAt', 'recoveryReverifiedAt')


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise VaultError('Invalid password rotation identifier')
    return value


def exact(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise VaultError('Invalid password rotation metadata')


def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z', value):
        raise VaultError('Invalid password rotation timestamp')
    try:
        datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise VaultError('Invalid password rotation timestamp') from None


def identity(value):
    exact(value, ('user', 'uid', 'home', 'machineId', 'homeDevice', 'homeInode'))
    if not isinstance(value['user'], str) or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,63}', value['user']):
        raise VaultError('Invalid remote account identity')
    if not isinstance(value['home'], str) or not value['home'].startswith('/') or len(value['home']) > 4096 or any(c in value['home'] for c in '\0\r\n'):
        raise VaultError('Invalid remote account identity')
    if not isinstance(value['machineId'], str) or not re.fullmatch(r'[a-fA-F0-9]{32}', value['machineId']):
        raise VaultError('Invalid remote account identity')
    for key in ('uid', 'homeDevice', 'homeInode'):
        if type(value[key]) is not int or not 0 <= value[key] <= 9007199254740991:
            raise VaultError('Invalid remote account identity')


def fingerprint(value):
    if not isinstance(value, str) or not re.fullmatch(r'SHA256:[A-Za-z0-9+/]{43}', value):
        raise VaultError('Invalid recovery key fingerprint')


def validate_intent(value):
    value = deepcopy(value)
    exact(value, INTENT)
    for key in ('expectedEntryId', 'replacementEntryId'):
        identifier(value[key])
    for key in ('expectedEntryRevision', 'replacementEntryRevision'):
        if type(value[key]) is not int or not 1 <= value[key] <= 9007199254740991:
            raise VaultError('Invalid password entry revision')
    if value['expectedEntryId'] == value['replacementEntryId']:
        raise VaultError('Replacement requires a distinct entry')
    identity(value['remoteIdentity'])
    fingerprint(value['recoveryKeyFingerprint'])
    timestamp(value['recoveryVerifiedAt'])
    return value


def validate_proof(value):
    value = deepcopy(value)
    exact(value, PROOF)
    if value['method'] != 'ssh_password':
        raise VaultError('Expected password authentication attestation')
    identity(value['remoteIdentity'])
    fingerprint(value['recoveryKeyFingerprint'])
    times = [value[key] for key in PROOF[3:]]
    for item in times:
        timestamp(item)
    if times != sorted(times):
        raise VaultError('Password proof timestamps are out of order')
    return value


def receipt(data, operation_id, binding_id=None, expected=None):
    op = data.get('operation') if isinstance(data, dict) else None
    required = set(INTENT) | {'operationId', 'bindingId', 'hostId', 'enrollmentId', 'role', 'endpoint', 'state', 'createdAt', 'verificationSource'}
    optional = {'startedAt', 'terminalAt', 'purgeAt', 'verification', 'snapshotsPurgedAt'}
    if not isinstance(op, dict) or not required <= set(op) or set(op) - required - optional:
        raise VaultError('Invalid password rotation response')
    validate_intent({key: op[key] for key in INTENT})
    if op['operationId'] != operation_id or binding_id is not None and op['bindingId'] != binding_id or expected is not None and any(op.get(key) != item for key, item in expected.items()):
        raise VaultError('Password rotation receipt identity mismatch')
    if not isinstance(op['bindingId'], str) or not re.fullmatch(r'[a-f0-9-]{36}', op['bindingId']):
        raise VaultError('Invalid password binding identity')
    for key in ('hostId', 'enrollmentId'):
        if not isinstance(op[key], str) or not re.fullmatch(r'[a-f0-9]{24}', op[key]):
            raise VaultError('Invalid password host identity')
    if op['role'] not in ('target', 'jump') or op['state'] not in ('reserved', 'mutation_pending', 'confirmed', 'cancelled') or op['verificationSource'] != 'client_attestation' or not isinstance(op['endpoint'], str) or not re.fullmatch(r'[A-Za-z0-9_.@:\[\]-]{1,255}', op['endpoint']):
        raise VaultError('Invalid password rotation scope')
    for key in ('createdAt', 'startedAt', 'terminalAt', 'purgeAt', 'snapshotsPurgedAt'):
        if key in op:
            timestamp(op[key])
    if op['state'] in ('mutation_pending', 'confirmed') and 'startedAt' not in op:
        raise VaultError('Missing password mutation start receipt')
    if op['state'] in ('confirmed', 'cancelled') and not {'terminalAt', 'purgeAt'} <= set(op):
        raise VaultError('Missing password terminal receipt')
    if (op['state'] == 'reserved' and set(op) & optional
            or op['state'] == 'mutation_pending' and set(op) & (optional - {'startedAt'})
            or op['state'] == 'cancelled' and set(op) & {'startedAt', 'verification'}):
        raise VaultError('Unexpected password lifecycle metadata')
    if op['state'] == 'confirmed':
        proof = validate_proof(op.get('verification'))
        if proof['remoteIdentity'] != op['remoteIdentity'] or proof['recoveryKeyFingerprint'] != op['recoveryKeyFingerprint'] or proof['newLoginVerifiedAt'] < op['startedAt']:
            raise VaultError('Password confirmation scope mismatch')
    return deepcopy(op)


def reserve(vault, *, binding_id, operation_id, intent):
    identifier(operation_id)
    if not isinstance(binding_id, str) or not re.fullmatch(r'[a-f0-9-]{36}', binding_id):
        raise VaultError('Invalid password binding identity')
    data = validate_intent(intent)
    return receipt(vault._request('PUT', f'/v1/vault/host-credentials/{binding_id}/password-rotations/{operation_id}', json=data), operation_id, binding_id, data)


def get(vault, operation_id):
    identifier(operation_id)
    return receipt(vault._request('GET', f'/v1/vault/host-password-rotations/{operation_id}'), operation_id)


def transition(vault, operation_id, action, proof=None):
    identifier(operation_id)
    data = validate_proof(proof) if action == 'confirm' else {}
    result = receipt(vault._request('POST', f'/v1/vault/host-password-rotations/{operation_id}/{action}', json=data), operation_id)
    if result['state'] != {'start': 'mutation_pending', 'cancel': 'cancelled', 'confirm': 'confirmed'}[action]:
        raise VaultError('Unexpected password rotation state')
    if action == 'confirm' and result.get('verification') != data:
        raise VaultError('Password confirmation receipt mismatch')
    return result


def read(vault, operation_id, slot):
    identifier(operation_id)
    if slot not in ('old', 'new'):
        raise VaultError('Select exactly one recovery snapshot')
    # Pin origin and authorization across metadata lookup and selected-secret read.
    # Concurrent caller changes to its shared HTTP client cannot redirect delivery.
    from urllib.parse import urlsplit
    from .vault import Vault
    original = vault
    url = urlsplit(str(original._http.base_url))
    authorization = original._http.headers.get('Authorization', '')
    if (url.username or url.password or url.query or url.fragment
            or url.scheme not in ('https', 'http')
            or url.scheme == 'http' and url.hostname not in ('localhost', '127.0.0.1', '::1')
            or not authorization.startswith('Bearer ')):
        raise VaultError('Password recovery requires a secure authenticated origin')
    origin = f'{url.scheme}://{url.netloc}'
    class Pinned(Vault):
        def _request(self, method, path, **kwargs):
            kwargs['headers'] = {**kwargs.get('headers', {}), 'Authorization': authorization}
            kwargs['auth'] = None
            return super()._request(method, origin+path, **kwargs)
    vault = Pinned(original._http)
    op = get(vault, operation_id)
    prefix = 'expected' if slot == 'old' else 'replacement'
    if op['state'] not in ('reserved', 'mutation_pending'):
        raise VaultError('Password recovery is no longer pending')
    data = vault._request('POST', f'/v1/vault/host-password-rotations/{operation_id}/read', json={'slot': slot})
    exact(data, ('operationId', 'slot', 'entryId', 'entryRevision', 'value'))
    if data['operationId'] != operation_id or data['slot'] != slot or data['entryId'] != op[prefix+'EntryId'] or data['entryRevision'] != op[prefix+'EntryRevision'] or not isinstance(data['value'], str):
        raise VaultError('Password snapshot identity mismatch')
    return data['value']
