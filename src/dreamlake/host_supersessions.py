"""Metadata-only replacement recovery; never performs remote SSH changes."""
import re
from .vault import VaultError

FIELDS = ('operationId', 'expectedEntryId', 'expectedEntryRevision', 'replacementEntryId', 'replacementEntryRevision')

def intent(binding_id, data):
    if not isinstance(binding_id, str) or not re.fullmatch(r'[a-f0-9-]{36}', binding_id):
        raise VaultError('Invalid binding identity')
    for name in FIELDS:
        value = data.get(name)
        if name.endswith('Revision'):
            if type(value) is not int or not 1 <= value <= 9007199254740991:
                raise VaultError('Invalid entry revision')
        elif not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
            raise VaultError('Invalid operation or entry identity')
    if data['expectedEntryId'] == data['replacementEntryId']:
        raise VaultError('Replacement requires a distinct entry identity')
    return data

def receipt(result, operation_id, binding_id=None, expected=None):
    op = result.get('operation') if isinstance(result, dict) else None
    if not isinstance(op, dict):
        raise VaultError('Invalid replacement receipt')
    intent(op.get('bindingId'), op)
    if op['operationId'] != operation_id or binding_id is not None and op['bindingId'] != binding_id or expected is not None and any(op.get(k) != v for k, v in expected.items()):
        raise VaultError('Replacement receipt identity mismatch')
    if op.get('state') not in ('cleanup_pending', 'cleanup_confirmed') or not isinstance(op.get('createdAt'), str) or not op['createdAt']:
        raise VaultError('Invalid replacement receipt')
    for key in ('hostId', 'enrollmentId'):
        if not isinstance(op.get(key), str) or not re.fullmatch(r'[0-9a-f]{24}', op[key]):
            raise VaultError('Invalid replacement host identity')
    if op.get('role') not in ('target', 'jump') or op.get('kind') not in ('password', 'private_key') or not isinstance(op.get('endpoint'), str) or not re.fullmatch(r'[A-Za-z0-9_.@:\[\]-]{1,255}', op['endpoint']):
        raise VaultError('Invalid replacement scope')
    result = {k: op[k] for k in (*FIELDS, 'bindingId', 'hostId', 'enrollmentId', 'role', 'endpoint', 'kind', 'state', 'createdAt')}
    if op['state'] == 'cleanup_confirmed':
        confirmation = op.get('confirmation')
        keys = {'bindingId', 'expectedEntryId', 'expectedEntryRevision', 'replacementEntryId', 'replacementEntryRevision', 'verification'}
        if (not isinstance(confirmation, dict) or set(confirmation) != keys
                or any(confirmation[k] != op[k] for k in keys - {'verification'})
                or op.get('verificationSource') != 'client_attestation'
                or not isinstance(op.get('cleanupConfirmedAt'), str)):
            raise VaultError('Invalid cleanup receipt')
        validate_verification(confirmation['verification'])
        result.update(confirmation=confirmation, verificationSource='client_attestation', cleanupConfirmedAt=op['cleanupConfirmedAt'])
    return result


def validate_verification(value):
    from datetime import datetime
    from .host_key_transport import identity
    keys = {'oldFingerprint', 'newFingerprint', 'remoteIdentity', 'newLoginVerifiedAt', 'oldLoginDeniedAt', 'replacementReverifiedAt'}
    try:
        if not isinstance(value, dict) or set(value) != keys:
            raise ValueError()
        for key in ('oldFingerprint', 'newFingerprint'):
            if not isinstance(value[key], str) or not re.fullmatch(r'SHA256:[A-Za-z0-9+/]{43}', value[key]):
                raise ValueError()
        if value['oldFingerprint'] == value['newFingerprint']:
            raise ValueError()
        identity(value['remoteIdentity'])
        times = []
        for key in ('newLoginVerifiedAt', 'oldLoginDeniedAt', 'replacementReverifiedAt'):
            text = value[key]
            if not isinstance(text, str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:00)', text):
                raise ValueError()
            times.append(datetime.fromisoformat(text.replace('Z', '+00:00')))
        if times != sorted(times):
            raise ValueError()
        return value
    except (ValueError, TypeError, KeyError):
        raise VaultError('Invalid cleanup verification') from None
