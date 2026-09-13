"""Create-only selected TOTP imports; no prompts or automatic write retries."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

import httpx
from .pass_store import decrypt_pass
from .pass_otp import parse_otp_uri
from .vault import VaultError, _entry_name, _metadata_response


def _cookie_binding(cookies):
    # Preserve domain/path/secure/expiry attributes and duplicate cookie names.
    return tuple(copy.deepcopy(vars(cookie)) for cookie in cookies.jar)


class _BorrowedTransport(httpx.BaseTransport):
    """Use the pinned route without closing the caller-owned connection pool."""

    def __init__(self, transport):
        self.transport = transport

    def handle_request(self, request):
        return self.transport.handle_request(request)


def _send_prepared(transport, request):
    # A separate client cannot consult the source client's mutable auth, hooks,
    # cookie jar, redirects or base URL. Cookies are already in request headers;
    # response cookies stay in this short-lived dispatcher and cannot affect the
    # next selected entry. The transport remains owned by the source client.
    with httpx.Client(transport=_BorrowedTransport(transport), trust_env=False) as dispatcher:
        return dispatcher.send(request, auth=None, follow_redirects=False)


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
    connection = vault._http
    cookies = httpx.Cookies()
    for cookie in connection.cookies.jar:
        cookies.jar.set_cookie(copy.deepcopy(cookie))
    binding = (str(connection.base_url), tuple(connection.headers.multi_items()),
               connection.auth, _cookie_binding(cookies))
    url = urlsplit(binding[0])
    if url.username or url.password or url.query or url.fragment or url.scheme not in ("http", "https") or url.scheme == "http" and url.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise VaultError("Pass OTP import requires HTTPS or loopback HTTP")
    # Auth objects and hooks can mutate destinations/credentials at send time.
    # Arbitrary callables cannot be safely snapshotted: fail before decryption.
    if connection.auth is not None or any(connection.event_hooks.values()):
        raise VaultError("Pass OTP import requires header authentication and no client hooks")
    # Build only from captured configuration, never via the mutable client.
    base_url = httpx.URL(binding[0])
    destination = base_url.copy_with(raw_path=base_url.raw_path + b"v1/vault/entry")
    template = httpx.Request("PUT", destination, headers=binding[1],
                             cookies=cookies, params=connection.params)
    destination = template.url
    headers = tuple((k, v) for k, v in template.headers.multi_items()
                    if k.lower() not in ("content-length", "transfer-encoding"))
    timeout = connection.timeout.as_dict()
    # httpx has no public route-selection API. Capture the selected transport
    # once, preserving caller TLS/proxy/MockTransport configuration without
    # consulting mutable client mounts again at dispatch.
    transport = connection._transport_for_url(destination)
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
            request = httpx.Request("PUT", destination, headers=headers,
                                    extensions={"timeout": dict(timeout)},
                                    json=dict(name=entry["name"], type="totp",
                                              value=json.dumps(registration.reveal())))
            prepared.append((hashlib.sha256(data).digest(), request))

        def revalidate(i):
            if vault._http is not connection or (str(connection.base_url), tuple(connection.headers.multi_items()), connection.auth, _cookie_binding(connection.cookies)) != binding:
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
                response = _send_prepared(transport, prepared[i][1])
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
