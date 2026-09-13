"""Standalone remote Python3 helper: public-key metadata only; no SDK dependency."""
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import sys
import uuid


class Refused(Exception):
    pass


def checked(path, directory=False, private=False):
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022 or not directory and info.st_nlink != 1:
        raise Refused('UNSAFE_PATH')
    if private and info.st_mode & 0o077:
        raise Refused('UNSAFE_PATH')
    return info


def public(value):
    if not isinstance(value, str) or not re.fullmatch(r'(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)) [A-Za-z0-9+/]+={0,3}', value) or len(value) > 8192:
        raise Refused('INVALID_PUBLIC_KEY')
    kind, encoded = value.split(' ')
    try:
        raw = base64.b64decode(encoded, validate=True)
        size = int.from_bytes(raw[:4], 'big')
        if raw[4:4 + size].decode() != kind or len(raw) < 4 + size + 4:
            raise ValueError()
    except (ValueError, UnicodeError):
        raise Refused('INVALID_PUBLIC_KEY') from None
    return value.encode()


def restrictions(prefix):
    """Preserve allowed options byte-for-byte; never silently broaden access."""
    text = prefix.strip().decode('utf-8', errors='strict')
    if not text:
        return
    chunks, current, quoted, escaped = [], '', False, False
    for char in text:
        if escaped:
            current += char; escaped = False; continue
        if char == '\\' and quoted:
            current += char; escaped = True; continue
        if char == '"':
            quoted = not quoted
        if char == ',' and not quoted:
            chunks.append(current); current = ''; continue
        if char.isspace() and not quoted:
            raise Refused('UNSUPPORTED_KEY_CONSTRAINTS')
        current += char
    chunks.append(current)
    flags = {'restrict', 'no-agent-forwarding', 'no-port-forwarding', 'no-pty', 'no-user-rc', 'no-X11-forwarding', 'agent-forwarding', 'port-forwarding', 'pty', 'user-rc', 'X11-forwarding'}
    values = {'command', 'from', 'environment', 'permitopen', 'permitlisten', 'expiry-time', 'tunnel'}
    if quoted or escaped or any(c not in flags and not any(c.startswith(k + '="') and c.endswith('"') for k in values) for c in chunks):
        raise Refused('UNSUPPORTED_KEY_CONSTRAINTS')


def matching(lines, key):
    kind, blob = key.split(b' ')
    result = []
    expression = re.compile(rb'(?<!\S)' + re.escape(kind) + rb'[ \t]+' + re.escape(blob) + rb'(?=[ \t]|$)')
    for index, line in enumerate(lines):
        content = line.rstrip(b'\r\n')
        matches = list(expression.finditer(content))
        if not matches:
            continue
        if len(matches) != 1:
            raise Refused('AMBIGUOUS_KEY')
        match = matches[0]
        prefix = content[:match.start()]
        restrictions(prefix)
        result.append((index, prefix, line))
    if len(result) > 1:
        raise Refused('DUPLICATE_KEY')
    return result


def digest(value):
    return hashlib.sha256(value).hexdigest()


