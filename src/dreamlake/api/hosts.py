"""Host enrollment against DreamLake account APIs, without terminal prompts.

The packaged remote bootstrap has the same stdin protocol as the standalone CLI.
SSH credentials remain local; bootstrap grants travel only on stdin.
"""
from __future__ import annotations

import base64
import json
import math
import re
import subprocess
import tempfile
import time
import uuid
from collections.abc import Mapping
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx

if TYPE_CHECKING:
    from ._client import DreamLakeClient


class HostError(Exception):
    """Sanitized host failure; status and request_id support explicit recovery."""

    def __init__(self, message: str, *, status: int | None = None,
                 request_id: str | None = None):
        super().__init__(message)
        self.status = status
        self.request_id = request_id


class HostConfigurationError(HostError, ValueError):
    pass


class HostAuthorizationError(HostError):
    pass


class HostConflictError(HostError):
    pass


class HostNotFound(HostError):
    pass


_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_DESTINATION = re.compile(r"(?:[A-Za-z0-9_.-]+@)?[A-Za-z0-9_\[][A-Za-z0-9_.:\[\]-]*\Z")
_OPTIONS = {"BatchMode", "ServerAliveInterval", "ServerAliveCountMax", "ConnectTimeout",
            "IdentitiesOnly", "StrictHostKeyChecking", "UserKnownHostsFile"}


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or re.search(r"[\x00-\x1f\x7f]", value):
        raise HostConfigurationError(f"{label} must be a nonempty string without control characters")
    return value


def _object(value: Any, allowed: set[str], label: str) -> dict:
    if not isinstance(value, Mapping):
        raise HostConfigurationError(f"{label} must be an object")
    if set(value) - allowed:
        raise HostConfigurationError(f"unknown {label} field")
    return dict(value)


def _split_ssh(text: str) -> list[str]:
    # Match the CLI grammar, including escaped newlines, without shell expansion.
    result, token, quote_char, started = [], "", "", False
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\" and quote_char != "'":
            i += 1
            if i == len(text):
                raise HostConfigurationError("SSH arguments have a trailing escape")
            if text[i] != "\n":
                token += text[i]
                started = True
        elif quote_char:
            if c == quote_char:
                quote_char = ""
            else:
                token += c
        elif c in "\"'":
            quote_char, started = c, True
        elif c.isspace():
            if started:
                result.append(token)
                token, started = "", False
        else:
            token += c
            started = True
        i += 1
    if quote_char:
        raise HostConfigurationError("SSH arguments have an unterminated quote")
    if started:
        result.append(token)
    return result


def _ssh_args(value: Any) -> list[str]:
    if isinstance(value, str):
        args = _split_ssh(value)
    else:
        cfg = _object(value, {"host", "user", "port", "identityFile", "jumpHost", "options"}, "ssh")
        args = []
        if "port" in cfg:
            if type(cfg["port"]) is not int:
                raise HostConfigurationError("ssh.port must be an integer")
            args += ["-p", str(cfg["port"])]
        for field, flag in [("identityFile", "-i"), ("jumpHost", "-J"), ("user", "-l")]:
            if field in cfg:
                args += [flag, _string(cfg[field], f"ssh.{field}")]
        if "options" in cfg:
            for key, val in _object(cfg["options"], _OPTIONS, "ssh.options").items():
                args += ["-o", f"{key}={_string(val, 'SSH option value')}"]
        host = _string(cfg.get("host"), "ssh.host")
        if "user" in cfg and "@" in host:
            raise HostConfigurationError("ssh.user conflicts with user@host")
        args.append(host)
    if not args:
        raise HostConfigurationError("SSH requires a destination")
    i = 0
    while i < len(args) - 1:
        flag = args[i]
        if flag not in {"-i", "-J", "-p", "-o", "-l", "-F"} or i + 2 >= len(args):
            raise HostConfigurationError("unsupported SSH arguments; expected options and one destination")
        val = _string(args[i + 1], "SSH option value")
        if val.startswith("-"):
            raise HostConfigurationError("SSH option value cannot start with '-' ")
        if flag == "-p" and (not val.isascii() or not val.isdecimal() or not 1 <= int(val) <= 65535):
            raise HostConfigurationError("SSH port must be an integer from 1 to 65535")
        if flag == "-o" and ("=" not in val or val.split("=", 1)[0] not in _OPTIONS or not val.split("=", 1)[1]):
            raise HostConfigurationError("unsupported SSH option")
        if flag == "-l" and not re.fullmatch(r"[A-Za-z0-9_.-]+", val):
            raise HostConfigurationError("invalid SSH user")
        if flag == "-J" and not re.fullmatch(r"[A-Za-z0-9_@.,:\[\]-]+", val):
            raise HostConfigurationError("invalid SSH jump host")
        i += 2
    if i != len(args) - 1 or not _DESTINATION.fullmatch(args[-1]):
        raise HostConfigurationError("SSH requires one alias, hostname, or user@host and no remote command")
    return args


