"""Connection management: the credentials DreamLake holds to reach other systems.

Only a namespace's credential managers can use these calls, with a signed-in
user token; anyone else, and any unknown id, gets ``CONNECTION_NOT_FOUND``.
Machine and service-account tokens are refused by the server.

What can be connected is declared by the server, per deployment. Read it with
:meth:`Connections.connectors` rather than assuming a connector, auth kind or
profile exists.

Secrets travel one way. A ``credential`` you pass is sent once, in the request
body, and never appears in a return value, ``repr`` or exception raised here:
errors carry the server's typed code and message, and the server never echoes
a credential. Every change takes an idempotency key and, where the server
requires one, the settings revision you last read; a stale revision raises
:class:`ConnectionConflict` instead of overwriting someone else's change.

``entry_begin`` hands credential entry to the signed-in web form instead: the
returned ``url`` carries only an opaque operation id, the person types the
secret in the browser, and :meth:`Connections.entry_status` recovers the
Connection id and masked details.
"""
from __future__ import annotations

from typing import Any, Literal, Mapping, Optional
from urllib.parse import quote

Lifecycle = Literal["active", "disabled"]
Intent = Literal["create", "reconnect"]

__all__ = [
    "Connections",
    "ConnectionApiError",
    "ConnectionNotFound",
    "ConnectionConflict",
    "ConnectionInvalid",
    "ConnectionUnavailable",
]


class ConnectionApiError(Exception):
    """A typed Connection API failure.

    ``code`` is the server's stable code (for example ``CONNECTION_VALIDATION_FAILED``),
    ``status`` the HTTP status, ``retryable`` whether repeating the identical
    request can help, ``request_id`` the server's correlation id and ``details``
    its safe structured details. None of them carry a credential.
    """

    def __init__(self, code: str, status: int, message: str = "", *, retryable: bool = False,
                 request_id: Optional[str] = None, details: Optional[dict] = None):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.status = status
        self.message = message
        self.retryable = retryable
        self.request_id = request_id
        self.details = details or {}


class ConnectionNotFound(ConnectionApiError):
    """Unknown id or namespace, or you are not a credential manager there."""


class ConnectionConflict(ConnectionApiError):
    """A revision, label, lifecycle, in-use, idempotency or entry-state conflict (409)."""


class ConnectionInvalid(ConnectionApiError, ValueError):
    """The request or the credential was refused (400/422)."""


class ConnectionUnavailable(ConnectionApiError):
    """The server or the provider could not answer now (5xx); see ``retryable``."""


def _error(response) -> ConnectionApiError:
    status = response.status_code
    code, message, retryable, request_id, details = "CONNECTION_REQUEST_FAILED", "", False, None, None
    try:
        body = response.json()
    except ValueError:
        body = None
    envelope = body.get("error") if isinstance(body, dict) else None
    if isinstance(envelope, dict):
        code = envelope.get("code") if isinstance(envelope.get("code"), str) else code
        message = envelope.get("message") if isinstance(envelope.get("message"), str) else ""
        retryable = envelope.get("retryable") is True
        request_id = envelope.get("requestId") if isinstance(envelope.get("requestId"), str) else None
        details = envelope.get("details") if isinstance(envelope.get("details"), dict) else None
    cls = (ConnectionNotFound if status == 404 else ConnectionConflict if status == 409
           else ConnectionInvalid if status in (400, 422) else ConnectionUnavailable if status >= 500
           else ConnectionApiError)
    return cls(code, status, message, retryable=retryable, request_id=request_id, details=details)


def _key(key: Any) -> str:
    if not isinstance(key, str) or not key.strip() or len(key) > 255:
        raise ValueError("Supply a nonempty idempotency key of at most 255 characters")
    return key


def _revision(value: Any, name: str = "expected_settings_revision") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _object(value: Any, name: str) -> dict:
    if not isinstance(value, Mapping):
        # Never format the value: it may be a credential.
        raise ValueError(f"{name} must be a mapping")
    return dict(value)


def _seg(value: str, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} is required")
    return quote(value, safe="")


