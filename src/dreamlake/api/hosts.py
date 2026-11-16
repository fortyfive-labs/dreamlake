"""Host enrollment against DreamLake account APIs, without terminal prompts.

The packaged remote bootstrap has the same stdin protocol as the standalone CLI.
SSH credentials remain local; bootstrap grants travel only on stdin.
"""
from __future__ import annotations

import base64
import copy
import json
import math
import os
import re
import subprocess
import tempfile
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypedDict
from urllib.parse import quote, urlsplit

import httpx

if TYPE_CHECKING:
    from ._client import DreamLakeClient


# Response shapes of the owner-only connection-grant routes. Servers may add
# fields; these name the ones the SDK verifies or documents.
class HostGrantPerson(TypedDict):
    id: str
    username: str | None
    name: str | None


class HostGrantTeam(TypedDict):
    id: str
    slug: str
    name: str | None


class _HostGrantListing(TypedDict, total=False):
    state: Literal["active", "revoked"]
    pendingConnections: int  # revoked only: sessions not yet confirmed closed
    pendingUntil: str        # revoked only: latest expiry of those sessions


class HostUserGrant(_HostGrantListing):
    id: str
    hostId: str
    enrollmentId: str
    unixUser: str
    machineId: str
    grantee: HostGrantPerson
    grantedAt: str


class _HostTeamGrantFlags(_HostGrantListing, total=False):
    # Optional on the wire; a total=False base keeps it optional at runtime too
    # (NotRequired is not seen through postponed annotations in this module).
    inactive: bool  # only present (True) when the team was deleted; listed while sessions are pending


class HostTeamGrant(_HostTeamGrantFlags):
    id: str
    hostId: str
    enrollmentId: str
    unixUser: str
    machineId: str
    team: HostGrantTeam
    grantedAt: str


class HostAccessGrants(TypedDict):
    grants: list[HostUserGrant]
    teamGrants: list[HostTeamGrant]
    teams: list[HostGrantTeam]  # the organization's live teams, for choosing a grantee


class HostRevokeResult(TypedDict, total=False):
    revoked: bool
    id: str
    closedConnections: int
    unconfirmedConnections: int
    unconfirmedUntil: str
    # Sessions kept open because their user still holds another current grant
    # on the enrollment (team-grant servers only).
    stillAuthorizedConnections: int