def _name(name: Any, prefix: Any = None) -> list[str]:
    parts = _string(name, "name").split("/")
    if prefix is not None:
        pref = _string(prefix, "prefix").split("/")
        if len(pref) != 2 or not all(_SEGMENT.fullmatch(p) for p in pref):
            raise HostConfigurationError("prefix must be namespace/group")
        if len(parts) == 1:
            parts = pref + parts
        elif parts[:2] != pref:
            raise HostConfigurationError("name and prefix conflict")
    if len(parts) != 3 or not all(_SEGMENT.fullmatch(p) for p in parts):
        raise HostConfigurationError("name must be namespace/group/host; wildcards are not enrollment names")
    return parts


def _remote(args: list[str], payload: dict) -> dict:
    source = files("dreamlake.api").joinpath("_host_bootstrap.py").read_bytes()
    encoded = base64.b64encode(source).decode("ascii")
    command = f"python3 -c 'import base64;exec(base64.b64decode(\"{encoded}\"))'"
    # First-value-wins OpenSSH options prohibit prompts even if config requests them.
    argv = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "NumberOfPasswordPrompts=0", *args, command]
    try:
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(argv, input=json.dumps(payload).encode(), stdout=output,
                                    stderr=subprocess.DEVNULL, timeout=180, check=False)
            output.seek(0)
            raw = output.read(64001)
        if result.returncode or len(raw) > 64000:
            raise HostError("SSH bootstrap failed; check noninteractive access and target prerequisites")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise TypeError
        return data
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        raise HostError("SSH bootstrap failed; check noninteractive access, Python 3, OpenSSL, and user systemd") from None


