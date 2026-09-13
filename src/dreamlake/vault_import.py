"""Explicit one-way SSH import. Static parsing only; never executes OpenSSH/config."""
import base64
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlparse

from .vault import VaultError, _entry_name, _revision_headers, _validate_secret

LIMIT = 65536


def read_ssh_file(path, private_key=False):
    """Bounded, no-follow, owner-only regular-file read with identity/race checks."""
    buffer = bytearray()
    try:
        path = Path(path)
        if not path.is_absolute() or path.resolve() != path:
            raise ValueError()
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            def allowed(s):
                return (stat.S_ISREG(s.st_mode) and s.st_size <= LIMIT and s.st_nlink == 1
                        and s.st_uid == os.getuid() and not s.st_mode & (0o077 if private_key else 0o022))
            if not allowed(opened) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise ValueError()
            buffer.extend(stream.read(LIMIT + 1))
            after, current = os.fstat(stream.fileno()), path.lstat()
            if (len(buffer) > LIMIT or not allowed(after)
                    or (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
                    or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino) or path.resolve() != path):
                raise ValueError()
            value = buffer.decode('utf-8')
            if '\0' in value:
                raise ValueError()
            return value
    except Exception:
        raise VaultError('Selected private-key file is unsafe, changed, unreadable or invalid UTF-8' if private_key
                         else 'SSH config is unsafe, changed, unreadable or invalid UTF-8') from None
    finally:
        buffer[:] = b'\0' * len(buffer)


def _words(line):
    result, word, quote, started = [], '', '', False
    for char in line:
        if char == '\\':
            return None
        if quote:
            if char == quote:
                quote = ''
            else:
                word += char
            started = True
        elif char in ('"', "'"):
            quote, started = char, True
        elif char == '#':
            break
        elif char.isspace():
            if started:
                result.append(word)
            word, started = '', False
        else:
            word, started = word + char, True
    if quote:
        return None
    if started:
        result.append(word)
    return result


def discover_ssh(config, home=None):
    if len(config.encode('utf-8')) > LIMIT or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', config):
        raise VaultError('SSH config contains unsupported controls or exceeds 64 KiB')
    warnings, blocks, block = [], [], None
    for index, raw in enumerate(config.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'([A-Za-z][A-Za-z0-9]*)\s*(?:=\s*|\s+)(.*)', line)
        if not match:
            if block is not None:
                block['invalid'] = True
            warnings.append(f'Line {index}: unsupported syntax; current profile excluded')
            continue
        directive, text = match[1].lower(), match[2]
        if directive == 'match':
            block = None
            warnings.append('Match sections ignored; no conditions executed')
            continue
        if directive == 'include':
            warnings.append('Include ignored; no files followed')
            continue
        if directive == 'host':
            aliases = _words(text)
            block = dict(aliases=aliases or [], values={}, keys=[], invalid=aliases is None)
            blocks.append(block)
            continue
        if directive not in ('hostname', 'user', 'port', 'proxyjump', 'identityfile'):
            warnings.append(f'Line {index}: directive omitted from portable profiles')
            continue
        if block is None:
            warnings.append('Global and Match settings ignored')
            continue
        args = _words(text)
        if not args or len(args) != 1 or not args[0]:
            block['invalid'] = True
            continue
        if directive == 'identityfile':
            block['keys'].append(args[0])
        else:
            block['values'].setdefault(directive, args[0])
    counts = Counter(alias.lower() for b in blocks for alias in b['aliases'])
    items = []
    for b in blocks:
        for alias in b['aliases']:
            if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', alias)
                    or any(re.search(r'[*?!]', a) for a in b['aliases']) or counts[alias.lower()] != 1):
                warnings.append('Wildcard, duplicate, negated or unsafe aliases excluded')
                continue
            v = b['values']
            host, user, port, jump = v.get('hostname', alias), v.get('user'), v.get('port'), v.get('proxyjump')
            if (b['invalid'] or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.:-]*', host)
                    or user is not None and not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', user)
                    or port is not None and (not re.fullmatch(r'[0-9]{1,5}', port) or not 1 <= int(port) <= 65535)
                    or jump is not None and not re.fullmatch(r'[A-Za-z0-9_.@,:\[\]-]+', jump)):
                warnings.append(f'Profile {alias} excluded: unsupported connection fields')
                continue
            profile = dict(schema='dreamlake-ssh-profile-v1', alias=alias, hostname=host,
                           identityfiles=json.dumps(b['keys'], separators=(',', ':'), ensure_ascii=False),
                           interpretation='explicit-fields-only; dependencies are references')
            profile.update({k: value for k, value in [('user', user), ('port', port), ('proxyjump', jump)] if value})
            items.append(dict(id='profile:' + alias, kind='profile', alias=alias, profile=profile))
            for index, reference in enumerate(b['keys'], 1):
                if re.search(r'[\x00-\x1f\x7f%$`*?!]', reference) or reference == 'none':
                    path = None
                else:
                    path = str(Path(home or Path.home()) / reference[2:]) if reference.startswith('~/') else reference
                    path = os.path.abspath(path) if os.path.isabs(path) else None
                if path:
                    items.append(dict(id=f'key:{alias}:{index}', kind='private-key', alias=alias, profile=profile, key_path=path))
                else:
                    warnings.append(f'Profile {alias}: identity reference {index} is reference-only')
    warnings.append('Only explicit portable fields are copied; defaults, Include, wildcard and Match inheritance are not resolved. Jump hosts and keys remain references unless separately selected.')
    return items, list(dict.fromkeys(warnings))


