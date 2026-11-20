"""Typed named-tmux references and explicit creation; never shell-execute arguments."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict
from urllib.parse import quote, unquote, urlsplit

import httpx

if TYPE_CHECKING:
    from ._client import DreamLakeClient

_ID = re.compile(r"[a-f0-9]{24}\Z")
_SERVER = re.compile(r"[a-f0-9]{16}\Z")
_NEW_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_UUID = re.compile(r"[a-f0-9]{8}-[a-f0-9]{4}-[1-8][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}\Z")
_CODE = re.compile(r"[a-z][a-z0-9_]{0,95}\Z")
_UNSET = object()


class TmuxError(Exception):
    """Bounded failure code. A failed write response can leave creation uncertain."""

    def __init__(self, code: str, status: int | None = None):
        self.code, self.status = code, status
        super().__init__(f"({status}) {code}" if status is not None else code)


def _match(pattern: re.Pattern, value: Any) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _enrollment(value: str) -> str:
    if not _match(_ID, value):
        raise ValueError("Use an exact 24-character lowercase enrollment ID")
    return value


def _namespace(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise ValueError("Use an explicit namespace slug")
    return value


def _name(value: Any) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 256 and not re.search(r"[\x00-\x1f\x7f-\x9f]", value)


@dataclass(frozen=True)
class TmuxReference:
    enrollment: str
    server: str
    session: str

    def __post_init__(self) -> None:
        _enrollment(self.enrollment)
        if not _match(_SERVER, self.server) or not _name(self.session):
            raise ValueError("Use an exact tmux server and session name")
        try:
            self.session.encode("utf-8", errors="strict")
        except UnicodeError:
            raise ValueError("Tmux session name must be valid Unicode") from None

    def value(self) -> str:
        # Match JavaScript encodeURIComponent, including its literal apostrophes.
        encoded = quote(self.session, safe="~!*'()-._")
        return f"{self.enrollment}/{self.server}/{encoded}"

    def directive(self) -> str:
        return f":tmux[{self.value()}]"

    def as_dict(self) -> dict[str, str]:
        return {"enrollment": self.enrollment, "server": self.server, "session": self.session}


def parse_tmux_reference(value: str) -> TmuxReference:
    """Parse a compact value or directive; never reinterpret legacy host handles."""
    if not isinstance(value, str):
        raise ValueError("Invalid tmux reference")
    if value.startswith(":tmux[") and value.endswith("]"):
        value = value[6:-1]
    parts = value.split("/")
    if len(parts) != 3 or not parts[2] or re.search(r"[\[\]\s]", value) or re.search(r"%(?![0-9a-fA-F]{2})", parts[2]):
        raise ValueError("Invalid tmux reference")
    try:
        name = unquote(parts[2], errors="strict")
    except UnicodeError:
        raise ValueError("Invalid tmux reference encoding") from None
    return TmuxReference(parts[0], parts[1], name)


def tmux_directive(reference: TmuxReference) -> str:
    return reference.directive()


class TmuxTarget(TypedDict):
    namespace: str
    enrollmentId: str
    machineId: str
    username: str


class TmuxResolution(TypedDict):
    version: Literal[1]
    multiplexer_references: Literal[0, 1]
    status: Literal["resolved", "stale", "unavailable"]
    reason: str
    observedAt: str | None
    reference: dict[str, str]
    target: NotRequired[TmuxTarget]


class TmuxOperation(TypedDict):
    version: Literal[1]
    operation: Literal["create"]
    requestId: str
    requestedName: str | None
    status: Literal["pending", "succeeded", "failed"]
    reference: NotRequired[dict[str, str]]
    directive: NotRequired[str]
    target: NotRequired[TmuxTarget]
    error: NotRequired[str]


class TmuxInventory(TypedDict):
    apiVersion: Literal[1]
    hostId: str
    enrollmentId: str
    machineId: str
    unixUser: str
    checkedAt: str
    observedAt: str | None
    ageSeconds: int | None
    status: Literal["unknown", "reported"]
    reason: str
    configuration: dict[str, Any] | None
    snapshot: dict[str, Any] | None


class TmuxTicket(TypedDict):
    ticket: str
    path: Literal["/terminal/stream"]


def _reference(value: TmuxReference | str) -> TmuxReference:
    if isinstance(value, str):
        return parse_tmux_reference(value)
    if not isinstance(value, TmuxReference):
        raise ValueError("Use a TmuxReference or compact directive")
    return value


def _target(value: Any, enrollment: str) -> TmuxTarget:
    if not isinstance(value, dict) or value.get("enrollmentId") != enrollment:
        raise TmuxError("terminal_reference_target_mismatch")
    try:
        namespace = _namespace(value.get("namespace"))
    except ValueError:
        raise TmuxError("terminal_reference_target_invalid") from None
    if not all(isinstance(value.get(k), str) and value[k] for k in ("machineId", "username")):
        raise TmuxError("terminal_reference_target_invalid")
    return {"namespace": namespace, "enrollmentId": enrollment, "machineId": value["machineId"], "username": value["username"]}


def _resolution(value: dict, reference: TmuxReference) -> TmuxResolution:
    if (type(value.get("version")) is not int or value["version"] != 1
        or type(value.get("multiplexer_references")) is not int or value["multiplexer_references"] not in (0, 1)
        or value.get("status") not in ("resolved", "stale", "unavailable")
        or not _match(_CODE, value.get("reason")) or value.get("reference") != reference.as_dict()
        or "observedAt" not in value):
        raise TmuxError("terminal_reference_response_mismatch")
    observed = value["observedAt"]
    if observed is not None:
        try:
            if not isinstance(observed, str):
                raise ValueError()
            datetime.fromisoformat(observed.replace("Z", "+00:00"))
        except ValueError:
            raise TmuxError("terminal_reference_response_invalid") from None
    result: TmuxResolution = {"version": 1, "multiplexer_references": value["multiplexer_references"],
        "status": value["status"], "reason": value["reason"], "observedAt": observed, "reference": reference.as_dict()}
    if result["status"] == "resolved":
        if result["multiplexer_references"] != 1:
            raise TmuxError("terminal_reference_unsupported")
        result["target"] = _target(value.get("target"), reference.enrollment)
    return result


def _operation(value: dict, enrollment: str, request_id: str, expected_name: Any = _UNSET) -> TmuxOperation:
    if (type(value.get("version")) is not int or value["version"] != 1 or value.get("operation") != "create"
        or value.get("requestId") != request_id or value.get("status") not in ("pending", "succeeded", "failed")
        or "requestedName" not in value or (value["requestedName"] is not None and not _match(_NEW_NAME, value["requestedName"]))
        or (expected_name is not _UNSET and value["requestedName"] != expected_name)):
        raise TmuxError("terminal_operation_response_mismatch")
    result: TmuxOperation = {"version": 1, "operation": "create", "requestId": request_id,
        "requestedName": value["requestedName"], "status": value["status"]}
    if result["status"] == "succeeded":
        raw = value.get("reference")
        try:
            if not isinstance(raw, dict):
                raise ValueError()
            reference = TmuxReference(raw.get("enrollment"), raw.get("server"), raw.get("session"))
        except ValueError:
            raise TmuxError("terminal_reference_response_invalid") from None
        if reference.enrollment != enrollment or value.get("directive") != reference.directive():
            raise TmuxError("terminal_reference_response_mismatch")
        result.update(reference=reference.as_dict(), directive=reference.directive(), target=_target(value.get("target"), enrollment))
    elif result["status"] == "failed":
        if not _match(_CODE, value.get("error")):
            raise TmuxError("terminal_operation_response_invalid")
        result["error"] = value["error"]
    return result


def _origin(value: str) -> str:
    if not isinstance(value, str) or re.search(r"[\x00-\x20\x7f-\x9f]", value):
        raise ValueError("Use an explicit HTTPS origin")
    url = urlsplit(value)
    if (url.username or url.password or url.query or url.fragment or url.path not in ("", "/")
        or not url.hostname or not (url.scheme == "https" or (url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1", "::1")))):
        raise ValueError("Use an explicit HTTPS origin; HTTP is only allowed on loopback")
    _ = url.port  # Validate the optional port before constructing an Origin header.
    return f"{url.scheme}://{url.netloc}"


class Tmux:
    """Typed operations over the current account; no automatic retries or shell fallback."""

    def __init__(self, client: DreamLakeClient):
        self.client = client

    def _request(self, method: str, path: str, *, body: dict | None = None,
                 params: dict | None = None, headers: dict | None = None, timeout: float = 30) -> dict[str, Any]:
        try:
            with self.client.http() as http:
                response = http.request(method, path, **({"json": body} if body is not None else {}),
                    params=params, headers=headers, timeout=timeout, follow_redirects=False)
        except httpx.HTTPError:
            raise TmuxError("terminal_transport_unavailable") from None
        try:
            result = response.json()
        except ValueError:
            raise TmuxError("terminal_response_invalid", response.status_code) from None
        if not response.is_success:
            code = result.get("error") if isinstance(result, dict) else None
            if not _match(_CODE, code):
                code = "terminal_request_failed"
            raise TmuxError(code, response.status_code)
        if not isinstance(result, dict):
            raise TmuxError("terminal_response_invalid", response.status_code)
        return result

    def create(self, enrollment: str, *, request_id: str, name: str | None = None) -> TmuxOperation:
        """Create only. Persist request_id beforehand and reuse identical inputs after uncertainty."""
        enrollment = _enrollment(enrollment)
        if not _match(_UUID, request_id) or (name is not None and not _match(_NEW_NAME, name)):
            raise ValueError("Use a lowercase UUID request_id and a name of 1–64 letters, digits, underscores or hyphens")
        body = {"requestId": request_id, **({"name": name} if name is not None else {})}
        return _operation(self._request("POST", f"/terminal-references/{enrollment}/tmux/sessions", body=body), enrollment, request_id, name)

    def operation(self, enrollment: str, request_id: str, *, expected_name: str | None | object = _UNSET, timeout: float = 30) -> TmuxOperation:
        """Read the same operation. This GET cannot enqueue creation or trigger discovery."""
        enrollment = _enrollment(enrollment)
        if not _match(_UUID, request_id) or type(timeout) not in (int, float) or not 0 < timeout <= 60:
            raise ValueError("Use a lowercase UUID request_id and a timeout of at most 60 seconds")
        return _operation(self._request("GET", f"/terminal-references/{enrollment}/tmux/operations/{request_id}", timeout=timeout), enrollment, request_id, expected_name)

    def resolve(self, reference: TmuxReference | str) -> TmuxResolution:
        """Read cached exact-name metadata; never creates, attaches or initiates a scan."""
        ref = _reference(reference)
        return _resolution(self._request("GET", f"/terminal-references/{ref.enrollment}/tmux/{ref.server}", params={"name": ref.session}), ref)

    def list(self, namespace: str, host_id: str, enrollment: str) -> TmuxInventory:
        """Read the public cached multiplexer snapshot for one exact host enrollment."""
        namespace, enrollment = _namespace(namespace), _enrollment(enrollment)
        if not _match(_ID, host_id):
            raise ValueError("Use an exact Host ID")
        result = self._request("GET", f"/namespaces/{namespace}/hosts/{host_id}/enrollments/{enrollment}/multiplexer-sessions")
        if result.get("hostId") != host_id or result.get("enrollmentId") != enrollment:
            raise TmuxError("terminal_inventory_identity_mismatch")
        return result

    def attach_ticket(self, namespace: str, reference: TmuxReference | str, *, origin: str, cols: int = 80, rows: int = 24) -> TmuxTicket:
        """Use a separately gateway-configured client. Attach only; never create a missing name."""
        namespace, ref = _namespace(namespace), _reference(reference)
        origin = _origin(origin)
        _origin(self.client.dl_url)  # Never infer a production gateway for another environment.
        if type(cols) is not int or not 2 <= cols <= 500 or type(rows) is not int or not 2 <= rows <= 200:
            raise ValueError("Invalid terminal dimensions")
        result = self._request("POST", f"/namespaces/{namespace}/terminal/tickets", body={
            "targetId": f"enrollment-{ref.enrollment}", "cols": cols, "rows": rows,
            "launch": {"kind": "tmux-reference", "enrollmentId": ref.enrollment, "serverId": ref.server, "session": ref.session}}, headers={"Origin": origin})
        if not isinstance(result.get("ticket"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", result["ticket"]) or result.get("path") != "/terminal/stream":
            raise TmuxError("terminal_ticket_invalid")
        return {"ticket": result["ticket"], "path": "/terminal/stream"}
