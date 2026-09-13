"""Account-authenticated vault reads. No interactive prompts or implicit exports."""
import json
import re
import shlex
from datetime import datetime

import httpx


_UNSET = object()


class VaultError(RuntimeError):
    """Sanitized vault failure; never contains response bodies or secrets."""


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
    allowed = {"name", "type", "env", "keyName", "fileName", "deleteAt", "purgeAt", "expiresAt", "revision"}
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


class Vault:
    def __init__(self, http_client: httpx.Client):
        self._http = http_client

    def import_entries(self, *, source, prefix, select=None, config=None, store=None,
                       dry_run=False, if_match=None, retry=0, gpg_home=None, decryptor=None):
        """Import explicitly selected SSH items or TOTP records from pass.

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
            if store is not None or gpg_home is not None or decryptor is not None:
                raise VaultError("Pass options cannot be used with SSH import")
            from .vault_import import import_ssh
            return import_ssh(self, prefix=prefix, select=select, config=config,
                              dry_run=dry_run, if_match=if_match, retry=retry)
        if source == "pass-otp":
            if config is not None or if_match is not None or retry != 0:
                raise VaultError("config, if_match and retry cannot be used with pass-OTP import")
            if not dry_run:
                from .pass_import import import_pass_otp
                return import_pass_otp(self, store=store, prefix=prefix, select=select, gpg_home=gpg_home, decryptor=decryptor)
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
                raise VaultError(f"Vault request failed (HTTP {response.status_code})")
            return response.json()
        except VaultError:
            raise
        except Exception:
            raise VaultError("Vault connection or response failed") from None

    def add(self, name, value, *, prefix="", env=None, key_name=None, file_name=None, if_match=None, expires_at=_UNSET):
        """Create only by default; explicit if_match replaces that revision.

        expires_at omitted preserves expiry; None explicitly clears it. Expiry
        stops vault retrieval, not remote access using previously read credentials.
        Never prompts. The value is transmitted as the encrypted-at-rest server's
        request payload, not as URL metadata. Returns metadata only.
        """
        name = _entry_name(name, prefix)
        headers = _revision_headers(if_match)
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
        return _metadata_response(self._request("PUT", "/v1/vault/entry", json=data, headers=headers), name)

    def otp(self, name, *, prefix="", to_json=False):
        """Explicitly retrieve a current TOTP code. Owner-only; never prompts.

        Returns a code string, or JSON containing code and validUntil when
        to_json=True. Treat either result as secret. HOTP is not supported.
        """
        if type(to_json) is not bool:
            raise VaultError("to_json must be a boolean")
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

    def list(self, *, prefix=""):
        """List authorized metadata only, never secret payloads."""
        if prefix:
            resolve_selector("probe", prefix)
        data = self._request("GET", "/v1/vault/entries", params={"prefix": prefix})
        allowed = {"name", "type", "env", "keyName", "fileName", "deleteAt", "purgeAt", "expiresAt", "revision"}
        return [{k: v for k, v in entry.items() if k in allowed} for entry in _entries(data)]

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
            if not entry or entry["type"] not in ("string", "totp"):
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
