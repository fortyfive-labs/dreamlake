"""Account-authenticated vault reads. No interactive prompts or implicit exports."""
import json
import re
import shlex

import httpx


class VaultError(RuntimeError):
    """Sanitized vault failure; never contains response bodies or secrets."""


def resolve_selector(name: str, prefix: str = "") -> str:
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
        if any(entry.get(k) is not None and not isinstance(entry[k], str) for k in ("env", "keyName", "fileName")):
            raise VaultError("Invalid vault response")
        value = entry.get("value")
        if secrets and not (isinstance(value, str) or isinstance(value, dict) and all(isinstance(v, str) for v in value.values())):
            raise VaultError("Invalid vault response")
    return data["entries"]


class Vault:
    def __init__(self, http_client: httpx.Client):
        self._http = http_client

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

    def list(self, *, prefix=""):
        """List authorized metadata only, never secret payloads."""
        if prefix:
            resolve_selector("probe", prefix)
        data = self._request("GET", "/v1/vault/entries", params={"prefix": prefix})
        allowed = {"name", "type", "env", "keyName", "fileName", "deleteAt", "purgeAt", "revision"}
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
        data = self._request("POST", "/v1/vault/entries/read", json={"selectors": [s.split("=", 1)[-1] for s in selectors]})
        entries = _entries(data, secrets=True)
        if {e["name"] for e in entries} != {s.split("=", 1)[-1].split(".")[0] for s in selectors}:
            raise VaultError("Invalid vault response")
        selected = []
        for selector in selectors:
            alias, selector = selector.split("=", 1) if "=" in selector else (None, selector)
            path, _, field = selector.partition(".")
            entry = next((e for e in entries if e["name"] == path), None)
            if not entry or entry["type"] != "string":
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
