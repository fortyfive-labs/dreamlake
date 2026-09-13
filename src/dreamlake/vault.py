"""Account-authenticated vault reads. No interactive prompts or implicit exports."""
import json
import re
import shlex
from datetime import datetime
from uuid import uuid4

import httpx


_UNSET = object()


class VaultError(RuntimeError):
    """Sanitized vault failure; never contains response bodies or secrets."""


class VaultHttpError(VaultError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"Vault request failed (HTTP {status})")


class VaultWriteError(VaultError):
    """Logical write remains unknown; attempt_outcome describes this HTTP attempt.

    Even a rejected replay may follow a committed write with the same ID.
    Only a matching authenticated committed receipt resolves that uncertainty.
    """
    def __init__(self, request_id, status=None):
        self.request_id = request_id
        self.status = status
        self.outcome = "unknown"
        self.attempt_outcome = (
            "rejected" if status is not None and 400 <= status < 500 and status != 408
            else "unknown"
        )
        detail = f" (HTTP {status})" if status is not None else ""
        super().__init__(f"Vault write {self.outcome}{detail}; reconcile request ID {request_id}")


def _write_request_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise VaultError("Invalid request ID")
    return value


def _write_receipt(data, request_id):
    op = data.get("operation") if isinstance(data, dict) else None
    if not isinstance(op, dict) or op.get("requestId") != request_id or op.get("state") != "committed":
        raise VaultError("Invalid vault write receipt")
    for key in ("committedAt", "retainUntil"):
        if not isinstance(op.get(key), str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", op[key]):
            raise VaultError("Invalid vault write receipt")
    entry = op.get("entry")
    if not isinstance(entry, dict) or type(entry.get("revision")) is not int or entry["revision"] < 1:
        raise VaultError("Invalid vault write receipt")
    return {"requestId": request_id, "state": "committed", "entry": _metadata_response({"entry": entry}, entry.get("name")), "committedAt": op["committedAt"], "retainUntil": op["retainUntil"]}


def resolve_selector(name: str, prefix: str = "") -> str:
    if name.count("=") > 1:
        raise VaultError("Invalid output alias")
    if "=" in name:
        alias, path = name.split("=", 1)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", alias):
            raise VaultError("Invalid output alias")
        return alias + "=" + resolve_selector(path, prefix)
    absolute = name.startswith("/")
    path = name.removeprefix("/")
    if not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*(?:\.[A-Za-z0-9_-]+)?", path):
        raise VaultError("Invalid vault selector")
    if prefix and not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*/?", prefix):
        raise VaultError("Invalid vault prefix")
    return f"{prefix.rstrip('/')}/{path}" if prefix and not absolute else path


def _entries(data, secrets=False):
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise VaultError("Invalid vault response")
    names = set()
    for entry in data["entries"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not isinstance(entry.get("type"), str) or entry["name"] in names:
            raise VaultError("Invalid vault response")
        resolve_selector(entry["name"])
        names.add(entry["name"])
        if "revision" in entry and (type(entry["revision"]) is not int or not 1 <= entry["revision"] <= 9007199254740991):
            raise VaultError("Invalid vault response")
        for key in ("deleteAt", "purgeAt", "expiresAt"):
            if entry.get(key) is not None and (not isinstance(entry[key], str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z", entry[key])):
                raise VaultError("Invalid vault response")
        if any(entry.get(k) is not None and not isinstance(entry[k], str) for k in ("env", "keyName", "fileName")):
            raise VaultError("Invalid vault response")
        value = entry.get("value")
        if secrets and not (isinstance(value, str) or isinstance(value, dict) and all(isinstance(v, str) for v in value.values())):
            raise VaultError("Invalid vault response")
    return data["entries"]


def _entry_name(name, prefix=""):
    resolved = resolve_selector(name, prefix)
    if "." in resolved or "=" in resolved:
        raise VaultError("Expected an entry path, not a field or alias")
    return resolved


def _revision_headers(revision):
    if revision is None:
        return {}
    if type(revision) is not int or revision < 1 or revision > 9007199254740991:
        raise VaultError("Invalid revision")
    return {"If-Match": str(revision)}


def _metadata_response(data, name):
    entries = _entries({"entries": [data.get("entry")]} if isinstance(data, dict) else None)
    if entries[0]["name"] != name:
        raise VaultError("Invalid vault response")
    allowed = {"id", "name", "type", "env", "keyName", "fileName", "deleteAt", "purgeAt", "expiresAt", "revision"}
    return {k: v for k, v in entries[0].items() if k in allowed}


def _validate_secret(value):
    if not isinstance(value, str) and not (isinstance(value, dict) and value and all(isinstance(k, str) and re.fullmatch(r"[A-Za-z0-9_-]+", k) and k not in {"__proto__", "prototype", "constructor"} and isinstance(v, str) for k, v in value.items())):
        raise VaultError("Expected a string or nonempty string field map")
    try:
        size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (ValueError, UnicodeError):
        raise VaultError("Invalid secret encoding") from None
    if size > 65536:
        raise VaultError("Secret value exceeds 64 KiB")


def _host_binding(b):
    if not isinstance(b, dict) or any(not isinstance(b.get(k), str) or not re.fullmatch(r"[0-9a-f]{24}", b[k]) for k in ("hostId", "enrollmentId")) or not isinstance(b.get("entryId"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", b["entryId"]) or type(b.get("entryRevision")) is not int or b["entryRevision"] < 1 or b["entryRevision"] > 9007199254740991 or b.get("role") not in ("target", "jump") or b.get("kind") not in ("password", "private_key") or not isinstance(b.get("endpoint"), str) or not re.fullmatch(r"[A-Za-z0-9_.@:\[\]-]{1,255}", b["endpoint"]):
        raise VaultError("Invalid host credential binding")


class Vault:
    def __init__(self, http_client: httpx.Client):
        self._http = http_client

    def bind_host_credential(self, *, host_id, enrollment_id, role, endpoint, kind, entry_id, entry_revision):
        """Bind an existing exact entry revision to this account's enrollment.

        No SSH connection, installation, or backend delegation. Repeating the
        same slot/ref is idempotent; replacing an existing binding is rejected.
        """
        data = dict(hostId=host_id, enrollmentId=enrollment_id, role=role, endpoint=endpoint,
                    kind=kind, entryId=entry_id, entryRevision=entry_revision)
        _host_binding(data)
        result = self._request("PUT", "/v1/vault/host-credentials", json=data)
        binding = result.get("binding") if isinstance(result, dict) else None
        if not isinstance(binding, dict) or not isinstance(binding.get("id"), str) or any(binding.get(k) != v for k, v in data.items()):
            raise VaultError("Invalid host credential binding response")
        return {k: binding[k] for k in (*data, "id", "createdAt") if k in binding}

    def unbind_host_credential(self, *, binding_id, entry_id, entry_revision):
        """Release a personal retention reference; does not revoke remote SSH access.

        Retiring the entry and its configured retention deadline still govern
        deletion. Retry the same metadata after an uncertain response.
        """
        if not isinstance(binding_id, str) or not re.fullmatch(r"[a-f0-9-]{36}", binding_id) or not isinstance(entry_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", entry_id) or type(entry_revision) is not int or not 1 <= entry_revision <= 9007199254740991:
            raise VaultError("Invalid host credential reference")
        result = self._request("POST", f"/v1/vault/host-credentials/{binding_id}/release", json={"entryId": entry_id, "entryRevision": entry_revision})
        b = result.get("binding") if isinstance(result, dict) else None
        _host_binding(b)
        if b.get("id") != binding_id or b.get("entryId") != entry_id or b.get("entryRevision") != entry_revision or not isinstance(b.get("releasedAt"), str) or not b["releasedAt"] or b.get("remoteAccessRevoked") is not False:
            raise VaultError("Invalid host credential release response")
        allowed = {"id", "hostId", "enrollmentId", "role", "endpoint", "kind", "entryId", "entryRevision", "createdAt", "releasedAt", "remoteAccessRevoked"}
        return {k: v for k, v in b.items() if k in allowed}

    def host_credentials(self, *, host_id, enrollment_id):
        """Account-only binding metadata; does not return secret values."""
        if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{24}", value) for value in (host_id, enrollment_id)):
            raise VaultError("Invalid host credential identity")
        result = self._request("GET", "/v1/vault/host-credentials", params={"hostId": host_id, "enrollmentId": enrollment_id})
        bindings = result.get("bindings") if isinstance(result, dict) else None
        if not isinstance(bindings, list):
            raise VaultError("Invalid host credential binding response")
        allowed = {"id", "hostId", "enrollmentId", "role", "endpoint", "kind", "entryId", "entryRevision", "createdAt", "releasedAt", "status"}
        for binding in bindings:
            _host_binding(binding)
            if not isinstance(binding.get("id"), str):
                raise VaultError("Invalid host credential binding response")
        return [{k: v for k, v in binding.items() if k in allowed} for binding in bindings]

    def import_entries(self, *, source, prefix, select=None, config=None, store=None,
                       dry_run=False, if_match=None, retry=0, gpg_home=None, decryptor=None, hotp_owner=None):
        """Import selected SSH or OTP records. HOTP defaults inactive.

        Never prompts. SSH upload requires select=[item IDs]; dry_run reads no
        private keys and makes no HTTP calls. pass-otp upload requires select paths.
        Unknown writes are reconciled within this call's bounded retry loop only;
        do not blindly invoke a new import after an unknown result.
        """
        if not isinstance(prefix, str) or not prefix:
            raise VaultError("Vault import requires an explicit prefix")
        _entry_name("probe", prefix)
        if type(dry_run) is not bool:
            raise VaultError("dry_run must be a boolean")
        if source == "ssh":
            if store is not None or gpg_home is not None or decryptor is not None or hotp_owner is not None:
                raise VaultError("Pass options cannot be used with SSH import")
            from .vault_import import import_ssh
            return import_ssh(self, prefix=prefix, select=select, config=config,
                              dry_run=dry_run, if_match=if_match, retry=retry)
        if source == "pass-otp":
            if config is not None or if_match is not None or retry != 0:
                raise VaultError("config, if_match and retry cannot be used with pass-OTP import")
            if not dry_run:
                from .pass_import import import_pass_otp
                return import_pass_otp(self, store=store, prefix=prefix, select=select, gpg_home=gpg_home, decryptor=decryptor, hotp_owner=hotp_owner)
            if hotp_owner is not None:
                raise VaultError("hotp_owner is for upload, not preview")
            if select is not None:
                raise VaultError("select is for upload; dry_run previews the explicit store")
            return self.pass_store.sync(store=store, otp=True, dry_run=dry_run, prefix=prefix,
                                        gpg_home=gpg_home, decryptor=decryptor)
        raise VaultError("Choose exactly one supported import source: ssh or pass-otp")

    @property
    def pass_store(self):
        """Read-only, explicitly selected local pass store preview."""
        from .pass_store import PassStore
        return PassStore()

    @property
    def keys(self):
        """Manage scoped retrieval keys with this authenticated session."""
        from .vault_keys import VaultKeys
        return VaultKeys(self)

    def _request(self, method, path, **kwargs):
        try:
            response = self._http.request(method, path, follow_redirects=False, **kwargs)
            if not response.is_success:
                raise VaultHttpError(response.status_code)
            return response.json()
        except VaultError:
            raise
        except Exception:
            raise VaultError("Vault connection or response failed") from None

    def add(self, name, value, *, prefix="", env=None, key_name=None, file_name=None, if_match=None, expires_at=_UNSET, request_id=None):
        """Create only by default; explicit if_match replaces that revision.

        expires_at omitted preserves expiry; None explicitly clears it. Expiry
        stops vault retrieval, not remote access using previously read credentials.
        Never prompts. The value is transmitted as the encrypted-at-rest server's
        request payload, not as URL metadata. Returns metadata plus requestId and
        replayed. Retain an explicit request_id before submission for crash
        recovery; otherwise one is generated. Requires receipt-capable server.
        VaultWriteError.outcome stays unknown on failure, including rejected
        retries; attempt_outcome reports only this attempt's HTTP rejection.
        """
        name = _entry_name(name, prefix)
        request_id = _write_request_id(str(uuid4()) if request_id is None else request_id)
        headers = {**_revision_headers(if_match), "Idempotency-Key": request_id}
        _validate_secret(value)
        data = {"name": name, "type": "string", "value": value}
        if expires_at is not _UNSET:
            if expires_at is not None:
                if not isinstance(expires_at, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z", expires_at):
                    raise VaultError("Invalid expiry; use UTC RFC3339")
                try:
                    datetime.fromisoformat(expires_at)
                except ValueError:
                    raise VaultError("Invalid expiry; use UTC RFC3339") from None
            data["expiresAt"] = expires_at
        for key, item in (("env", env), ("keyName", key_name), ("fileName", file_name)):
            if item is not None:
                data[key] = item
        try:
            result = self._request("PUT", "/v1/vault/entry", json=data, headers=headers)
            receipt = _write_receipt(result, request_id)
            if receipt["entry"]["name"] != name or type(result.get("replayed")) is not bool:
                raise VaultError("Invalid vault write receipt")
            return {**receipt["entry"], "requestId": request_id, "replayed": result["replayed"]}
        except VaultHttpError as error:
            raise VaultWriteError(request_id, error.status) from None
        except Exception:
            raise VaultWriteError(request_id) from None

    def write_status(self, *, request_id):
        """Owner-only committed receipt, without revealing values or requiring KMS.

        Missing/unavailable status does not prove failure. Retry identical input
        and revision with the same ID only within the 30-day retention window.
        """
        request_id = _write_request_id(request_id)
        return _write_receipt(self._request("GET", f"/v1/vault/write-operations/{request_id}"), request_id)

    def otp(self, name, *, prefix="", to_json=False, request_file=None):
        """Retrieve a TOTP code or explicitly issue/replay HOTP. Owner-only; never prompts.

        Returns a code string, or JSON containing code and validUntil when
        to_json=True. HOTP requires request_file and returns a 24-hour recovery
        deadline instead of TOTP validity. Reuse that file after uncertain
        outcomes; never create a fresh intent merely to retry. All code output is secret.
        """
        if type(to_json) is not bool:
            raise VaultError("to_json must be a boolean")
        if request_file is not None:
            from .hotp import retrieve
            result = retrieve(self, _entry_name(name, prefix), request_file)
            return json.dumps(result) if to_json else result["code"]
        result = self._request("POST", "/v1/vault/otp", json={"name": _entry_name(name, prefix)})
        try:
            if not isinstance(result, dict) or not isinstance(result.get("code"), str) or not re.fullmatch(r"[0-9]{6,8}", result["code"]) or not isinstance(result.get("validUntil"), str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", result["validUntil"]):
                raise ValueError()
            from datetime import timezone
            if datetime.fromisoformat(result["validUntil"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                raise ValueError()
        except (ValueError, TypeError):
            raise VaultError("Invalid or expired OTP response") from None
        return json.dumps({"code": result["code"], "validUntil": result["validUntil"]}) if to_json else result["code"]

    def activate_otp(self, name, *, counter_owner, if_match, prefix=""):
        """Explicitly take HOTP authority; generates no code and never prompts."""
        if counter_owner != "dreamlake" or type(if_match) is not int or not 1 <= if_match <= 9007199254740991:
            raise VaultError("Explicit dreamlake owner and current revision required")
        name = _entry_name(name, prefix)
        data = self._request("POST", "/v1/vault/otp/activate", json={"name": name, "expectedRevision": if_match, "counterOwner": counter_owner})
        result = _metadata_response(data, name)
        if result.get("type") != "hotp" or result.get("revision") != if_match + 1:
            raise VaultError("Invalid HOTP activation response")
        return result

    def show(self, name, *, prefix=""):
        """Return metadata, including retirement status, without decrypting."""
        name = _entry_name(name, prefix)
        return _metadata_response(self._request("GET", "/v1/vault/entry", params={"name": name}), name)

    def delete(self, name, *, prefix="", if_match=None):
        """Soft-delete vault access; does not revoke the credential on its host."""
        name = _entry_name(name, prefix)
        return _metadata_response(self._request("DELETE", "/v1/vault/entry", params={"name": name}, headers=_revision_headers(if_match)), name)

    def restore(self, name, *, prefix="", if_match=None):
        """Restore during retention; cannot restore a hard-deleted entry."""
        name = _entry_name(name, prefix)
        return _metadata_response(self._request("POST", "/v1/vault/restore", json={"name": name}, headers=_revision_headers(if_match)), name)

    def list_page(self, *, prefix="", limit=100, cursor=None, include_deleted=False):
        """Read one bounded metadata page; nextCursor is None at completion.

        Cursor traversal is not a snapshot. Names inserted before the previous
        page boundary require a fresh listing; deleted names may disappear.
        """
        if prefix:
            resolve_selector("probe", prefix)
        if type(limit) is not int or not 1 <= limit <= 200 or type(include_deleted) is not bool:
            raise VaultError("Invalid page options")
        if cursor is not None and (not isinstance(cursor, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,2048}", cursor)):
            raise VaultError("Invalid cursor")
        params = {"prefix": prefix, "limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        if include_deleted:
            params["includeDeleted"] = "true"
        data = self._request("GET", "/v1/vault/entries", params=params)
        entries = _entries(data)
        next_cursor = data.get("nextCursor")
        if next_cursor is not None and (not isinstance(next_cursor, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,2048}", next_cursor)):
            raise VaultError("Invalid vault page")
        if "nextCursor" in data and (len(entries) > limit or next_cursor is not None and not entries or any(a["name"] >= b["name"] for a, b in zip(entries, entries[1:]))):
            raise VaultError("Invalid vault page")
        allowed = {"name", "type", "env", "keyName", "fileName", "deleteAt", "purgeAt", "expiresAt", "revision"}
        return {"entries": [{k: v for k, v in entry.items() if k in allowed} for entry in entries], "nextCursor": next_cursor}

    def list(self, *, prefix="", include_deleted=False):
        """Return all authorized metadata, draining bounded pages without reveals."""
        def authority():
            return (id(self._http), id(self._http._transport_for_url(self._http.base_url)), str(self._http.base_url), tuple(self._http.headers.raw),
                    tuple((c.domain, c.path, c.name, c.value) for c in self._http.cookies.jar),
                    self._http.auth, tuple((key, tuple(value)) for key, value in self._http.event_hooks.items()))
        original_authority = authority()
        entries, seen, cursor = [], set(), None
        while True:
            if authority() != original_authority:
                raise VaultError("Vault connection changed during listing")
            page = self.list_page(prefix=prefix, cursor=cursor, include_deleted=include_deleted)
            if entries and page["entries"] and entries[-1]["name"] >= page["entries"][0]["name"]:
                raise VaultError("Non-progressing vault page")
            entries.extend(page["entries"])
            cursor = page["nextCursor"]
            if cursor is None:
                return entries
            if cursor in seen:
                raise VaultError("Repeated vault cursor")
            seen.add(cursor)

    def get(self, name, *, prefix="", to_json=False, to_envs=False, env_prefix=""):
        """Read one selector or compose a list. Explicit formatting returns a string.

        Default output is a string, dictionary, or composed dictionary. to_envs
        produces POSIX shell exports, but never evaluates them or changes os.environ.
        """
        if to_json and to_envs:
            raise VaultError("Choose to_json or to_envs")
        if env_prefix and not to_envs:
            raise VaultError("env_prefix requires to_envs")
        names = [name] if isinstance(name, str) else name
        if not names:
            raise VaultError("At least one selector is required")
        selectors = [resolve_selector(n, prefix) for n in names]
        if env_prefix and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_prefix):
            raise VaultError("Invalid environment prefix")
        if len(set(selectors)) != len(selectors):
            raise VaultError("Colliding output keys")
        known = set()
        for selector in selectors:
            alias, path = selector.split("=", 1) if "=" in selector else (None, selector)
            _, _, field = path.partition(".")
            key = alias or field
            if not key:
                continue
            if to_envs and not alias:
                key = key.replace("-", "_").upper()
            if key in known:
                raise VaultError("Colliding output keys")
            known.add(key)
        data = self._request("POST", "/v1/vault/entries/read", json={"selectors": [s.split("=", 1)[-1] for s in selectors]})
        entries = _entries(data, secrets=True)
        if {e["name"] for e in entries} != {s.split("=", 1)[-1].split(".")[0] for s in selectors}:
            raise VaultError("Invalid vault response")
        selected = []
        for selector in selectors:
            alias, selector = selector.split("=", 1) if "=" in selector else (None, selector)
            path, _, field = selector.partition(".")
            entry = next((e for e in entries if e["name"] == path), None)
            if not entry or entry["type"] not in ("string", "totp", "hotp"):
                raise VaultError("Entry unavailable or unsupported type")
            value = entry["value"]
            if field:
                if not isinstance(value, dict) or field not in value:
                    raise VaultError("Invalid or missing secret value")
                value = value[field]
            if not isinstance(value, str) and not (isinstance(value, dict) and all(isinstance(v, str) for v in value.values())):
                raise VaultError("Invalid or missing secret value")
            selected.append((alias or field or entry.get("keyName") or path, alias or field or entry.get("env") or entry.get("keyName") or path.rsplit("/", 1)[-1], value, bool(alias or (not field and entry.get("env")))))
        if to_envs:
            envs = {}
            for _, env, value, explicit_env in selected:
                for key, val in ({env: value} if isinstance(value, str) else value).items():
                    key = env_prefix + (key if isinstance(value, str) and explicit_env else key.replace("-", "_").upper())
                    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or key in envs or "\0" in val:
                        raise VaultError("Invalid or colliding environment name/value")
                    envs[key] = val
            return "".join(f"export {key}={shlex.quote(value)}\n" for key, value in envs.items())
        if len(selected) == 1:
            result = selected[0][2]
        else:
            result = {}
            for key, _, value, _ in selected:
                if key in result:
                    raise VaultError("Colliding output keys")
                result[key] = value
        return json.dumps(result, indent=2, ensure_ascii=False) + "\n" if to_json else result