def _validate_key(value):
    match = re.fullmatch(r'-----BEGIN (OPENSSH PRIVATE KEY|RSA PRIVATE KEY|EC PRIVATE KEY|DSA PRIVATE KEY|PRIVATE KEY|ENCRYPTED PRIVATE KEY)-----\r?\n([\s\S]+)\r?\n-----END \1-----(?:\r?\n)*', value)
    if not match:
        raise ValueError()
    if match[1] != 'OPENSSH PRIVATE KEY':
        return
    data = bytearray(base64.b64decode(re.sub(r'\s', '', match[2]), validate=True))
    try:
        if data[:15] != b'openssh-key-v1\0':
            raise ValueError()
        offset = 15
        def number():
            nonlocal offset
            if offset + 4 > len(data):
                raise ValueError()
            size = int.from_bytes(data[offset:offset + 4], 'big')
            offset += 4
            return size
        def field():
            nonlocal offset
            size = number()
            if size > len(data) - offset:
                raise ValueError()
            part = data[offset:offset + size]
            offset += size
            return part
        field(); field(); field()
        if number() != 1:
            raise ValueError()
        pub = field()
        size = int.from_bytes(pub[:4], 'big')
        if len(pub) < 4 or size > len(pub) - 4 or pub[4:4 + size].startswith(b'sk-'):
            raise ValueError()
        field()
        if offset != len(data):
            raise ValueError()
    finally:
        data[:] = b'\0' * len(data)


