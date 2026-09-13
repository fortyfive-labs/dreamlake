"""Explicit post-enrollment saving. No prompts, remote key installation, or SSH delegation."""
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
import re
from urllib.parse import urlsplit
from .vault import VaultError, VaultWriteError, _entry_name
from .vault_import import read_ssh_file, _validate_key


@dataclass(frozen=True)
class HostCredential:
    """One explicitly selected target/jump credential; repr never reveals its source.

    password is provided in memory (e.g. by getpass in application code), never
    enrollment JSON. key_file must be a private regular file. entry_name is a
    personal vault path, independent of the host's namespace.
    """
    role: str
    endpoint: str
    entry_name: str
    kind: str
    password: str | None = field(default=None, repr=False)
    key_file: str | Path | None = field(default=None, repr=False)


def save_enrollment_credentials(vault, result, credentials, request_id):
    """Return redacted partial outcomes; never undo successful enrollment."""
    entries = []
    try:
        url = urlsplit(str(vault._http.base_url))
        if url.username or url.password or url.query or url.fragment or url.scheme not in ('http', 'https') or url.scheme == 'http' and url.hostname not in ('localhost', '127.0.0.1', '::1'):
            raise VaultError("Credential saving requires HTTPS")
        selected = tuple(credentials or ())
        if not selected:
            return {"status": "needs_input", "entries": []}
        if len(selected) > 32:
            raise VaultError("Too many selected credentials")
        seen, names = set(), set()
        for c in selected:
            if not isinstance(c, HostCredential) or c.role not in ('target', 'jump') or c.kind not in ('password', 'private_key') or not re.fullmatch(r'[A-Za-z0-9_.@:\[\]-]{1,255}', c.endpoint):
                raise VaultError("Invalid selected credential")
            _entry_name(c.entry_name)
            slot = (c.role, c.endpoint, c.kind)
            if slot in seen or c.entry_name in names:
                raise VaultError("Duplicate selection")
            seen.add(slot); names.add(c.entry_name)
            if c.kind == 'password' and (not isinstance(c.password, str) or not c.password or c.key_file is not None):
                raise VaultError("Password input required")
            if c.kind == 'private_key' and (c.key_file is None or c.password is not None):
                raise VaultError("Private-key source required")
        for c in selected:
            rid = 'host_' + sha256((request_id + '\0' + c.role + '\0' + c.endpoint + '\0' + c.kind + '\0' + c.entry_name).encode()).hexdigest()
            item = {"role": c.role, "endpoint": c.endpoint, "kind": c.kind, "name": c.entry_name, "requestId": rid, "status": "failed"}
            entries.append(item)
            writing = False
            try:
                value = c.password if c.kind == 'password' else read_ssh_file(Path(c.key_file).absolute(), private_key=True)
                if c.kind == 'private_key':
                    _validate_key(value)
                writing = True
                meta = vault.add(c.entry_name, value, request_id=rid)
                value = None
                item.update(status='saved_unbound', entryId=meta['id'], revision=meta['revision'])
                writing = False
                item['binding'] = dict(host_id=result['host']['id'], enrollment_id=result['enrollment']['id'], role=c.role, endpoint=c.endpoint, kind=c.kind, entry_id=meta['id'], entry_revision=meta['revision'])
                bound = vault.bind_host_credential(**item['binding'])
                item.update(status='saved', bindingId=bound['id'])
            except VaultWriteError:
                # Preserve stable request ID. Caller can reconcile; no blind replay.
                item['status'] = 'unknown'
            except (KeyboardInterrupt, SystemExit):
                if writing:
                    item['status'] = 'unknown'
                return {"status": "cancelled", "entries": entries}
            except Exception:
                pass  # Source/provider diagnostics may include plaintext.
        return {"status": 'saved' if all(e['status'] == 'saved' for e in entries) else 'partial' if any(e['status'] in ('saved', 'saved_unbound') for e in entries) else 'unknown' if any(e['status'] == 'unknown' for e in entries) else 'failed', "entries": entries}
    except (KeyboardInterrupt, SystemExit):
        return {"status": "cancelled", "entries": entries}
    except Exception:
        return {"status": "failed", "entries": entries}
