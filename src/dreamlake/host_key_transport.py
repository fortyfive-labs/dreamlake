"""Explicit selected-key SSH transport. Ambient SSH config is never evaluated."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import selectors
import shlex
import subprocess
import time

from .host_key_journal import publish_record, read_record
from . import host_key_remote


def _path(value):
    if not isinstance(value, str) or not Path(value).is_absolute() or re.search(r'[\r\n\x00%$]', value):
        raise ValueError('Rotation requires literal absolute file paths')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def validate_profile(profile):
    if not isinstance(profile, dict) or set(profile) not in (
        {'host', 'user', 'port', 'knownHostsFile'},
        {'host', 'user', 'port', 'knownHostsFile', 'jump'},
    ):
        raise ValueError('Invalid explicit rotation transport')
    endpoints = [profile]
    if 'jump' in profile:
        jump = profile['jump']
        if not isinstance(jump, dict) or set(jump) != {'host', 'user', 'port', 'knownHostsFile', 'identityFile'}:
            raise ValueError('Invalid explicit jump transport')
        _path(jump['identityFile'])
        endpoints.append(jump)
    for endpoint in endpoints:
        if (not isinstance(endpoint['host'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.:-]{0,253}', endpoint['host'])
                or not isinstance(endpoint['user'], str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', endpoint['user'])
                or type(endpoint['port']) is not int or not 1 <= endpoint['port'] <= 65535):
            raise ValueError('Invalid explicit rotation endpoint')
        _path(endpoint['knownHostsFile'])
    return profile


def ssh_config(profile, selected_key):
    validate_profile(profile)
    def endpoint(alias, data, key):
        return [f'Host {alias}', f'  HostName {data["host"]}', f'  User {data["user"]}',
                f'  Port {data["port"]}', f'  IdentityFile {_path(key)}',
                f'  UserKnownHostsFile {_path(data["knownHostsFile"])}',
                '  GlobalKnownHostsFile /dev/null', '  StrictHostKeyChecking yes',
                '  UpdateHostKeys no', '  VerifyHostKeyDNS no', '  IdentitiesOnly yes',
                '  IdentityAgent none', '  CertificateFile none', '  PKCS11Provider none',
                '  PreferredAuthentications publickey', '  PasswordAuthentication no',
                '  KbdInteractiveAuthentication no', '  BatchMode yes', '  ControlMaster no',
                '  ControlPath none', '  ControlPersist no', '  ForwardAgent no',
                '  ClearAllForwardings yes', '  PermitLocalCommand no', '  RequestTTY no',
                '  ConnectionAttempts 1', '  ConnectTimeout 10']
    lines = endpoint('rotation-target', profile, selected_key)
    if 'jump' in profile:
        lines += ['  ProxyJump rotation-jump', ''] + endpoint('rotation-jump', profile['jump'], profile['jump']['identityFile'])
    return '\n'.join(lines) + '\n'


def identity(value):
    if (not isinstance(value, dict)
            or set(value) != {'uid', 'home', 'machineId', 'sshDirectoryDevice', 'sshDirectoryInode'}
            or any(type(value[k]) is not int or not 0 <= value[k] <= 9007199254740991
                   for k in ('uid', 'sshDirectoryDevice', 'sshDirectoryInode'))
            or not isinstance(value['home'], str) or not value['home'].startswith('/')
            or len(value['home']) > 4096 or re.search(r'[\r\n\x00]', value['home'])
            or not isinstance(value['machineId'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value['machineId'])):
        raise ValueError('Invalid remote identity')
    return value


def _request(value):
    if (not isinstance(value, dict)
            or set(value) not in ({'action', 'operationId', 'oldPublicKey', 'newPublicKey'},
                                  {'action', 'operationId', 'oldPublicKey', 'newPublicKey', 'expectedIdentity'})
            or value['action'] not in ('inspect', 'install', 'remove_old', 'remove_new')
            or not isinstance(value['operationId'], str)
            or not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', value['operationId'], re.I)):
        raise ValueError('Invalid remote operation metadata')
    for key in ('oldPublicKey', 'newPublicKey'):
        host_key_remote.public(value[key])
    if 'expectedIdentity' in value:
        identity(value['expectedIdentity'])
    elif value['action'] != 'inspect':
        raise ValueError('Remote mutation requires pinned identity')


def _bounded_process(argv, data, *, timeout=30):
    """Bound both output and elapsed time; never expose process diagnostics."""
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env={**os.environ, 'LC_ALL': 'C'})
    outputs = {process.stdout: bytearray(), process.stderr: bytearray()}
    selector = selectors.DefaultSelector()
    try:
        for stream in (process.stdin, process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
        selector.register(process.stdin, selectors.EVENT_WRITE)
        selector.register(process.stdout, selectors.EVENT_READ)
        selector.register(process.stderr, selectors.EVENT_READ)
        sent = 0
        deadline = time.monotonic() + timeout
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Remote rotation timed out')
            for key, _ in selector.select(min(remaining, .25)):
                stream = key.fileobj
                if stream is process.stdin:
                    try:
                        sent += os.write(stream.fileno(), data[sent:])
                    except BrokenPipeError:
                        sent = len(data)
                    if sent == len(data):
                        selector.unregister(stream)
                        stream.close()
                else:
                    chunk = os.read(stream.fileno(), 8192)
                    if not chunk:
                        selector.unregister(stream)
                    else:
                        outputs[stream].extend(chunk)
                        if len(outputs[stream]) > 65536:
                            raise ValueError('Remote rotation output exceeded limit')
        return process.wait(timeout=max(.01, deadline - time.monotonic())), bytes(outputs[process.stdout]), bytes(outputs[process.stderr])
    finally:
        selector.close()
        if process.poll() is None:
            process.kill()
        process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


def public_key_denied(code, stderr, profile):
    return code == 255 and f'{profile["user"]}@{profile["host"]}: Permission denied (publickey).' in stderr.splitlines()


def run_helper(*, profile, selected_key, config_file, request, expect_denied=False, executable='ssh'):
    _request(request)
    _path(selected_key)
    read_record(Path(selected_key))
    if 'jump' in profile:
        read_record(Path(profile['jump']['identityFile']))
    config = ssh_config(profile, selected_key).encode()
    if publish_record(Path(config_file), config) != config:
        raise ValueError('Rotation transport intent changed')
    data = json.dumps(request, ensure_ascii=False).encode()
    if len(data) > 65536:
        raise ValueError('Remote metadata exceeds limit')
    source = Path(host_key_remote.__file__).read_text()
    try:
        code, stdout, stderr = _bounded_process(
            [executable, '-F', str(config_file), 'rotation-target', 'python3', '-c', shlex.quote(source)], data)
        if expect_denied and public_key_denied(code, stderr.decode('utf-8'), profile):
            return 'publickey-denied'
        if code != 0 or expect_denied:
            raise ValueError('Remote step unconfirmed')
        reply = json.loads(stdout.decode('utf-8'))
        if (not isinstance(reply, dict) or set(reply) != {'identity', 'oldPresent', 'newPresent'}
                or type(reply['oldPresent']) is not bool or type(reply['newPresent']) is not bool):
            raise ValueError('Invalid remote reply')
        identity(reply['identity'])
        if request.get('expectedIdentity') is not None and reply['identity'] != request['expectedIdentity']:
            raise ValueError('Remote identity changed')
        return reply
    except Exception:
        raise ValueError('Remote rotation step unconfirmed; retain the operation and retry') from None