def read_checked(name, parent_fd, private=False):
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
    except OSError:
        raise Refused('UNSAFE_PATH') from None
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022 or info.st_nlink != 1 or private and info.st_mode & 0o077:
            raise Refused('UNSAFE_PATH')
        data = stream.read(1024 * 1024 + 1)
        after = os.fstat(stream.fileno())
        if len(data) > 1024 * 1024 or (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise Refused('FILE_CHANGED')
        return data, info


def same_directory(path, parent_fd):
    current, pinned = checked(path, directory=True), os.fstat(parent_fd)
    if (current.st_dev, current.st_ino) != (pinned.st_dev, pinned.st_ino):
        raise Refused('REMOTE_IDENTITY_CHANGED')


def atomic(path, value, mode, parent_fd, group=None, expected=None):
    temporary = '.rotation-' + uuid.uuid4().hex
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
    try:
        os.fchmod(descriptor, mode)
        if group is not None: os.fchown(descriptor, -1, group)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(value); stream.flush(); os.fsync(stream.fileno())
        same_directory(path.parent, parent_fd)
        if expected is not None:
            old_bytes, old_info = expected
            current_bytes, current_info = read_checked(path.name, parent_fd)
            fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_size', 'st_mtime_ns')
            if current_bytes != old_bytes or any(getattr(current_info, k) != getattr(old_info, k) for k in fields):
                raise Refused('AUTHORIZED_KEYS_CHANGED')
        os.replace(temporary, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        try: os.unlink(temporary, dir_fd=parent_fd)
        except FileNotFoundError: pass


def operate(payload, *, home=None, machine_id=None):
    allowed = {'action', 'operationId', 'oldPublicKey', 'newPublicKey', 'expectedIdentity'}
    if not isinstance(payload, dict) or set(payload) - allowed or payload.get('action') not in ('inspect', 'install', 'remove_old', 'remove_new') or not isinstance(payload.get('operationId'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', payload['operationId']):
        raise Refused('INVALID_REQUEST')
    old, new = public(payload.get('oldPublicKey')), public(payload.get('newPublicKey'))
    if old == new: raise Refused('SAME_KEY')
    home = Path(home) if home is not None else Path(pwd.getpwuid(os.getuid()).pw_dir)
    checked(home, directory=True)
    folder = home / '.ssh'; info = checked(folder, directory=True)
    machine = machine_id if machine_id is not None else Path('/etc/machine-id').read_text().strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', machine): raise Refused('INVALID_MACHINE_ID')
    identity = {'uid': os.getuid(), 'home': str(home), 'machineId': machine, 'sshDirectoryDevice': info.st_dev, 'sshDirectoryInode': info.st_ino}
    if payload.get('expectedIdentity') is not None and payload['expectedIdentity'] != identity:
        raise Refused('REMOTE_IDENTITY_CHANGED')
    if payload['action'] != 'inspect' and payload.get('expectedIdentity') is None:
        raise Refused('EXPECTED_IDENTITY_REQUIRED')
    lock = folder / '.dreamlake-vault-rotation.lock'
    folder_fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd = os.open(lock.name, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=folder_fd)
    journal_fd = None
    try:
        lock_info = checked(lock, private=True)
        if (lock_info.st_dev, lock_info.st_ino) != (os.fstat(fd).st_dev, os.fstat(fd).st_ino): raise Refused('UNSAFE_PATH')
        fcntl.flock(fd, fcntl.LOCK_EX)
        same_directory(folder, folder_fd)
        authorized = folder / 'authorized_keys'
        contents, metadata = read_checked(authorized.name, folder_fd)
        lines = contents.splitlines(keepends=True)
        old_matches, new_matches = matching(lines, old), matching(lines, new)
        journal_dir = folder / '.dreamlake-vault-rotations'
        if journal_dir.exists():
            checked(journal_dir, directory=True, private=True)
            journal_fd = os.open(journal_dir.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=folder_fd)
            same_directory(journal_dir, journal_fd)
        journal_path = journal_dir / (payload['operationId'] + '.json')
        journal = None
        if journal_path.exists():
            if journal_fd is None: raise Refused('UNSAFE_PATH')
            journal_bytes, _ = read_checked(journal_path.name, journal_fd, private=True)
            journal = json.loads(journal_bytes)
            if any(journal.get(k) != v for k, v in {'operationId': payload['operationId'], 'oldPublicKey': old.decode(), 'newPublicKey': new.decode(), 'identity': identity}.items()):
                raise Refused('OPERATION_CONFLICT')
        if not old_matches and not journal:
            raise Refused('OLD_KEY_NOT_FOUND')
        if new_matches and not journal:
            raise Refused('REPLACEMENT_ALREADY_PRESENT')
        if journal:
            if journal.get('state') not in ('prepared', 'installed', 'removing_old', 'old_removed', 'removing_new', 'new_removed'): raise Refused('INVALID_JOURNAL')
            if not old_matches and journal['state'] not in ('removing_old', 'old_removed'): raise Refused('OLD_KEY_NOT_FOUND')
            if old_matches and digest(old_matches[0][2]) != journal['oldLineHash']:
                raise Refused('OLD_KEY_CHANGED')
            if new_matches and digest(new_matches[0][2]) != journal['newLineHash']:
                raise Refused('NEW_KEY_CHANGED')
        action = payload['action']
        if action == 'install':
            if journal and journal['state'] not in ('prepared', 'installed'): raise Refused('OPERATION_TERMINAL')
            if not old_matches: raise Refused('OLD_KEY_NOT_FOUND')
            if journal is None:
                ending = b'\r\n' if old_matches[0][2].endswith(b'\r\n') else b'\n'
                new_line = old_matches[0][1] + new + b' dreamlake-vault:' + payload['operationId'].encode() + ending
                journal = {'operationId': payload['operationId'], 'oldPublicKey': old.decode(), 'newPublicKey': new.decode(), 'identity': identity, 'oldLineHash': digest(old_matches[0][2]), 'newLineHash': digest(new_line), 'state': 'prepared'}
                journal_dir.mkdir(mode=0o700, exist_ok=True); checked(journal_dir, directory=True, private=True)
                if journal_fd is None: journal_fd = os.open(journal_dir.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=folder_fd)
                atomic(journal_path, json.dumps(journal).encode(), 0o600, journal_fd)
            else:
                ending = b'\r\n' if old_matches[0][2].endswith(b'\r\n') else b'\n'
                new_line = old_matches[0][1] + new + b' dreamlake-vault:' + payload['operationId'].encode() + ending
                if digest(new_line) != journal['newLineHash']: raise Refused('NEW_KEY_CHANGED')
            if not new_matches:
                atomic(authorized, new_line + contents, stat.S_IMODE(metadata.st_mode), folder_fd, metadata.st_gid, (contents, metadata))
            journal['state'] = 'installed'
        elif action in ('remove_old', 'remove_new'):
            if not journal: raise Refused('OPERATION_NOT_FOUND')
            if action == 'remove_old' and not new_matches: raise Refused('NEW_KEY_NOT_FOUND')
            if action == 'remove_new' and not old_matches: raise Refused('OLD_KEY_NOT_FOUND')
            if action == 'remove_old' and journal['state'] not in ('installed', 'removing_old', 'old_removed'): raise Refused('OPERATION_TERMINAL')
            if action == 'remove_new' and journal['state'] not in ('prepared', 'installed', 'removing_new', 'new_removed'): raise Refused('OPERATION_TERMINAL')
            selected = old_matches if action == 'remove_old' else new_matches
            journal['state'] = 'removing_old' if action == 'remove_old' else 'removing_new'
            atomic(journal_path, json.dumps(journal).encode(), 0o600, journal_fd)
            if selected:
                del lines[selected[0][0]]
                atomic(authorized, b''.join(lines), stat.S_IMODE(metadata.st_mode), folder_fd, metadata.st_gid, (contents, metadata))
            journal['state'] = 'old_removed' if action == 'remove_old' else 'new_removed'
        if action != 'inspect': atomic(journal_path, json.dumps(journal).encode(), 0o600, journal_fd)
        final = read_checked(authorized.name, folder_fd)[0].splitlines(keepends=True)
        return {'identity': identity, 'oldPresent': bool(matching(final, old)), 'newPresent': bool(matching(final, new))}
    finally:
        os.close(fd)
        os.close(folder_fd)
        if journal_fd is not None: os.close(journal_fd)


def main():
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536: raise Refused("INPUT_TOO_LARGE")
        payload = json.loads(raw)
        result = operate(payload)
        print(json.dumps(result))
    except Exception as error:
        code = str(error) if isinstance(error, Refused) else 'REMOTE_OPERATION_FAILED'
        print(json.dumps({'error': code})); raise SystemExit(1)


if __name__ == '__main__': main()