def import_ssh(vault, *, prefix, select=None, config=None, dry_run=False, if_match=None, retry=0):
    if not prefix:
        raise VaultError('SSH import requires an explicit prefix')
    _entry_name('ssh/probe', prefix)
    if type(retry) is not int or not 0 <= retry <= 3:
        raise VaultError('SSH retry count must be 0 to 3')
    if select is None:
        if not dry_run:
            raise VaultError('SSH import requires explicit selection; use [] for no upload')
        select = []
    if not isinstance(select, (list, tuple)) or not all(isinstance(i, str) for i in select) or len(set(select)) != len(select):
        raise VaultError('Invalid or duplicate SSH selection')
    revisions = if_match if if_match is not None else {}
    if not isinstance(revisions, dict) or any(key not in select for key in revisions):
        raise VaultError('Invalid SSH revision selection')
    for value in revisions.values():
        if value is None:
            raise VaultError('Invalid SSH revision selection')
        _revision_headers(value)
    url = urlparse(str(vault._http.base_url))
    if (url.username or url.password or url.query or url.fragment or url.scheme not in ('https', 'http')
            or url.scheme == 'http' and url.hostname not in ('localhost', '127.0.0.1', '::1')):
        raise VaultError('SSH import requires HTTPS; HTTP allowed only on loopback')
    path = Path(os.path.abspath(config or Path.home() / '.ssh/config'))
    original = read_ssh_file(path)
    items, warnings = discover_ssh(original)
    by_id = {item['id']: item for item in items}
    if any(item not in by_id for item in select):
        raise VaultError('Unknown or unsafe SSH selection')
    transfers = []
    for selected in select:
        item = by_id[selected]
        suffix = 'profiles/' + item['alias'] if item['kind'] == 'profile' else 'keys/' + item['alias'] + '-' + selected.split(':')[2]
        transfers.append(dict(id=selected, kind=item['kind'], name=_entry_name('ssh/' + suffix, prefix),
                              **({'ifMatch': revisions[selected]} if selected in revisions else {}), status='pending', detail='Not attempted'))
    report = dict(dryRun=dry_run, prefix=prefix, warnings=warnings, entries=transfers)
    if dry_run:
        report['discovered'] = [{k: item[k] for k in ('id', 'kind', 'profile')} for item in items]
        return report
    if read_ssh_file(path) != original:
        raise VaultError('SSH config changed since selection; start a new review')
    values = {}
    try:
        for transfer in transfers:
            try:
                item = by_id[transfer['id']]
                value = dict(item['profile']) if item['kind'] == 'profile' else read_ssh_file(item['key_path'], True)
                if isinstance(value, str):
                    _validate_key(value)
                _validate_secret(value)
                values[transfer['id']] = value
            except Exception:
                transfer.update(status='failure', detail='Selected source unavailable, unsafe, unsupported or too large; no write attempted')
        for _ in range(retry + 1):
            for transfer in transfers:
                if transfer['status'] == 'success' or transfer['id'] not in values:
                    continue
                value = values[transfer['id']]
                unknown, writing = transfer['status'] == 'unknown', False
                try:
                    try:
                        meta = vault.show(transfer['name'])
                    except VaultError as error:
                        if str(error) == 'Vault request failed (HTTP 404)':
                            meta = None
                        else:
                            raise
                    expired = meta and meta.get('expiresAt') and datetime.fromisoformat(meta['expiresAt']) <= datetime.now(timezone.utc)
                    if meta and not meta.get('deleteAt') and not expired and vault.get(transfer['name']) == value:
                        transfer.update(status='success', detail='Destination verified equal; no additional write')
                        continue
                    if unknown:
                        transfer['detail'] = 'Write remains unknown; no operation receipt. No replay attempted'
                        continue
                    if meta and 'ifMatch' not in transfer:
                        transfer.update(status='failure', detail='Destination exists and differs; explicit if_match revision required')
                        continue
                    writing = True
                    meta = vault.add(transfer['name'], value, if_match=transfer.get('ifMatch'))
                    if meta.get('deleteAt') or meta.get('type') != 'string' or meta.get('revision') != transfer.get('ifMatch', 0) + 1:
                        raise VaultError('Invalid vault acknowledgement')
                    transfer.update(status='success', detail=f"Saved revision {meta['revision']}")
                except Exception as error:
                    match = re.fullmatch(r'Vault request failed \(HTTP ([0-9]+)\)', str(error)) if isinstance(error, VaultError) else None
                    status = getattr(error, 'status', None) or (int(match[1]) if match else None)
                    rejected = status in (400, 401, 403, 404, 405, 409, 412, 413, 422, 429, 501)
                    outcome = 'unknown' if unknown or writing and not rejected else 'failure'
                    transfer.update(status=outcome, detail='Write outcome unknown; retry reconciles only, without replay' if outcome == 'unknown'
                                    else 'Revision or destination conflict; no automatic overwrite' if status in (409, 412)
                                    else 'Vault operation failed; response details redacted')
        report['message'] = 'Completed writes are retained. No rollback performed.'
        return report
    finally:
        values.clear()