class Connections:
    """``client.connections`` — list, create and manage Connections in a namespace."""

    def __init__(self, client):
        self.client = client

    def __repr__(self) -> str:
        return "Connections()"

    # ── transport ────────────────────────────────────────────────────────────

    def _call(self, method: str, path: str, *, json: Any = None, params: Optional[dict] = None,
              key: Optional[str] = None):
        headers = {"Idempotency-Key": key} if key else None
        with self.client.http() as http:
            response = http.request(method, path, json=json, params=params, headers=headers)
        if not response.is_success:
            raise _error(response)
        return None if response.status_code == 204 else response.json()

    @staticmethod
    def _ns(namespace: str) -> str:
        return "/namespaces/" + _seg(namespace, "namespace")

    def _path(self, namespace: str, connection_id: str, suffix: str = "") -> str:
        return self._ns(namespace) + "/connections/" + _seg(connection_id, "connection_id") + suffix

    # ── discovery and reads ──────────────────────────────────────────────────

    def connectors(self, namespace: str) -> list:
        """What this server accepts: connector kinds, auth kinds (``mode`` is
        ``fields``, ``oauth`` or ``none``), the inputs each asks for and where
        they go (``target``: ``credential`` or ``privateConfig``), and product
        or profile choices."""
        return self._call("GET", self._ns(namespace) + "/connection-connectors")["connectors"]

    def list(self, namespace: str, *, limit: Optional[int] = None, cursor: Optional[str] = None,
             label_contains: Optional[str] = None, lifecycle: Optional[Lifecycle] = None) -> dict:
        """One page: ``{"items", "nextCursor", "lifecycleCounts"}``."""
        params = {k: v for k, v in (("limit", limit), ("cursor", cursor),
                                    ("labelContains", label_contains), ("lifecycle", lifecycle)) if v is not None}
        return self._call("GET", self._ns(namespace) + "/connections", params=params or None)

    def get(self, namespace: str, connection_id: str) -> dict:
        """Detail with masked configuration (presence, never stored values)."""
        return self._call("GET", self._path(namespace, connection_id))

    def affected_sources(self, namespace: str, connection_id: str, *, limit: Optional[int] = None,
                         cursor: Optional[str] = None) -> dict:
        """Sources whose external layer uses this Connection."""
        params = {k: v for k, v in (("limit", limit), ("cursor", cursor)) if v is not None}
        return self._call("GET", self._path(namespace, connection_id, "/affected-sources"), params=params or None)

    def check(self, namespace: str, connection_id: str) -> dict:
        """Probe without changing anything: ``status`` is ``available``,
        ``unavailable`` or ``unverified`` (a target-bound credential such as an
        S3 key, until a Source uses it)."""
        return self._call("POST", self._path(namespace, connection_id, ":check"), json={})

    # ── writes ───────────────────────────────────────────────────────────────

    def create(self, namespace: str, *, label: str, connector_kind: str, auth_kind: str,
               idempotency_key: str, credential: Optional[Mapping[str, Any]] = None,
               private_config: Optional[Mapping[str, Any]] = None) -> dict:
        """Create from a credential you supply. Omit ``credential`` only for an
        auth kind whose mode is ``none``; OAuth kinds use :meth:`oauth_begin`."""
        body: dict = {"label": label, "connectorKind": connector_kind, "authKind": auth_kind}
        if credential is not None:
            body["credential"] = _object(credential, "credential")
        if private_config is not None:
            body["privateConfig"] = _object(private_config, "private_config")
        return self._call("POST", self._ns(namespace) + "/connections", json=body, key=_key(idempotency_key))

    def rename(self, namespace: str, connection_id: str, *, label: str, expected_settings_revision: int,
               idempotency_key: str) -> dict:
        return self._call("PATCH", self._path(namespace, connection_id),
                          json={"expectedSettingsRevision": _revision(expected_settings_revision), "label": label},
                          key=_key(idempotency_key))

    def reconnect(self, namespace: str, connection_id: str, *, credential: Mapping[str, Any],
                  expected_settings_revision: int, idempotency_key: str,
                  private_config: Optional[Mapping[str, Any]] = None,
                  enable_after_success: Optional[bool] = None) -> dict:
        """Replace the credential. Omitting ``private_config`` keeps what is stored."""
        body: dict = {"expectedSettingsRevision": _revision(expected_settings_revision),
                      "credential": _object(credential, "credential")}
        if private_config is not None:
            body["privateConfig"] = _object(private_config, "private_config")
        if enable_after_success is not None:
            body["enableAfterSuccess"] = bool(enable_after_success)
        return self._call("POST", self._path(namespace, connection_id, ":reconnect"), json=body,
                          key=_key(idempotency_key))

    def enable(self, namespace: str, connection_id: str, *, expected_settings_revision: int,
               idempotency_key: str) -> dict:
        return self._call("POST", self._path(namespace, connection_id, ":enable"),
                          json={"expectedSettingsRevision": _revision(expected_settings_revision)},
                          key=_key(idempotency_key))

    def disable(self, namespace: str, connection_id: str, *, expected_settings_revision: int,
                idempotency_key: str) -> dict:
        return self._call("POST", self._path(namespace, connection_id, ":disable"),
                          json={"expectedSettingsRevision": _revision(expected_settings_revision)},
                          key=_key(idempotency_key))

    def delete(self, namespace: str, connection_id: str, *, expected_settings_revision: int,
               idempotency_key: str) -> None:
        """Refused with ``CONNECTION_IN_USE`` while a Source still uses it."""
        self._call("DELETE", self._path(namespace, connection_id),
                   json={"expectedSettingsRevision": _revision(expected_settings_revision)},
                   key=_key(idempotency_key))

    # ── OAuth ────────────────────────────────────────────────────────────────

    def oauth_begin(self, namespace: str, *, intent: Intent, connector_kind: str,
                    label: Optional[str] = None, connection_id: Optional[str] = None,
                    expected_settings_revision: Optional[int] = None, oauth_profile: Optional[str] = None,
                    return_to: str = "/settings/connections") -> dict:
        """Start provider sign-in: open ``authorizationUrl`` in a browser, then
        poll :meth:`oauth_status` with ``operationId``."""
        body: dict = {"intent": intent, "connectorKind": connector_kind, "returnTo": return_to}
        if label is not None:
            body["label"] = label
        if connection_id is not None:
            body["connectionId"] = connection_id
        if expected_settings_revision is not None:
            body["expectedSettingsRevision"] = _revision(expected_settings_revision)
        if oauth_profile is not None:
            body["oauthProfile"] = oauth_profile
        return self._call("POST", self._ns(namespace) + "/connection-oauth-operations", json=body)

    def oauth_status(self, namespace: str, operation_id: str) -> dict:
        """``state`` is ``pending``, ``exchanging``, ``succeeded`` (with
        ``connection``) or ``failed`` (with ``error``)."""
        return self._call("GET", self._ns(namespace) + "/connection-oauth-operations/"
                          + _seg(operation_id, "operation_id"))

    # ── browser credential entry ─────────────────────────────────────────────

    def entry_begin(self, namespace: str, *, intent: Intent, connector_kind: Optional[str] = None,
                    auth_kind: Optional[str] = None, connection_id: Optional[str] = None,
                    expected_settings_revision: Optional[int] = None,
                    suggested_label: Optional[str] = None) -> dict:
        """Hand credential entry to the signed-in web form.

        For ``create`` pass ``connector_kind`` and an ``auth_kind`` whose mode
        is ``fields``; ``suggested_label`` is only a prefill. For ``reconnect``
        pass ``connection_id`` (and optionally the revision you read). Give the
        person the returned ``url``; it works only for you, in this namespace,
        once, until ``expiresAt``.
        """
        body: dict = {"intent": intent}
        for wire, value in (("connectorKind", connector_kind), ("authKind", auth_kind),
                            ("connectionId", connection_id), ("suggestedLabel", suggested_label)):
            if value is not None:
                body[wire] = value
        if expected_settings_revision is not None:
            body["expectedSettingsRevision"] = _revision(expected_settings_revision)
        return self._call("POST", self._ns(namespace) + "/connection-entry-operations", json=body)

    def entry_status(self, namespace: str, operation_id: str) -> dict:
        """``state`` is ``pending``, ``succeeded`` (``resultConnectionId`` and
        ``connection``), ``cancelled`` or ``expired``."""
        return self._call("GET", self._ns(namespace) + "/connection-entry-operations/"
                          + _seg(operation_id, "operation_id"))

    def entry_cancel(self, namespace: str, operation_id: str) -> dict:
        """Close a pending entry so its link can no longer be used."""
        return self._call("POST", self._ns(namespace) + "/connection-entry-operations/"
                          + _seg(operation_id, "operation_id") + ":cancel", json={})