class HostError(Exception):
    """Sanitized host failure; status and request_id support explicit recovery."""

    def __init__(self, message: str, *, status: int | None = None,
                 request_id: str | None = None, operation_id: str | None = None,
                 retry_at: str | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.request_id = request_id
        self.operation_id = operation_id
        self.retry_at = retry_at
        self.code = code


class HostConfigurationError(HostError, ValueError):
    pass


class HostAuthorizationError(HostError):
    pass


class HostConflictError(HostError):
    pass


class HostNotFound(HostError):
    pass


_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_DESTINATION = re.compile(r"(?:[A-Za-z0-9_.-]+@)?[A-Za-z0-9_\[][A-Za-z0-9_.:\[\]-]*\Z")
_OPTIONS = {"BatchMode", "ServerAliveInterval", "ServerAliveCountMax", "ConnectTimeout",
            "IdentitiesOnly", "StrictHostKeyChecking", "UserKnownHostsFile"}


def _recovery(data: Any) -> dict:
    if not isinstance(data, dict):
        return {}
    safe = {}
    operation = data.get("operationId")
    if isinstance(operation, str) and re.fullmatch(r"[a-fA-F0-9]{24}", operation):
        safe["operation_id"] = operation
    retry = data.get("retryAt")
    if isinstance(retry, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", retry):
        try:
            datetime.fromisoformat(retry)
            safe["retry_at"] = retry
        except ValueError:
            pass
    # Server error codes are fixed snake_case identifiers, never request data.
    code = data.get("code")
    if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code):
        safe["code"] = code
    return safe


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
        if len(parts) <= 2:
            parts = pref + parts
        elif parts[:2] != pref:
            raise HostConfigurationError("name and prefix conflict")
    if len(parts) not in (3, 4) or not all(_SEGMENT.fullmatch(p) for p in parts[:3]) or (len(parts) == 4 and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}\$?", parts[3])):
        raise HostConfigurationError("name must be namespace/group/host[/user]; wildcards are not enrollment names")
    return parts


def _remote(args: list[str], payload: dict) -> dict:
    source = files("dreamlake.api").joinpath("_host_bootstrap.py").read_bytes()
    encoded = base64.b64encode(source).decode("ascii")
    command = f"python3 -c 'import base64;exec(base64.b64decode(\"{encoded}\"))'"
    # Target options do not propagate to ProxyJump. Detach the whole SSH tree
    # from the caller TTY and prohibit askpass so jump hosts cannot prompt either.
    argv = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "NumberOfPasswordPrompts=0", *args, command]
    try:
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(argv, input=json.dumps(payload).encode(), stdout=output,
                                    stderr=subprocess.DEVNULL, timeout=180, check=False,
                                    start_new_session=True,
                                    env={**os.environ, "SSH_ASKPASS_REQUIRE": "never"})
            output.seek(0)
            raw = output.read(64001)
        if len(raw) > 64000:
            raise HostError("SSH bootstrap failed; response exceeded the output limit")
        if result.returncode:
            messages = {
                "openssl_missing": "SSH target needs OpenSSL before enrollment",
                "systemd_user_unavailable": "SSH target has no accessible systemd user manager",
                "linger_unavailable": "SSH target user-linger status could not be checked",
                "linger_required": "SSH target requires user lingering: run loginctl enable-linger for the target account before enrollment",
            }
            try:
                failure = json.loads(raw)
                code = failure.get("code") if isinstance(failure, dict) else None
                message = messages.get(code) if isinstance(code, str) else None
            except (ValueError, TypeError):
                message = None
            raise HostError(message or "SSH bootstrap failed; check noninteractive access and target prerequisites")
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
                response = client.request(method, path, json=body, follow_redirects=False)
            if not response.is_success:
                cls = HostAuthorizationError if response.status_code in {401, 403} else HostConflictError if response.status_code == 409 else HostNotFound if response.status_code == 404 else HostError
                try:
                    recovery = _recovery(response.json())
                except ValueError:
                    recovery = {}
                raise cls(f"Host API request failed (HTTP {response.status_code})", status=response.status_code,
                          request_id=request_id, **recovery)
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
        if type(page) is not int or not 1 <= page <= 100_000 or type(page_size) is not int or not 1 <= page_size <= 100:
            raise HostConfigurationError("page must be between 1 and 100000 and page_size between 1 and 100")
        wildcard = selector.endswith("/*")
        parts = _name(selector[:-1] + "placeholder" if wildcard else selector)
        if wildcard and len(parts) != 3:
            raise HostConfigurationError("wildcards must be namespace/group/*")
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

    def rename(self, namespace: str, host_id: str, *, name: str, expected_name: str) -> dict:
        """Rename an owned host in place; keep identical inputs after an uncertain response."""
        if _name(name)[0] != namespace or _name(expected_name)[0] != namespace:
            raise HostConfigurationError("Rename must stay in the same namespace")
        path = self._move_path(namespace, host_id).removesuffix("/move") + "/rename"
        result = self._request("POST", path, body={"name": name, "expectedName": expected_name})
        if result.get("host", {}).get("id") != host_id or result.get("host", {}).get("name") != name:
            raise HostError("Host rename response could not be verified; reconcile before retrying")
        return result

    @staticmethod
    def _move_path(namespace: str, host_id: str) -> str:
        _string(namespace, "namespace")
        if not isinstance(host_id, str) or not re.fullmatch(r"[a-f0-9]{24}", host_id):
            raise HostConfigurationError("host_id must be an exact lowercase Host ID")
        return f"/namespaces/{quote(namespace, safe='')}/hosts/{host_id}/move"

    @staticmethod
    def _move_response(data: dict, host_id: str, destination_namespace: str, request_id: str | None = None) -> None:
        if (type(data.get("apiVersion")) is not int or data["apiVersion"] != 1
                or data.get("hostId") != host_id or data.get("destinationNamespace") != destination_namespace
                or data.get("visibility") != "private" or type(data.get("revision")) is not int
                or data["revision"] < 0
                or any(not isinstance(data.get(key), str) or not data[key]
                       for key in ("sourceNamespaceId", "destinationNamespaceId", "ownerUserId", "name"))):
            raise HostError("Host move response could not be verified; reconcile before retrying", request_id=request_id)

    def move_preview(self, namespace: str, host_id: str, destination_namespace: str) -> dict:
        """Preview a private, identity-preserving namespace move without changing the host.

        When the Host is filed in Projects, the preview includes
        ``projectPlacements`` (``{"count", "policy": "unfile"}``); the move
        removes those placements and their Bindr tags in the same transaction.
        """
        path = self._move_path(namespace, host_id)
        if not isinstance(destination_namespace, str) or not _SEGMENT.fullmatch(destination_namespace):
            raise HostConfigurationError("destination_namespace must be an exact namespace slug")
        data = self._request("POST", path + "/preview", body={"destinationNamespace": destination_namespace})
        self._move_response(data, host_id, destination_namespace)
        if (not isinstance(data.get("previewHash"), str) or not re.fullmatch(r"[a-f0-9]{64}", data["previewHash"])
                or not isinstance(data.get("blockers"), list)
                or any(not isinstance(item, str) or not item for item in data["blockers"])
                or type(data.get("executable")) is not bool or data["executable"] != (not data["blockers"])):
            raise HostError("Host move preview could not be verified")
        return data

    def move(self, namespace: str, host_id: str, *, destination_namespace: str,
             request_id: str, expected_revision: int, preview_hash: str) -> dict:
        """Apply the reviewed move once. After an uncertain response, replay these exact inputs.

        No key is generated, preview refreshed, or mutation retried automatically.
        A successful receipt preserves the Host ID and confirms private owner access.
        """
        path = self._move_path(namespace, host_id)
        if not isinstance(destination_namespace, str) or not _SEGMENT.fullmatch(destination_namespace):
            raise HostConfigurationError("destination_namespace must be an exact namespace slug")
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", request_id):
            raise HostConfigurationError("request_id must be a stable key of 1 to 128 safe characters")
        if type(expected_revision) is not int or not 0 <= expected_revision < 2147483647:
            raise HostConfigurationError("expected_revision must be a nonnegative integer")
        if not isinstance(preview_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", preview_hash):
            raise HostConfigurationError("preview_hash must be the exact preview SHA-256 hash")
        data = self._request("POST", path, body={"destinationNamespace": destination_namespace,
            "requestId": request_id, "expectedRevision": expected_revision, "previewHash": preview_hash},
            request_id=request_id)
        self._move_response(data, host_id, destination_namespace, request_id)
        if (data.get("requestId") != request_id or data["revision"] != expected_revision + 1
                or not isinstance(data.get("operationId"), str) or not data["operationId"]
                or type(data.get("replayed")) is not bool or not isinstance(data.get("recordedAt"), str)):
            raise HostError("Host move receipt could not be verified; replay the same request", request_id=request_id)
        try:
            datetime.fromisoformat(data["recordedAt"].replace("Z", "+00:00"))
        except ValueError:
            raise HostError("Host move receipt timestamp is invalid", request_id=request_id) from None
        return data

    # Connection grants: the exact Host owner shares one enrollment (one Unix
    # account, with that account's existing OS permissions) with one member of
    # the organization namespace, or with the live direct members of one team.
    # No OS user is created and no Host management, metrics, lifecycle or
    # credential access is shared. Filing a Host in a Project never grants this.

    def _grants_path(self, namespace: str, host_id: str) -> str:
        return self._move_path(namespace, host_id).removesuffix("/move") + "/access-grants"

    @staticmethod
    def _grant_base(grant: Any, host_id: str) -> bool:
        return (isinstance(grant, dict) and grant.get("hostId") == host_id
                and isinstance(grant.get("id"), str) and re.fullmatch(r"[a-f0-9]{64}", grant["id"]) is not None
                and isinstance(grant.get("enrollmentId"), str) and re.fullmatch(r"[a-f0-9]{24}", grant["enrollmentId"]) is not None
                and isinstance(grant.get("unixUser"), str) and bool(grant["unixUser"])
                and isinstance(grant.get("grantedAt"), str))

    @classmethod
    def _grant_view(cls, grant: Any, host_id: str) -> bool:
        grantee = grant.get("grantee") if isinstance(grant, dict) else None
        return (cls._grant_base(grant, host_id)
                and isinstance(grantee, dict) and isinstance(grantee.get("id"), str) and bool(grantee["id"]))

    @staticmethod
    def _team_ref(team: Any) -> bool:
        return (isinstance(team, dict) and isinstance(team.get("id"), str) and bool(team["id"])
                and isinstance(team.get("slug"), str) and bool(team["slug"]))

    @classmethod
    def _team_grant_view(cls, grant: Any, host_id: str) -> bool:
        return cls._grant_base(grant, host_id) and cls._team_ref(grant.get("team"))

    @staticmethod
    def _listed_state(grant: dict) -> bool:
        return grant.get("state") == "active" or (
            grant.get("state") == "revoked" and type(grant.get("pendingConnections")) is int
            and grant["pendingConnections"] >= 1 and isinstance(grant.get("pendingUntil"), str))

    def access_grants(self, namespace: str, host_id: str) -> HostAccessGrants:
        """List everyone granted connection access to an owned Host.

        Owner-only. Returns ``{"grants": [...], "teamGrants": [...], "teams": [...]}``
        as sent by the server: person grants (``grantee``), team grants (``team``
        with ``id``, ``slug``, ``name``) and the organization's live teams that
        can be chosen as a grantee. Each grant names its enrollment and
        ``unixUser``; ``state`` is ``"active"`` or ``"revoked"``. A revoked grant
        stays listed only while connections opened through it are not confirmed
        closed: ``pendingConnections`` and ``pendingUntil`` (their latest
        expiry). An active grant can still be inert (for example the grantee left
        the organization); every use is rechecked.

        A server without team grants raises ``HostError`` with code
        ``host_team_grants_unsupported`` rather than reporting that no team can
        connect.
        """
        data = self._request("GET", self._grants_path(namespace, host_id) + "?grantees=all")
        grants, team_grants, teams = data.get("grants"), data.get("teamGrants"), data.get("teams")
        if isinstance(grants, list) and "teamGrants" not in data and "teams" not in data:
            raise HostError("This DreamLake server does not support team Host grants; "
                            "its grant list cannot show every team or person who can connect",
                            code="host_team_grants_unsupported")
        if (not isinstance(grants, list) or not isinstance(team_grants, list) or not isinstance(teams, list)
                or any(not self._grant_view(g, host_id) or not self._listed_state(g) for g in grants)
                or any(not self._team_grant_view(g, host_id) or not self._listed_state(g) for g in team_grants)
                or any(not self._team_ref(team) for team in teams)):
            raise HostError("Host access grant list could not be verified")
        return data

    def grant_access(self, namespace: str, host_id: str, *, enrollment_id: str,
                     grantee: str | None = None, team: str | None = None) -> dict:
        """Grant connection access to one enrollment of an owned Host.

        Name exactly one recipient: ``grantee``, a DreamLake username (a leading
        ``@`` is accepted) of a current organization member, or ``team``, the
        slug of a live team in the namespace's organization. A team grant
        reaches the team's current and future direct members (not parent or
        child teams), all as the same Unix account, while they remain members.
        The grant covers only ``enrollment_id``'s Unix account and its existing
        OS permissions. An unchanged active grant is returned as is. After an
        uncertain response, call ``access_grants`` to reconcile instead of
        repeating the grant: a repeat also re-activates a grant revoked
        meanwhile, so grant again only as a fresh, explicit decision.

        Returns ``{"grant": {...}}`` for a person or ``{"teamGrant": {...}}`` for
        a team. Personal namespaces, non-members, unknown teams and the owner
        themselves are refused (see ``HostError.code``, e.g. ``team_not_found``).
        A server without team grants refuses ``team`` with HTTP 400 and grants
        nothing.
        """
        path = self._grants_path(namespace, host_id)
        if not isinstance(enrollment_id, str) or not re.fullmatch(r"[a-f0-9]{24}", enrollment_id):
            raise HostConfigurationError("enrollment_id must be an exact lowercase enrollment ID")
        if (grantee is None) == (team is None):
            raise HostConfigurationError("grant to exactly one of grantee (a username) or team (a team slug)")
        if team is not None:
            if not isinstance(team, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", team):
                raise HostConfigurationError("team must be a team slug")
            try:
                data = self._request("POST", path, body={"enrollmentId": enrollment_id, "team": team})
            except HostError as error:
                if error.status != 400:
                    raise
                # Servers without team grants reject the `team` field by schema validation.
                raise HostError("Host team access grant refused (HTTP 400); nothing was granted. "
                                "The server may not support team Host grants",
                                status=400, code=error.code) from None
            grant = data.get("teamGrant")
            if (not self._team_grant_view(grant, host_id) or grant["enrollmentId"] != enrollment_id
                    or grant["team"]["slug"] != team):
                raise HostError("Host team access grant response could not be verified; list grants before retrying")
            return data
        if not isinstance(grantee, str) or not re.fullmatch(r"@?[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", grantee):
            raise HostConfigurationError("grantee must be a DreamLake username")
        data = self._request("POST", path, body={"enrollmentId": enrollment_id, "grantee": grantee})
        grant = data.get("grant")
        if (not self._grant_view(grant, host_id) or grant["enrollmentId"] != enrollment_id
                or grant["grantee"].get("username") != grantee.removeprefix("@")):
            raise HostError("Host access grant response could not be verified; list grants before retrying")
        return data

    def revoke_access(self, namespace: str, host_id: str, grant_id: str) -> HostRevokeResult:
        """Revoke one person or team connection grant. New connections through it are denied at once.

        Returns ``{"revoked": True, "id", "closedConnections",
        "unconfirmedConnections"[, "unconfirmedUntil"][, "stillAuthorizedConnections"]}``.
        Connections whose user still holds another current grant on the same
        enrollment (their own, or another team's) stay open and are counted in
        ``stillAuthorizedConnections``. Others are asked to close; connections
        the control plane did not confirm closed are not reported as closed:
        revoke again to retry closing them, or they end at ``unconfirmedUntil``.
        Idempotent.
        """
        path = self._grants_path(namespace, host_id)
        if not isinstance(grant_id, str) or not re.fullmatch(r"[a-f0-9]{64}", grant_id):
            raise HostConfigurationError("grant_id must be an exact grant ID")
        data = self._request("DELETE", path + "/" + grant_id)
        unconfirmed = data.get("unconfirmedConnections")
        kept = data.get("stillAuthorizedConnections", 0)
        if (data.get("revoked") is not True or data.get("id") != grant_id
                or type(data.get("closedConnections")) is not int or data["closedConnections"] < 0
                or type(unconfirmed) is not int or unconfirmed < 0
                or (unconfirmed > 0) != isinstance(data.get("unconfirmedUntil"), str)
                or type(kept) is not int or kept < 0):
            raise HostError("Host access revoke response could not be verified; revoke again to reconcile")
        return data

    def enroll(self, name: str | None = None, *, prefix: str | None = None,
               ssh: str | Mapping | None = None, config: str | Path | Mapping | None = None,
               dry_run: bool = False, save_credentials: bool = False, credentials=None,
               request_id: str | None = None, lakeshore_id: str | None = None,
               wait_seconds: float = 60, nymph_version: str = "latest") -> dict:
        """Enroll, then optionally save explicitly supplied HostCredential selections.

        Reuse request_id when reconciling an uncertain write. Pending readiness is
        returned separately from online status. Bootstrap grants never escape the API.
        """
        if type(dry_run) is not bool or type(save_credentials) is not bool:
            raise HostConfigurationError("dry_run and save_credentials must be booleans")
        plan = self.plan(name, prefix=prefix, ssh=ssh, config=config)
        if type(wait_seconds) not in {int, float} or not math.isfinite(wait_seconds) or not 0 <= wait_seconds <= 600:
            raise HostConfigurationError("wait_seconds must be between 0 and 600")
        _string(nymph_version, "nymph_version")
        if request_id is not None and (not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", request_id)):
            raise HostConfigurationError("Invalid enrollment request_id")
        if lakeshore_id is not None:
            _string(lakeshore_id, "lakeshore_id")
        if dry_run:
            return {"status": "validated", "enrolled": False, "authorizationVerified": False,
                    "credentialSaveRequested": bool(save_credentials), "credentialsSaved": False, "plan": plan}
        request_id = request_id or str(uuid.uuid4())
        # Pin endpoint/account across asynchronous SSH bootstrap and saving.
        pinned_client = copy.copy(self._client)
        pinned_request = Hosts(pinned_client)._request
        path = f"/namespaces/{quote(plan['namespace'], safe='')}/hosts"
        # Authorize namespace access before creating target identity files.
        pinned_request("GET", path + "?prefix=" + quote(plan["namespace"] + "/" + plan["group"], safe=""), request_id=request_id)
        try:
            probe = _remote(plan["ssh"]["args"], {"action": "probe", "name": plan["name"]})
        except HostError as error:
            error.request_id = request_id
            raise
        if not isinstance(probe.get("unixUser"), str) or not probe["unixUser"] or not isinstance(probe.get("publicKey"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", probe["publicKey"]):
            raise HostError("Remote host returned invalid identity", request_id=request_id)
        if len(plan["name"].split("/")) == 4 and plan["name"].split("/")[3] != probe["unixUser"]:
            raise HostConfigurationError("Host path user must match the enrolled Unix user")
        body = {"name": plan["name"], "unixUser": probe["unixUser"], "publicKey": probe["publicKey"], "requestId": request_id}
        if lakeshore_id is not None:
            body["lakeshoreId"] = lakeshore_id
        receipt = pinned_request("POST", path + "/enrollments", body=body, request_id=request_id)
        try:
            host, enrollment, bootstrap = receipt["host"], receipt["enrollment"], receipt["bootstrap"]
            if host["name"] != plan["name"]:
                raise ValueError
            for v in (host["id"], enrollment["id"], enrollment["machineId"], bootstrap["controlPlaneUrl"], bootstrap["namespace"]):
                if not isinstance(v, str) or not v:
                    raise ValueError
            operation_id = _recovery(receipt).get("operation_id")
            if operation_id is None:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise HostError("Host API returned inconsistent enrollment identity", request_id=request_id) from None
        try:
            url = urlsplit(bootstrap["controlPlaneUrl"])
            expires = datetime.fromisoformat(bootstrap["expiresAt"])
            grant = bootstrap["token"]
            if (url.scheme not in {"http", "https"} or not url.hostname or url.username is not None
                    or url.password is not None or url.query or url.fragment
                    or not _SEGMENT.fullmatch(bootstrap["namespace"])
                    or not isinstance(grant, str) or not 16 <= len(grant) <= 4096
                    or expires.tzinfo is None or expires <= datetime.now(UTC)):
                raise ValueError
            _ = url.port
        except (KeyError, TypeError, ValueError):
            raise HostError("Host API returned an invalid or expired bootstrap grant",
                            request_id=request_id, operation_id=operation_id) from None
        try:
            _remote(plan["ssh"]["args"], {"action": "configure", "name": plan["name"], "publicKey": probe["publicKey"],
                    "controlPlaneUrl": bootstrap["controlPlaneUrl"], "namespace": bootstrap["namespace"],
                    "token": bootstrap.get("token"), "machineId": enrollment["machineId"], "version": nymph_version})
        except HostError:
            raise HostError("Remote configuration failed after host registration; reconcile with the same request_id", request_id=request_id, operation_id=operation_id) from None
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                detail = pinned_request("GET", path + "/" + quote(host["id"], safe=""), request_id=request_id)
            except HostError as error:
                error.operation_id = error.operation_id or operation_id
                raise
            match = next((e for e in detail.get("enrollments", []) if e.get("id") == enrollment["id"]), None)
            online = (match is not None and match.get("state") == "online"
                      and match.get("statusVerified") is True
                      and match.get("machineId") == enrollment["machineId"])
            if online or time.monotonic() >= deadline:
                result = {"status": "online" if online else "pending", "enrolled": online,
                        "host": {"id": host["id"], "name": host["name"]},
                        "enrollment": {"id": enrollment["id"], "machineId": enrollment["machineId"],
                                       **({"state": "online"} if online else {})},
                        "operationId": operation_id, "requestId": request_id, "credentialsSaved": False}
                result["credentials"] = {"status": "not_requested" if not save_credentials else "enrollment_pending", "entries": []}
                if online and save_credentials:
                    from ..host_credentials import save_enrollment_credentials
                    from ..vault import Vault
                    try:
                        with pinned_client.http() as http:
                            result["credentials"] = save_enrollment_credentials(Vault(http), result, credentials, request_id)
                    except Exception:
                        result["credentials"] = {"status": "failed", "entries": []}
                    result["credentialsSaved"] = result["credentials"]["status"] == "saved"
                return result
            time.sleep(min(1, max(0, deadline - time.monotonic())))
