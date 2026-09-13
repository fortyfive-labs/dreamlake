"""Fresh password-only SSH verification. No password changes or unexpected prompts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import socket
import sys
import tempfile
import threading
import uuid

from .host_key_journal import publish_record, read_record
from .host_key_rotation import _PinnedVault, now
from .host_key_transport import _AuthLog, _bounded_process, ssh_config, validate_profile
from .vault import VaultError, _metadata_response

# This helper has only a private IPC path in its argv. Password bytes flow from
# parent memory to OpenSSH's dedicated askpass stdout, never ordinary CLI output.
ASKPASS = '''import socket,sys
s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
s.settimeout(5)
s.connect(sys.argv[1])
s.sendall(sys.argv[2].encode('utf-8')+b'\\n')
b=b''
while True:
 c=s.recv(1024)
 if not c:break
 b+=c
 if len(b)>1024:sys.exit(1)
s.close()
if not b.endswith(b'\\n'):sys.exit(1)
sys.stdout.buffer.write(b)
'''


def password_config(profile):
    """Only the target changes auth method; the explicitly selected jump is key-only."""
    config = ssh_config(profile, '/dev/null')
    target, sep, jump = config.partition('Host rotation-jump')
    target = target.replace('  IdentityFile "/dev/null"', '  IdentityFile none')
    target = target.replace('  PreferredAuthentications publickey',
                            '  PreferredAuthentications password\n  PubkeyAuthentication no\n  GSSAPIAuthentication no\n  HostbasedAuthentication no\n  NumberOfPasswordPrompts 1')
    target = target.replace('  PasswordAuthentication no', '  PasswordAuthentication yes')
    target = target.replace('  BatchMode yes', '  BatchMode no')
    return target + sep + jump


def password_bytes(value):
    if not isinstance(value, str):
        raise ValueError('Password input required')
    raw = value.encode('utf-8', errors='strict')
    if not 1 <= len(raw) <= 512 or any(b in raw for b in (b'\0', b'\r', b'\n')):
        raise ValueError('Password authentication requires 1–512 UTF-8 bytes without NUL or line breaks')
    return bytearray(raw)


def account_identity(value):
    if (not isinstance(value, dict) or set(value) != {'uid', 'user', 'home', 'machineId'}
            or type(value['uid']) is not int or not 0 <= value['uid'] <= 9007199254740991
            or not isinstance(value['user'], str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', value['user'])
            or not isinstance(value['home'], str) or not value['home'].startswith('/')
            or len(value['home']) > 4096 or re.search(r'[\r\n\0]', value['home'])
            or any(p in ('.', '..') for p in value['home'].split('/'))
            or not isinstance(value['machineId'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value['machineId'])):
        raise ValueError('Invalid remote account identity')
    return value


class _OneShotAskpass:
    def __init__(self, directory, secret, profile):
        self.path = directory / 'askpass.sock'
        self.secret = secret
        self.prompt = f"{profile['user']}@{profile['host']}'s password: "
        self.requests = 0
        self.delivered = 0
        self.stopped = threading.Event()
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(self.path))
        os.chmod(self.path, 0o600)
        self.server.listen(2)
        self.server.settimeout(.1)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while not self.stopped.is_set():
            try:
                connection, _ = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with connection:
                connection.settimeout(2)
                self.requests += 1
                try:
                    data = bytearray()
                    while len(data) <= 1024:
                        chunk = connection.recv(1024)
                        if not chunk:break
                        data.extend(chunk)
                        if b'\n' in data:break
                    if self.requests == 1 and data.decode('utf-8', errors='strict') == self.prompt + '\n':
                        connection.sendall(self.secret + b'\n')
                        self.delivered += 1
                except (OSError, UnicodeError):
                    pass

    def close(self):
        self.stopped.set()
        self.server.close()
        self.thread.join(timeout=3)
        self.secret[:] = b'\0' * len(self.secret)
        self.path.unlink(missing_ok=True)
        if self.thread.is_alive():
            raise ValueError('Password helper cleanup unconfirmed')


def probe_password(*, profile, password, executable='ssh'):
    """Return verified/denied metadata; inconclusive transport fails closed."""
    validate_profile(profile)
    raw = password_bytes(password)
    if 'jump' in profile:
        read_record(Path(profile['jump']['identityFile']))
    nonce = str(uuid.uuid4())
    program = "import json,os,pwd,pathlib;u=pwd.getpwuid(os.getuid());print(json.dumps({'nonce':" + repr(nonce) + ", 'identity':{'uid':u.pw_uid,'user':u.pw_name,'home':u.pw_dir,'machineId':pathlib.Path('/etc/machine-id').read_text().strip()}}))"
    try:
        # A short canonical path also fits macOS's Unix-socket path limit.
        with tempfile.TemporaryDirectory(prefix='dl-pw-', dir='/tmp') as temporary:
            directory = Path(temporary).resolve()
            os.chmod(directory, 0o700)
            config = directory / 'config'
            publish_record(config, password_config(profile).encode())
            helper = directory / 'askpass'
            script = '#!/bin/sh\nexec ' + shlex.join([sys.executable, '-I', '-c', ASKPASS, str(directory / 'askpass.sock')]) + ' "$@"\n'
            publish_record(helper, script.encode())
            helper.chmod(0o700)
            broker = _OneShotAskpass(directory, raw, profile)
            log = _AuthLog(directory)
            try:
                code, output, _ = _bounded_process(
                    [executable, '-F', str(config), '-v', '-E', str(log.path), 'rotation-target', 'python3', '-c', shlex.quote(program)],
                    b'', monitor=log.check_size,
                    environment={'SSH_ASKPASS': str(helper), 'SSH_ASKPASS_REQUIRE': 'force', 'DISPLAY': 'dreamlake-password-probe'})
                lines = log.read().splitlines()
                successes = [line for line in lines if re.match(r'^(?:debug[123]: )?(?:Authenticated to |Authentication succeeded\b)', line)]
                if broker.requests != 1 or broker.delivered != 1:
                    raise ValueError('Selected password delivery unconfirmed')
                methods = [line for line in lines if line.startswith('debug1: Next authentication method: ')]
                denied_line = re.compile(re.escape(f"{profile['user']}@{profile['host']}: Permission denied (") + r'[a-z0-9,-]+\)\.')
                if code == 255 and not successes and methods == ['debug1: Next authentication method: password'] and 'debug1: No more authentication methods to try.' in lines and any(denied_line.fullmatch(line) for line in lines):
                    return {'status': 'denied', 'method': 'ssh_password', 'observedAt': now()}
                if code != 0 or methods != ['debug1: Next authentication method: password'] or len(successes) != 1 or not successes[0].endswith(' using "password".'):
                    raise ValueError('Password authentication unconfirmed')
                reply = json.loads(output.decode('utf-8', errors='strict'))
                if not isinstance(reply, dict) or set(reply) != {'nonce', 'identity'} or reply['nonce'] != nonce:
                    raise ValueError('Invalid remote verification reply')
                identity = account_identity(reply['identity'])
                if identity['user'] != profile['user']:
                    raise ValueError('Remote account mismatch')
                return {'status': 'verified', 'method': 'ssh_password', 'observedAt': now(), 'remoteIdentity': identity}
            finally:
                try:
                    broker.close()
                finally:
                    log.close()
    except Exception:
        raise VaultError('Password authentication unconfirmed; no password was changed') from None
    finally:
        raw[:] = b'\0' * len(raw)


def verify_host_password(original, *, binding_id, ssh):
    vault = _PinnedVault(original)
    try:
        before = vault.host_credential(binding_id)
        binding, entry = before['binding'], before['entry']
        if (binding['kind'] != 'password' or binding.get('releasedAt') is not None
                or not entry or entry.get('type') != 'string' or entry['id'] != binding['entryId'] or entry['revision'] != binding['entryRevision']
                or entry.get('deleteAt') or entry.get('purgeAt') or entry.get('status') == 'expired'):
            raise ValueError('Inactive password binding')
        read = vault._request('POST', '/v1/vault/entries/read', json={'selectors': [entry['name']]})
        rows = read.get('entries') if isinstance(read, dict) else None
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or not isinstance(rows[0].get('value'), str):
            raise ValueError('Password unavailable')
        metadata = _metadata_response({'entry': rows[0]}, entry['name'])
        if metadata['id'] != entry['id'] or metadata['revision'] != entry['revision'] or metadata['type'] != 'string':
            raise ValueError('Password identity changed')
        value = rows[0].pop('value')
        if vault.host_credential(binding_id) != before:
            raise ValueError('Password binding changed')
        result = probe_password(profile=ssh, password=value)
        value = None
        if vault.host_credential(binding_id) != before:
            raise ValueError('Password binding changed')
        return {**result, 'bindingId': binding_id, 'entryId': entry['id'], 'entryRevision': entry['revision']}
    except Exception:
        raise VaultError('Password authentication unconfirmed; no password was changed') from None
