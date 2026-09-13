"""Immutable, cross-client rotation journal; no secret values belong in snapshots."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Callable
import uuid

PHASES = (
    'prepared', 'replacement_saved', 'new_key_installed', 'new_login_verified',
    'binding_superseded', 'old_key_removed',
    'old_login_denied_and_new_verified', 'cleanup_confirmed',
)
MAX_BYTES = 65536
_TEMP = re.compile(r'\.rotation-publish-[0-9a-f-]{36}\Z')


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def _directory(path: Path, private: bool = False) -> None:
    if path.resolve() != path.absolute():
        raise ValueError('Symlinked rotation directory')
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & (0o077 if private else 0o022)):
        raise ValueError('Unsafe rotation directory')


def _sync(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def read_record(path: Path) -> bytes:
    path = Path(path).absolute()
    _directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > MAX_BYTES):
            raise ValueError('Unsafe rotation record')
        if info.st_nlink > 1:
            for candidate in path.parent.iterdir():
                if not _TEMP.fullmatch(candidate.name):
                    continue
                try:
                    other = candidate.lstat()
                    if (stat.S_ISREG(other.st_mode) and other.st_uid == os.getuid()
                            and stat.S_IMODE(other.st_mode) == 0o600
                            and (other.st_dev, other.st_ino) == (info.st_dev, info.st_ino)):
                        candidate.unlink(missing_ok=True)
                except FileNotFoundError:
                    pass
            info = os.fstat(fd)
        if info.st_nlink != 1:
            raise ValueError('Shared rotation record')
        data = b''
        while len(data) <= MAX_BYTES:
            part = os.read(fd, MAX_BYTES + 1 - len(data))
            if not part:
                break
            data += part
        after = os.fstat(fd)
        current = path.lstat()
        if (len(data) != info.st_size or after.st_size != info.st_size
                or after.st_mtime_ns != info.st_mtime_ns
                or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino)):
            raise ValueError('Rotation record changed')
        os.fsync(fd)
        _sync(path.parent)
        return data
    finally:
        os.close(fd)


def publish_record(path: Path, data: bytes) -> bytes:
    """Publish complete bytes once; concurrent publishers adopt the winner."""
    path = Path(path).absolute()
    if not 0 < len(data) <= MAX_BYTES:
        raise ValueError('Rotation record size invalid')
    _directory(path.parent)
    temporary = path.parent / ('.rotation-publish-' + str(uuid.uuid4()))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)
    try:
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            pass
        _sync(path.parent)
    finally:
        temporary.unlink(missing_ok=True)
    return read_record(path)


def load_journal(path: Path, validate: Callable[[object], dict], transition=None) -> tuple[dict, str]:
    path = Path(path).absolute()
    data = read_record(path)
    state = validate(json.loads(data.decode('utf-8')))
    if state['phase'] != 'prepared':
        raise ValueError('Invalid rotation genesis')
    operation_id = state['operationId']
    folder = Path(str(path) + '.records')
    try:
        _directory(folder, private=True)
    except FileNotFoundError:
        return state, hashlib.sha256(data).hexdigest()
    missing = False
    for index, phase in enumerate(PHASES[1:], 1):
        try:
            following = read_record(folder / f'{index * 10}-{phase}.json')
        except FileNotFoundError:
            missing = True
            continue
        if missing:
            raise ValueError('Rotation journal phase gap')
        record = json.loads(following.decode('utf-8'))
        if (not isinstance(record, dict)
                or set(record) != {'schema', 'operationId', 'phase', 'previousSha256', 'state'}
                or record['schema'] != 'dreamlake.host-key-rotation/phase-v1'
                or record['operationId'] != operation_id or record['phase'] != phase
                or record['previousSha256'] != hashlib.sha256(data).hexdigest()):
            raise ValueError('Invalid rotation phase chain')
        prior = state
        state = validate(record['state'])
        if transition:
            transition(prior, state)
        if state['phase'] != phase or state['operationId'] != operation_id:
            raise ValueError('Mismatched rotation phase')
        data = following
    return state, hashlib.sha256(data).hexdigest()


def append_phase(path: Path, current: tuple[dict, str], next_state: dict,
                 validate: Callable[[object], dict], transition=None) -> tuple[dict, str]:
    index = PHASES.index(current[0]['phase']) + 1
    if (index >= len(PHASES) or next_state['operationId'] != current[0]['operationId']
            or next_state['phase'] != PHASES[index]):
        raise ValueError('Invalid rotation transition')
    validate(next_state)
    if transition:
        transition(current[0], next_state)
    latest = load_journal(path, validate, transition)
    if latest[1] != current[1]:
        return latest
    folder = Path(str(Path(path).absolute()) + '.records')
    folder.mkdir(mode=0o700, exist_ok=True)
    _directory(folder, private=True)
    _sync(folder.parent)
    record = {'schema': 'dreamlake.host-key-rotation/phase-v1',
              'operationId': next_state['operationId'], 'phase': next_state['phase'],
              'previousSha256': current[1], 'state': next_state}
    publish_record(folder / f'{index * 10}-{next_state["phase"]}.json', canonical_json(record))
    return load_journal(path, validate, transition)