class Hosts:
    """Account-authorized host operations. No interactive prompts or vault writes."""

    def __init__(self, client: DreamLakeClient):
        self._client = client

    def plan(self, name: str | None = None, *, prefix: str | None = None,
             ssh: str | Mapping | None = None, config: str | Path | Mapping | None = None) -> dict:
        """Validate configuration without networking, key reads, or target mutation."""
        if isinstance(config, (str, Path)):
            try:
                cfg = json.loads(Path(config).read_text())
            except (OSError, ValueError):
                raise HostConfigurationError("cannot read valid enrollment JSON configuration") from None
        else:
            cfg = {} if config is None else config
        cfg = _object(cfg, {"name", "prefix", "ssh"}, "config")
        parts = _name(name if name is not None else cfg.get("name"),
                      prefix if prefix is not None else cfg.get("prefix"))
        args = _ssh_args(ssh if ssh is not None else cfg.get("ssh"))
        return {"name": "/".join(parts), "namespace": parts[0], "group": parts[1],
                "hostName": parts[2], "ssh": {"executable": "ssh", "args": args}}

    def _request(self, method: str, path: str, *, body: dict | None = None,
                 request_id: str | None = None) -> dict:
        if not self._client._token:
            raise HostAuthorizationError("DreamLake authentication is required", status=401, request_id=request_id)
        try:
            with self._client.http() as client:
                response = client.request(method, path, json=body)
            if not response.is_success:
                cls = HostAuthorizationError if response.status_code in {401, 403} else HostConflictError if response.status_code == 409 else HostNotFound if response.status_code == 404 else HostError
                raise cls(f"Host API request failed (HTTP {response.status_code})", status=response.status_code, request_id=request_id)
            data = response.json()
            if not isinstance(data, dict):
                raise TypeError
            return data
        except (httpx.HTTPError, ValueError, TypeError):
            raise HostError("Host API response unavailable or invalid; reconcile before retrying writes", request_id=request_id) from None

    def status(self, selector: str, *, page: int = 1, page_size: int = 50) -> dict:
        """Inspect one canonical name or namespace/group/*; no credentials returned."""
        if not isinstance(selector, str):
            raise HostConfigurationError("host selector must be a string")
        if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 100:
            raise HostConfigurationError("page must be positive and page_size between 1 and 100")
        wildcard = selector.endswith("/*")
        parts = _name(selector[:-1] + "placeholder" if wildcard else selector)
        prefix = "/".join(parts[:2])
        path = f"/namespaces/{quote(parts[0], safe='')}/hosts"
        current_page = page if wildcard else 1
        while True:
            result = self._request("GET", path + "?prefix=" + quote(prefix, safe="") +
                                   f"&page={current_page}&pageSize={page_size}")
            if wildcard:
                return result
            for host in result.get("hosts", []):
                if host.get("name") == selector:
                    return self._request("GET", path + "/" + quote(host["id"], safe=""))
            total_pages = result.get("totalPages", 1)
            if type(total_pages) is not int or current_page >= total_pages:
                raise HostNotFound("Host not found", status=404)
            current_page += 1

    def enroll(self, name: str | None = None, *, prefix: str | None = None,
               ssh: str | Mapping | None = None, config: str | Path | Mapping | None = None,
               dry_run: bool = False, save_credentials: bool = False,
               request_id: str | None = None, lakeshore_id: str | None = None,
               wait_seconds: float = 60, nymph_version: str = "latest") -> dict:
        """Enroll without saving SSH credentials; return only backend-confirmed readiness.

        Reuse request_id when reconciling an uncertain write. Pending readiness is
        returned separately from online status. Bootstrap grants never escape the API.
        """
        if type(dry_run) is not bool or type(save_credentials) is not bool:
            raise HostConfigurationError("dry_run and save_credentials must be booleans")
        plan = self.plan(name, prefix=prefix, ssh=ssh, config=config)
        if type(wait_seconds) not in {int, float} or not math.isfinite(wait_seconds) or not 0 <= wait_seconds <= 600:
            raise HostConfigurationError("wait_seconds must be between 0 and 600")
        _string(nymph_version, "nymph_version")
        if request_id is not None:
            _string(request_id, "request_id")
        if lakeshore_id is not None:
            _string(lakeshore_id, "lakeshore_id")
        if dry_run:
            return {"status": "validated", "enrolled": False, "authorizationVerified": False,
                    "credentialSaveRequested": bool(save_credentials), "credentialsSaved": False, "plan": plan}
        if save_credentials:
            raise HostConfigurationError("Credential saving is not implemented; enroll without saving")
        request_id = request_id or str(uuid.uuid4())
        path = f"/namespaces/{quote(plan['namespace'], safe='')}/hosts"
        # Authorize namespace access before creating target identity files.
        self._request("GET", path + "?prefix=" + quote(plan["namespace"] + "/" + plan["group"], safe=""), request_id=request_id)
        probe = _remote(plan["ssh"]["args"], {"action": "probe", "name": plan["name"]})
        if not isinstance(probe.get("unixUser"), str) or not probe["unixUser"] or not isinstance(probe.get("publicKey"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", probe["publicKey"]):
            raise HostError("Remote host returned invalid identity", request_id=request_id)
        body = {"name": plan["name"], "unixUser": probe["unixUser"], "publicKey": probe["publicKey"], "requestId": request_id}
        if lakeshore_id is not None:
            body["lakeshoreId"] = lakeshore_id
        receipt = self._request("POST", path + "/enrollments", body=body, request_id=request_id)
        try:
            host, enrollment, bootstrap = receipt["host"], receipt["enrollment"], receipt["bootstrap"]
            if host["name"] != plan["name"]:
                raise ValueError
            for v in (host["id"], enrollment["id"], enrollment["machineId"], bootstrap["controlPlaneUrl"], bootstrap["namespace"]):
                if not isinstance(v, str) or not v:
                    raise ValueError
            operation_id = receipt["operationId"]
        except (KeyError, TypeError, ValueError):
            raise HostError("Host API returned inconsistent enrollment identity", request_id=request_id) from None
        try:
            _remote(plan["ssh"]["args"], {"action": "configure", "name": plan["name"], "publicKey": probe["publicKey"],
                    "controlPlaneUrl": bootstrap["controlPlaneUrl"], "namespace": bootstrap["namespace"],
                    "token": bootstrap.get("token"), "machineId": enrollment["machineId"], "version": nymph_version})
        except HostError:
            raise HostError("Remote configuration failed after host registration; reconcile with the same request_id", request_id=request_id) from None
        deadline = time.monotonic() + wait_seconds
        while True:
            detail = self._request("GET", path + "/" + quote(host["id"], safe=""), request_id=request_id)
            match = next((e for e in detail.get("enrollments", []) if e.get("id") == enrollment["id"]), None)
            online = match is not None and match.get("state") == "online"
            if online or time.monotonic() >= deadline:
                return {"status": "online" if online else "pending", "enrolled": online,
                        "host": {"id": host["id"], "name": host["name"]},
                        "enrollment": match if online else {"id": enrollment["id"], "machineId": enrollment["machineId"]},
                        "operationId": operation_id, "requestId": request_id, "credentialsSaved": False}
            time.sleep(min(1, max(0, deadline - time.monotonic())))
