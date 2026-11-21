"""Shared CLI/SDK authentication profiles and account-bound namespace defaults.

A profile selects credentials and a server. A default namespace selects where
implicit resource paths go; it never changes the authenticated account.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Callable

import httpx
import yaml

from .exceptions import NotAuthenticatedError

BUILTIN_ENVIRONMENTS = {
    "prod": {"server": "https://api.dreamlake.ai", "auth": "https://auth.vuer.ai",
             "bss": "https://bs-66d8a7267cdf47e1809553f1b0c00edf.ecs.us-east-1.on.aws"},
    "staging": {"server": "https://staging-api.dreamlake.ai", "auth": "https://staging-auth.vuer.ai",
                "bss": "https://bs-0b713023d6574ac2936eb1e9cbe9cfaa.ecs.us-east-1.on.aws"},
}


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) if path.suffix == ".yml" else json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"Expected an object in {path}")
    return data


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(yaml.safe_dump(data, sort_keys=False) if path.suffix == ".yml" else json.dumps(data, indent=2) + "\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def bound_namespace(token: str, remote: str, config_dir: Path | None = None) -> str | None:
    """Return a saved default only when both server and credentials still match."""
    if os.environ.get("DREAMLAKE_IDENTITY") == "machine":
        return None
    if config_dir is None:
        from dreamlake.config import config
        config_dir = config.config_dir
    try:
        binding = _read(Path(config_dir) / "config.json").get("default_namespace")
    except (ValueError, OSError):
        return None
    if not isinstance(binding, dict) or not isinstance(binding.get("remote"), str):
        return None
    namespace = binding.get("namespace")
    if (isinstance(namespace, str) and namespace
            and binding["remote"].rstrip("/") == remote.rstrip("/")
            and binding.get("tokenSha256") == hashlib.sha256(token.encode()).hexdigest()):
        return namespace
    return None


class Auth:
    """Manage the same local profiles as ``dreamlake auth``.

    Status and profile listings never include tokens. Persistent operations are
    refused under environment overrides and machine identity. ``transport`` is
    an optional caller-owned httpx transport, useful for controlled clients.
    """

    def __init__(self, config_dir: str | Path | None = None, *, transport: httpx.BaseTransport | None = None):
        self.config_dir = Path(config_dir) if config_dir is not None else Path.home() / ".dreamlake"
        self.transport = transport

    def _store(self) -> dict:
        data = _read(self.config_dir / "auth.yml")
        data.setdefault("envs", {})
        return data

    def _guard(self) -> None:
        if os.environ.get("DREAMLAKE_IDENTITY") == "machine":
            raise ValueError("Machine identity cannot change saved user profiles or namespace defaults.")
        if any(os.environ.get(key) for key in ("DREAMLAKE_API_KEY", "DREAMLAKE_REMOTE")):
            raise ValueError("Unset DREAMLAKE_API_KEY and DREAMLAKE_REMOTE before changing saved authentication.")

    def _profile(self, name: str | None = None) -> tuple[str, dict]:
        store = self._store()
        name = name or store.get("current")
        profile = store["envs"].get(name)
        if not profile:
            raise NotAuthenticatedError("No saved profile selected. Run 'dreamlake auth login'.")
        return name, profile

    def token(self, profile: str | None = None) -> str:
        """Return a credential explicitly. Never include this value in logs."""
        if profile is None and os.environ.get("DREAMLAKE_API_KEY"):
            return os.environ["DREAMLAKE_API_KEY"]
        if profile is not None:
            value = self._profile(profile)[1].get("token")
        else:
            value = _read(self.config_dir / "tokens.json").get("dreamlake-token")
            if not value and self.config_dir == Path.home() / ".dreamlake":
                from .token_storage import get_token_storage
                value = get_token_storage(self.config_dir).load("dreamlake-token")
        if not value:
            raise NotAuthenticatedError("Not authenticated. Run 'dreamlake auth login'.")
        return value

    def _context(self, profile: str | None = None) -> tuple[str, str]:
        from dreamlake.config import DEFAULT_REMOTE_URL
        saved = self._profile(profile)[1] if profile else _read(self.config_dir / "config.json")
        remote = saved.get("server") or saved.get("remote_url") or DEFAULT_REMOTE_URL
        if profile is None:
            remote = os.environ.get("DREAMLAKE_REMOTE") or remote
        return self.token(profile), remote.rstrip("/")

    def _request(self, method: str, path: str, token: str, remote: str, **kwargs) -> dict:
        from dreamlake.api._client import _BorrowedTransport
        transport = _BorrowedTransport(self.transport) if self.transport is not None else None
        with httpx.Client(transport=transport, timeout=10) as client:
            response = client.request(method, remote.rstrip("/") + path,
                                      headers={"Authorization": f"Bearer {token}"}, **kwargs)
        response.raise_for_status()
        return response.json()

    def profiles(self) -> list[dict]:
        """List safe profile metadata; active means the effective connection matches."""
        store = self._store()
        try:
            token, remote = self._context()
        except NotAuthenticatedError:
            token, remote = None, None
        machine = os.environ.get("DREAMLAKE_IDENTITY") == "machine"
        return [{"profile": name,
                 "active": not machine and name == store.get("current")
                 and value.get("token") == token and value.get("server", "").rstrip("/") == remote,
                 **{key: value.get(key) for key in ("server", "account", "environment", "namespace")},
                 "defaultNamespace": value.get("defaultNamespace") or value.get("namespace"),
                 "authenticated": bool(value.get("token"))}
                for name, value in store["envs"].items()]

    def status(self, *, verify: bool = False) -> dict:
        """Report local identity context; ``verified`` distinguishes server validation."""
        profiles = self.profiles()
        active = next((entry for entry in profiles if entry["active"]), None)
        overrides = [key for key in ("DREAMLAKE_API_KEY", "DREAMLAKE_REMOTE") if os.environ.get(key)]
        result = {"profile": active["profile"] if active else None, "profiles": profiles,
                  "source": "environment" if overrides else "saved", "overrides": overrides,
                  "verified": False,
                  "machine": os.environ.get("DREAMLAKE_IDENTITY") == "machine",
                  "account": active.get("account") if active else None,
                  "namespace": active.get("namespace") if active else None,
                  "defaultNamespace": None}
        try:
            token, remote = self._context()
        except NotAuthenticatedError:
            return {**result, "authenticated": False}
        selected = bound_namespace(token, remote, self.config_dir)
        result.update(authenticated=True, server=remote,
                      defaultNamespace=selected or result["namespace"])
        if verify:
            me = self._request("GET", "/auth/me", token, remote)
            result["account"] = me.get("username") or me.get("email") or me.get("sub") or me.get("id")
            result["namespace"] = (me.get("namespace") or {}).get("slug")
            if not result["namespace"]:
                raise ValueError("Server returned no personal namespace; authentication was not verified.")
            result["defaultNamespace"] = selected or result["namespace"]
            result["verified"] = True
        return result

    def _project(self, name: str | None, entry: dict | None, store: dict) -> None:
        config = _read(self.config_dir / "config.json")
        tokens = _read(self.config_dir / "tokens.json")
        config.pop("default_namespace", None)
        if entry:
            config.update(remote_url=entry["server"], namespace=entry.get("namespace"))
            if entry.get("bss"):
                config["bss_url"] = entry["bss"]
            else:
                config.pop("bss_url", None)
            tokens["dreamlake-token"] = entry["token"]
            if entry.get("defaultNamespace"):
                config["default_namespace"] = {
                    "namespace": entry["defaultNamespace"], "remote": entry["server"].rstrip("/"),
                    "tokenSha256": hashlib.sha256(entry["token"].encode()).hexdigest()}
        else:
            for key in ("remote_url", "namespace", "bss_url"):
                config.pop(key, None)
            tokens.pop("dreamlake-token", None)
        store["current"] = name
        _write(self.config_dir / "auth.yml", store)
        _write(self.config_dir / "config.json", config)
        _write(self.config_dir / "tokens.json", tokens)
        # Refresh singleton consumers in this process without touching another home.
        from dreamlake.config import config as singleton
        if singleton.config_dir == self.config_dir:
            singleton._load()

    def login(self, token: str, *, profile: str = "prod", server: str | None = None,
              environment: str | None = None, activate: bool = True) -> dict:
        """Validate a token before saving; existing unrelated profiles survive."""
        self._guard()
        if not profile or profile.strip() != profile:
            raise ValueError("Profile name must be nonempty without surrounding whitespace.")
        from dreamlake.config import DEFAULT_REMOTE_URL
        store = self._store()
        old = store["envs"].get(profile, {})
        builtin = BUILTIN_ENVIRONMENTS.get(environment) if environment else None
        if environment and not builtin and not server:
            raise ValueError("Unknown environment; provide server= for a custom deployment.")
        server = (server or (builtin or {}).get("server") or old.get("server") or DEFAULT_REMOTE_URL).rstrip("/")
        me = self._request("GET", "/auth/me", token, server)
        namespace = (me.get("namespace") or {}).get("slug")
        if not namespace:
            raise ValueError("Server returned no personal namespace; no credentials were saved.")
        entry = {"server": server, "namespace": namespace, "token": token,
                 "account": me.get("username") or me.get("email") or me.get("sub") or me.get("id")}
        deployment = next((value for value in BUILTIN_ENVIRONMENTS.values() if value["server"] == server), None)
        if deployment:
            entry.update(deployment)
        elif old.get("server", "").rstrip("/") == server:
            for key in ("auth", "bss"):
                if old.get(key):
                    entry[key] = old[key]
        if environment is not None:
            entry["environment"] = environment
        elif old.get("environment") and old.get("server", "").rstrip("/") == server:
            entry["environment"] = old["environment"]
        if old.get("server", "").rstrip("/") == server and old.get("namespace") == namespace:
            if old.get("defaultNamespace"):
                entry["defaultNamespace"] = old["defaultNamespace"]
        store["envs"][profile] = entry
        if activate or store.get("current") == profile:
            self._project(profile, entry, store)
        else:
            _write(self.config_dir / "auth.yml", store)
        return next(row for row in self.profiles() if row["profile"] == profile)

    def switch(self, profile: str) -> dict:
        """Select a saved profile and restore its namespace default."""
        self._guard()
        name, entry = self._profile(profile)
        if not entry.get("token"):
            raise NotAuthenticatedError(f"Profile {name!r} has no token. Log in again.")
        self._project(name, entry, self._store())
        return self.status()

    def logout(self, profile: str | None = None) -> None:
        """Remove local credentials only; this does not revoke server tokens."""
        self._guard()
        name, _ = self._profile(profile)
        store = self._store()
        del store["envs"][name]
        if store.get("current") == name:
            # Remove legacy credentials before projecting a successor: the
            # plaintext fallback shares the active projection's token slot.
            if self.config_dir == Path.home() / ".dreamlake":
                from .token_storage import get_token_storage
                get_token_storage(self.config_dir).delete("dreamlake-token")
            successor = next(iter(store["envs"]), None)
            self._project(successor, store["envs"].get(successor), store)
        else:
            _write(self.config_dir / "auth.yml", store)

    def refresh(self, *, profile: str | None = None, token: str | None = None,
                on_device_code: Callable | None = None) -> dict:
        """Replace credentials, or reauthorize via device flow using a callback.

        ``on_device_code(response)`` must display/open its verification URI.
        There is no refresh-token grant or arbitrary scope escalation.
        """
        self._guard()
        name, entry = self._profile(profile)
        if token is None:
            if on_device_code is None:
                raise ValueError("Provide token= or on_device_code= to reauthorize with device flow.")
            from .device_flow import DeviceFlowClient
            from .device_secret import generate_device_secret
            flow = DeviceFlowClient(generate_device_secret(), entry["server"], auth_url=entry.get("auth"))
            on_device_code(flow.start_device_flow())
            token = flow.exchange_token(flow.poll_for_token())
        return self.login(token, profile=name, server=entry["server"],
                          activate=name == self._store().get("current"))

    def namespaces(self, *, profile: str | None = None) -> list[dict]:
        """List the personal namespace and organizations the server authorizes."""
        token, remote = self._context(profile)
        me = self._request("GET", "/auth/me", token, remote)
        data = self._request("POST", "/graphql", token, remote,
                             json={"query": "query { myOrganizations { slug name myRole } }"})
        if data.get("errors"):
            raise RuntimeError("Unable to verify organization membership; namespace default was not changed.")
        result = []
        personal = (me.get("namespace") or {}).get("slug")
        if personal:
            result.append({"slug": personal, "kind": "personal"})
        for org in (data.get("data") or {}).get("myOrganizations", []):
            result.append({"slug": org["slug"], "kind": "organization",
                           "name": org.get("name"), "role": org.get("myRole")})
        return result

    def set_default_namespace(self, namespace: str, *, profile: str | None = None) -> dict:
        """Verify current membership before persisting a profile's default."""
        self._guard()
        name, entry = self._profile(profile)
        if namespace not in {row["slug"] for row in self.namespaces(profile=name)}:
            raise ValueError(f"Namespace {namespace!r} is not available to this account; default unchanged.")
        entry["defaultNamespace"] = namespace
        return self._save_default(name, entry)

    def clear_default_namespace(self, *, profile: str | None = None) -> dict:
        """Restore personal namespace fallback for a saved profile."""
        self._guard()
        name, entry = self._profile(profile)
        entry.pop("defaultNamespace", None)
        return self._save_default(name, entry)

    def _save_default(self, name: str, entry: dict) -> dict:
        store = self._store()
        store["envs"][name] = entry
        if store.get("current") == name:
            self._project(name, entry, store)
        else:
            _write(self.config_dir / "auth.yml", store)
        return next(row for row in self.profiles() if row["profile"] == name)
