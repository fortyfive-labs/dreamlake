"""Create-only selected TOTP imports; no prompts or automatic write retries."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit
from .pass_store import decrypt_pass
from .pass_otp import parse_otp_uri
from .vault import VaultError, _entry_name, _metadata_response


def read_pass_ciphertext(root, selected):
    source = root / (selected + ".gpg")
    if root.resolve() != root or not root.is_dir() or source.resolve() != source:
        raise VaultError("Unsafe pass source")
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > 1048576:
            raise VaultError("Unsafe pass source")
        data = stream.read(1048577)
        after = source.lstat()
        if len(data) > 1048576 or (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns) or source.resolve() != source:
            raise VaultError("Pass source changed")
        return data


def import_pass_otp(vault, *, store, prefix, select, gpg_home=None, decryptor=None):
    if not store or not prefix:
        raise VaultError("Pass OTP import requires explicit store and prefix")
    _entry_name("probe", prefix)
    if not isinstance(select, list) or not 1 <= len(select) <= 100 or any(not isinstance(p, str) or not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", p) for p in select) or len(set(select)) != len(select):
        raise VaultError("Pass OTP import requires unique canonical select paths (maximum 100)")
    url = urlsplit(str(vault._http.base_url))
    if url.username or url.password or url.query or url.fragment or url.scheme not in ("http", "https") or url.scheme == "http" and url.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise VaultError("Pass OTP import requires HTTPS or loopback HTTP")
    connection = vault._http
    binding = (str(connection.base_url), dict(connection.headers), connection.auth, list(connection.cookies.items()))
    root = Path(os.path.abspath(store))
    entries = [dict(path=p, name=_entry_name(p, prefix), status="not-attempted") for p in select]
    prepared = []
    try:
        for entry in entries:
            data = read_pass_ciphertext(root, entry["path"])
            content = decrypt_pass(data, gpg_home, decryptor)
            if not isinstance(content, str) or len(content) > 1048576:
                raise ValueError()
            lines = [line for line in re.split(r"\r?\n", content) if line.startswith("otpauth://")]
            if len(lines) != 1:
                raise ValueError()
            registration = parse_otp_uri(lines[0])
            if registration.type != "totp":
                raise ValueError()
            prepared.append((hashlib.sha256(data).digest(), json.dumps(registration.reveal())))

        def revalidate(i):
            if vault._http is not connection or (str(connection.base_url), dict(connection.headers), connection.auth, list(connection.cookies.items())) != binding:
                raise VaultError("Vault destination changed during import")
            data = read_pass_ciphertext(root, entries[i]["path"])
            if hashlib.sha256(data).digest() != prepared[i][0]:
                raise VaultError("Pass source changed")

        for i in range(len(entries)):
            revalidate(i)
        for i, entry in enumerate(entries):
            try:
                revalidate(i)
            except Exception:
                entry["status"] = "source-changed"
                break
            try:
                entry["status"] = "unknown"
                # Inspect only status and allowlisted metadata. Never expose HTTP bodies.
                response = vault._http.request("PUT", "/v1/vault/entry", follow_redirects=False,
                                               json=dict(name=entry["name"], type="totp", value=prepared[i][1]))
                if response.status_code == 409:
                    entry["status"] = "conflict"
                elif response.status_code in (400, 401, 403, 404, 405, 413, 422):
                    entry["status"] = "denied"
                elif response.is_success:
                    metadata = _metadata_response(response.json(), entry["name"])
                    if metadata.get("type") != "totp" or metadata.get("revision") != 1:
                        raise ValueError()
                    entry["status"] = "success"
                else:
                    entry["status"] = "unknown"
            except Exception:
                entry["status"] = "unknown"
            if entry["status"] != "success":
                break
        return dict(cancelled=False, uploaded=all(e["status"] == "success" for e in entries), entries=entries)
    except KeyboardInterrupt:
        return dict(cancelled=True, uploaded=False, entries=entries)
    except Exception:
        return dict(cancelled=False, uploaded=False, error="Pass OTP preparation or source revalidation failed; HOTP upload unsupported; no further writes attempted", entries=entries)
    finally:
        prepared.clear()
