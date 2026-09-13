"""Durable metadata-only HOTP intents; no prompting, secret files or retries."""
import base64
import json
import os
import re
import stat
from uuid import uuid4
from urllib.parse import urlsplit
from datetime import datetime, timezone
import httpx
from .vault import VaultError, VaultHttpError


def _intent(value, binding):
    keys = {'version', 'origin', 'account', 'name', 'entryId', 'expectedRevision', 'requestId'}
    if not isinstance(value, dict) or set(value) != keys or type(value['version']) is not int or value['version'] != 1 or any(value[k] != v for k, v in binding.items()) or not isinstance(value['entryId'], str) or not 1 <= len(value['entryId']) <= 128 or type(value['expectedRevision']) is not int or not 1 <= value['expectedRevision'] <= 9007199254740991 or not isinstance(value['requestId'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value['requestId']):
        raise VaultError('Invalid or mismatched OTP request file')
    return value


def intent_file(path, binding, metadata):
    def read():
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid() or info.st_size > 4096:
                raise VaultError('Unsafe OTP request file')
            return _intent(json.loads(stream.read(4097)), binding)
    try:
        return read()
    except FileNotFoundError:
        pass
    entry = metadata().get('entry')
    if not isinstance(entry, dict) or entry.get('name') != binding['name'] or entry.get('type') != 'hotp' or entry.get('deleteAt'):
        raise VaultError('HOTP entry required')
    value = _intent(dict(version=1, **binding, entryId=entry.get('id'), expectedRevision=entry.get('revision'), requestId=str(uuid4())), binding)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        return read()
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(json.dumps(value) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    return value


def retrieve(vault, name, request_file):
    from .pass_import import _send_prepared
    try:
        connection = vault._http
        base = str(connection.base_url).rstrip('/')
        url = urlsplit(base)
        if url.username or url.password or url.query or url.fragment or url.scheme not in ('http', 'https') or url.scheme == 'http' and url.hostname not in ('localhost', '127.0.0.1', '::1') or connection.auth is not None or any(connection.event_hooks.values()):
            raise VaultError('Unsupported HOTP connection')
        headers = dict(connection.headers)
        token = headers.get('authorization', '').removeprefix('Bearer ')
        encoded = token.split('.')[1]
        subject = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4))).get('sub')
        if not isinstance(subject, str) or not 1 <= len(subject) <= 512:
            raise VaultError('Owner session required')
        destination = httpx.URL(base + '/v1/vault/otp')
        transport = connection._transport_for_url(destination)
        timeout = connection.timeout.as_dict()
        # Capture cookies with their original scope, then discard mutable client
        # state: later retries reuse the same destination/account intent.
        prepared = connection.build_request('POST', destination)
        pinned_headers = dict(prepared.headers)
        def send(method, path, body=None):
            request = httpx.Request(method, base + path, headers=pinned_headers,
                                    extensions={'timeout': dict(timeout)}, json=body)
            # build_request's empty body may carry content-length:0.
            request.headers.pop('content-length', None)
            if body is not None:
                request.headers['content-length'] = str(len(request.content))
            response = _send_prepared(transport, request)
            if not response.is_success:
                raise VaultHttpError(response.status_code)
            return response.json()
        binding = dict(origin=base, account=subject, name=name)
        intent = intent_file(request_file, binding, lambda: send('GET', '/v1/vault/entry?' + str(httpx.QueryParams(name=name))))
        result = send('POST', '/v1/vault/otp', {key: intent[key] for key in ('name', 'entryId', 'expectedRevision', 'requestId')})
        if not isinstance(result, dict) or result.get('type') != 'hotp' or not isinstance(result.get('code'), str) or not re.fullmatch(r'[0-9]{6,8}', result['code']) or result.get('requestId') != intent['requestId'] or type(result.get('revision')) is not int or result['revision'] != intent['expectedRevision'] + 1 or type(result.get('replayed')) is not bool or not isinstance(result.get('recoverUntil'), str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z', result['recoverUntil']) or datetime.fromisoformat(result['recoverUntil'].replace('Z', '+00:00')) <= datetime.now(timezone.utc):
            raise VaultError('Invalid HOTP response')
        return {key: result[key] for key in ('type', 'code', 'requestId', 'revision', 'replayed', 'recoverUntil')}
    except Exception:
        raise VaultError('HOTP operation failed or outcome unknown; reuse the same request_file, never a fresh intent to retry') from None
