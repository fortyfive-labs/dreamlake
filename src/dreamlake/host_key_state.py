"""Allowlisted rotation snapshots and immutable evidence transitions."""
import base64
from datetime import datetime
import hashlib
from pathlib import Path
import re
from urllib.parse import urlsplit

from .host_key_journal import PHASES
from .host_key_transport import identity, validate_profile, _request
from .host_supersessions import receipt
from .vault import _host_binding

IMMUTABLE = ('schema', 'operationId', 'origin', 'account', 'bindingId', 'hostId',
             'enrollmentId', 'role', 'endpoint', 'kind', 'expectedEntryId',
             'expectedEntryRevision', 'oldEntryName', 'replacementEntryName',
             'writeRequestId', 'sshProfile', 'oldKeyPath', 'newKeyPath',
             'oldPublicKey', 'newPublicKey', 'oldFingerprint', 'newFingerprint', 'remoteIdentity')
OPTIONAL = ('replacementEntryId', 'replacementEntryRevision', 'receipt')


def fingerprint(key):
    kind, encoded = key.split(' ')
    raw = base64.b64decode(encoded, validate=True)
    if base64.b64encode(raw).decode() != encoded or len(raw) < 8:
        raise ValueError('Invalid public key encoding')
    size = int.from_bytes(raw[:4], 'big')
    if size > len(raw) - 8 or raw[4:4+size].decode('ascii') != kind:
        raise ValueError('Invalid public key type')
    return 'SHA256:' + base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip('=')


def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z', value):
        raise ValueError('Invalid rotation timestamp')
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def validate_state(value):
    if not isinstance(value, dict) or set(value) - set(IMMUTABLE + OPTIONAL + ('phase', 'timestamps', 'verification')) or set(IMMUTABLE) - set(value):
        raise ValueError('Unexpected rotation metadata')
    v = value
    if (v['schema'] != 'dreamlake.host-key-rotation/v1' or v['kind'] != 'private_key'
            or not isinstance(v['bindingId'], str) or not re.fullmatch(r'[0-9a-f-]{36}', v['bindingId'])
            or not isinstance(v['account'], str) or not re.fullmatch(r'[A-Za-z0-9_.:@|\-]{1,255}', v['account'])):
        raise ValueError('Invalid rotation identity')
    url = urlsplit(v['origin'])
    if (url.username or url.password or url.path or url.query or url.fragment
            or url.scheme not in ('https', 'http')
            or url.scheme == 'http' and url.hostname not in ('localhost', '127.0.0.1', '::1')):
        raise ValueError('Invalid rotation origin')
    _host_binding({**v, 'entryId': v['expectedEntryId'], 'entryRevision': v['expectedEntryRevision']})
    for name in (v['oldEntryName'], v['replacementEntryName']):
        if (not isinstance(name, str) or len(name) > 512
                or not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+', name)
                or any(p in ('.', '..') for p in name.split('/'))):
            raise ValueError('Invalid rotation entry name')
    if (v['oldEntryName'] == v['replacementEntryName'] or v['oldKeyPath'] == v['newKeyPath']
            or v['oldPublicKey'] == v['newPublicKey']
            or v['writeRequestId'] != 'rotate_' + v['operationId'].replace('-', '')):
        raise ValueError('Invalid replacement intent')
    for path in (v['oldKeyPath'], v['newKeyPath']):
        if not isinstance(path, str) or not Path(path).is_absolute() or re.search(r'[\r\n\x00%$]', path):
            raise ValueError('Invalid rotation key path')
    validate_profile(v['sshProfile'])
    identity(v['remoteIdentity'])
    _request(dict(action='inspect', operationId=v['operationId'], oldPublicKey=v['oldPublicKey'], newPublicKey=v['newPublicKey']))
    if v['oldFingerprint'] != fingerprint(v['oldPublicKey']) or v['newFingerprint'] != fingerprint(v['newPublicKey']):
        raise ValueError('Fingerprint mismatch')
    index = PHASES.index(v['phase'])
    if not isinstance(v['timestamps'], dict) or set(v['timestamps']) != set(PHASES[:index+1]):
        raise ValueError('Invalid phase timestamps')
    times = [timestamp(v['timestamps'][phase]) for phase in PHASES[:index+1]]
    if times != sorted(times):
        raise ValueError('Invalid phase timestamp order')
    required = {'newLoginVerifiedAt', 'oldLoginDeniedAt', 'replacementReverifiedAt'} if index >= 6 else {'newLoginVerifiedAt'} if index >= 3 else set()
    if not isinstance(v['verification'], dict) or set(v['verification']) != required:
        raise ValueError('Invalid verification metadata')
    for text in v['verification'].values():
        timestamp(text)
    if index >= 6 and timestamp(v['verification']['oldLoginDeniedAt']) > timestamp(v['verification']['replacementReverifiedAt']):
        raise ValueError('Invalid verification order')
    if index >= 1:
        if (not isinstance(v.get('replacementEntryId'), str) or v['replacementEntryId'] == v['expectedEntryId']
                or type(v.get('replacementEntryRevision')) is not int or not 1 <= v['replacementEntryRevision'] <= 9007199254740991):
            raise ValueError('Invalid replacement receipt')
    elif any(key in v for key in ('replacementEntryId', 'replacementEntryRevision')):
        raise ValueError('Premature replacement receipt')
    if index >= 4:
        expected = {k: v[k] for k in ('operationId', 'expectedEntryId', 'expectedEntryRevision', 'replacementEntryId', 'replacementEntryRevision')}
        clean = receipt({'operation': v.get('receipt')}, v['operationId'], v['bindingId'], expected)
        if clean != v['receipt'] or clean['state'] != 'cleanup_pending' or any(clean[k] != v[k] for k in ('hostId', 'enrollmentId', 'role', 'endpoint', 'kind')):
            raise ValueError('Receipt scope mismatch')
    elif 'receipt' in v:
        raise ValueError('Premature replacement receipt')
    return v


def validate_transition(previous, following):
    if PHASES.index(following['phase']) != PHASES.index(previous['phase']) + 1:
        raise ValueError('Invalid rotation transition')
    if any(previous[k] != following[k] for k in IMMUTABLE):
        raise ValueError('Rotation intent changed')
    if any(k in previous and previous[k] != following.get(k) for k in OPTIONAL):
        raise ValueError('Rotation evidence changed')
    for group in ('timestamps', 'verification'):
        if any(following[group].get(k) != v for k, v in previous[group].items()):
            raise ValueError('Rotation history changed')
