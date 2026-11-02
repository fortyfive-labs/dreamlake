"""Write a run to a project's managed Dash service (``/api/managed``).

Identity stays with DreamLake: every Dash request carries a token minted by
dreamlake-server's ``POST /managed-experiments/ns/{ns}/projects/{p}/session``
(60 s lifetime, scoped to one project). The writer re-mints it lazily before a
request when fewer than ``refresh_margin`` seconds remain, reusing the workload
credential (``workloadId``) the first session created. Tokens are never logged,
put in URLs, or included in ``repr``/error messages.
"""

from __future__ import annotations

import base64
import datetime as _dt
import mimetypes
import os
import random
import re
import secrets
import threading
import time
import uuid
from urllib.parse import quote, urlsplit

import httpx

from dreamlake.api.experiments import ExperimentRequestError, _error_code

__all__ = ["DashRun", "DashWriterError", "dash_writer"]

_KEY = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_STATUSES = ("RUNNING", "COMPLETED", "FAILED", "CANCELLED")
_LEVELS = {"info": "info", "warn": "warn", "warning": "warn", "error": "error", "debug": "debug",
           "fatal": "fatal", "critical": "fatal"}
_MAX_POINTS = 1000          # ml-dash: dataPoints.length <= 1000 per POST
_MAX_FILE_BYTES = 8 * 1024 * 1024  # ml-dash: decoded upload <= 8 MiB
_MAX_RETRIES = 5
_RETRY_CODES = {"OPERATION_CONFLICT"}


class DashWriterError(ExperimentRequestError):
    """A managed Dash request failed.

    Same shape as :class:`ExperimentRequestError`: ``status`` (HTTP status, or
    None for transport/shape failures), ``code`` (server ``error.code``) and
    ``body`` (parsed response). ``str(error)`` never echoes the body or a token.
    """


_HINTS = {
    "MANAGED_LEGACY_READ_ONLY": (
        "dreamlake-server refuses managed Dash writes while DREAMLAKE_NATIVE_EXPERIMENTS_ENABLED is on. "
        "A separate server switch, DASH_MANAGED_WRITES_ENABLED, is being added to allow them; until it is "
        "deployed, log to the native client (DreamLakeClient().experiments) instead"),
    "MANAGED_SETUP_REQUIRED": "Dash is not set up for this project; pass auto_setup=True (needs WRITE) or run setup in the UI",
    "MANAGED_SETUP_INCOMPLETE": "Dash setup for this project has not finished; retry or pass auto_setup=True",
    "MANAGED_DISABLED": "managed Dash experiments are disabled on this DreamLake server",
    "MANAGED_FORBIDDEN": "this account lacks the project permission (or token scope) for the request",
    "MANAGED_WORKLOAD_REQUIRED": "the Dash token carries no workload credential; open a write session",
}


def _fail(status, code, body, what: str) -> DashWriterError:
    suffix = f", {code}" if code else ""
    hint = _HINTS.get(code or "")
    return DashWriterError(status, f"{what} failed (HTTP {status}{suffix})" + (f": {hint}" if hint else ""),
                           code=code, body=body)


def _segment(name: str, value: str) -> str:
    if not isinstance(value, str) or not value or len(value.encode()) > 256 or any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise ValueError(f"{name} must be a nonempty path segment")
    return quote(value, safe="")


def _jsonable(value):
    if hasattr(value, "item") and callable(value.item):  # numpy / torch scalars
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value


def _flatten(data: dict, prefix: str = "") -> dict:
    out = {}
    for key, value in data.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict) and value:
            out.update(_flatten(value, name + "."))
        else:
            out[name] = _jsonable(value)
    return out


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _default_run_key() -> str:
    return f"run-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}-{secrets.token_hex(3)}"


class _Params:
    def __init__(self, run: "DashRun"):
        self._run = run

    def set(self, **kwargs) -> dict:
        """Merge parameters into the run (nested dicts become dotted keys)."""
        if not kwargs:
            raise ValueError("params.set needs at least one parameter")
        return self._run._call("POST", "/parameters", body={"data": _flatten(kwargs)}, idempotent=True)

    def get(self) -> dict | None:
        return self._run._call("GET", "/parameters", idempotent=True).get("parameters")


class _Metric:
    """``run.metrics(name)``: each logged field goes to metric ``<name>.<field>``."""

    def __init__(self, run: "DashRun", name: str | None):
        self._run, self._name = run, name

    def _metric_name(self, field: str) -> str:
        return f"{self._name}.{field}" if self._name else field

    def log(self, step: int | None = None, **fields) -> "_Metric":
        """Buffer one point per field; a buffer flushes at 1000 points, on ``flush()`` and on exit."""
        if not fields:
            raise ValueError("metrics().log needs at least one field")
        if step is not None and (type(step) is not int or step < 0):
            raise ValueError("step must be a nonnegative integer")
        ts = self._run._clock()
        for field, value in fields.items():
            point = {"step": step} if step is not None else {}
            point.update(value=_jsonable(value), timestamp=ts)
            self._run._buffer(self._metric_name(field), point)
        return self

    def flush(self) -> None:
        self._run.flush()

    def read(self, field: str | None = None, *, limit: int = 1000, cursor: str | None = None) -> dict:
        """Read committed rows of ``<name>.<field>`` (or ``<name>`` when field is None).

        Returns ``{"rows": [dict, ...], "nextCursor": str | None, "hasMore": bool}``.
        The server pages by an opaque cursor over a pinned snapshot (no start index),
        and only rows already committed to storage are visible, so recent points may lag.
        """
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError("limit must be an integer from 1 to 10000")
        name = self._metric_name(field) if field else self._name
        if not name:
            raise ValueError("a metric name or field is required")
        self._run.flush()
        params = {"limit": limit, **({"cursor": cursor} if cursor is not None else {})}
        page = self._run._call("GET", f"/metrics/{quote(name, safe='')}", params=params, idempotent=True)
        rows = []
        for block in page.get("blocks") or []:
            cols = [c.get("name") for c in block.get("columns") or []]
            rows.extend(dict(zip(cols, row)) for row in block.get("rows") or [])
        return {"rows": rows, "nextCursor": page.get("nextCursor"), "hasMore": bool(page.get("hasMore"))}


class _Metrics:
    def __init__(self, run: "DashRun"):
        self._run = run

    def __call__(self, name: str | None = None) -> _Metric:
        if name is not None and (not isinstance(name, str) or not name):
            raise ValueError("metric name must be a nonempty string")
        return _Metric(self._run, name)

    def log(self, step: int | None = None, **fields) -> _Metric:
        return _Metric(self._run, None).log(step, **fields)

    def list(self, *, limit: int = 100, cursor: str | None = None) -> dict:
        params = {"limit": limit, **({"cursor": cursor} if cursor is not None else {})}
        return self._run._call("GET", "/metrics", params=params, idempotent=True)


class _Files:
    """``run.files(prefix)``. The managed API stores files flat (no folders), so a
    non-root ``prefix`` is folded into the stored name: ``a/b`` + ``x.pt`` -> ``a__b__x.pt``."""

    def __init__(self, run: "DashRun", prefix: str | None):
        self._run = run
        parts = [p for p in (prefix or "").split("/") if p]
        self._prefix = "__".join(parts)

    def upload(self, path, *, name: str | None = None, content_type: str | None = None) -> dict:
        base = name if name is not None else os.path.basename(os.fspath(path))
        filename = f"{self._prefix}__{base}" if self._prefix else base
        if not base or len(filename) > 255 or re.search(r"[\x00-\x1f/\\]", filename):
            raise ValueError("file name must be a nonempty single segment of at most 255 characters")
        if os.path.getsize(path) > _MAX_FILE_BYTES:
            raise ValueError("managed Dash accepts files up to 8 MiB")
        with open(path, "rb") as fh:
            content = fh.read()
        body = {"filename": filename, "dataBase64": base64.b64encode(content).decode("ascii"),
                "contentType": content_type or mimetypes.guess_type(base)[0] or "application/octet-stream"}
        return self._run._call("POST", "/files", body=body, idempotent=False)

    def list(self, *, limit: int = 100, cursor: str | None = None) -> dict:
        params = {"limit": limit, **({"cursor": cursor} if cursor is not None else {})}
        return self._run._call("GET", "/files", params=params, idempotent=True)

    def download(self, file_id: str, dest=None) -> bytes:
        _segment("file_id", file_id)
        result = self._run._call("GET", f"/files/{quote(file_id, safe='')}/download", idempotent=True)
        try:
            content = base64.b64decode(result["dataBase64"], validate=True)
        except (KeyError, TypeError, ValueError):
            raise DashWriterError(None, "Dash returned an invalid file download") from None
        if dest is not None:
            with open(dest, "wb") as fh:
                fh.write(content)
        return content


class DashRun:
    """One run in a managed Dash project. Use as a context manager::

        with dash_writer("team", "research") as run:
            run.params.set(lr=3e-4)
            run.metrics("train").log(step=0, loss=1.2)

    Entering sets the run RUNNING; leaving flushes buffers and sets COMPLETED, or
    FAILED on an exception (CANCELLED on KeyboardInterrupt).
    """

    _clock = staticmethod(time.time)
    _sleep = staticmethod(time.sleep)

    def __init__(self, namespace: str, project: str, *, client=None, run_key: str | None = None,
                 refresh_margin: float = 15, auto_setup: bool = False, flush_every: int = _MAX_POINTS):
        if client is None:
            from dreamlake import DreamLakeClient
            client = DreamLakeClient()
        if not 0 <= refresh_margin < 60:
            raise ValueError("refresh_margin must be from 0 to 60 seconds")
        if type(flush_every) is not int or not 1 <= flush_every <= _MAX_POINTS:
            raise ValueError("flush_every must be an integer from 1 to 1000")
        if run_key is not None and (not isinstance(run_key, str) or not _KEY.fullmatch(run_key)):
            raise ValueError("run_key must contain 1-128 letters, digits, underscores or hyphens")
        self._client = client
        self.namespace, self.project = namespace, project
        self._base = f"/managed-experiments/ns/{_segment('namespace', namespace)}/projects/{_segment('project', project)}"
        self.run_key = run_key or _default_run_key()
        self.refresh_margin = refresh_margin
        self.auto_setup = auto_setup
        self._flush_every = flush_every
        self._lock = threading.RLock()
        self._token: str | None = None
        self._expires_at = 0.0
        self._api_url: str | None = None
        self.project_id: str | None = None
        self.workload_id: str | None = None
        self.run_id: str | None = None
        self.experiment: dict | None = None
        self._pending: dict[str, list] = {}
        self._http: httpx.Client | None = None
        self.params = _Params(self)
        self.metrics = _Metrics(self)

    def __repr__(self) -> str:
        return f"DashRun(namespace={self.namespace!r}, project={self.project!r}, run_key={self.run_key!r}, run_id={self.run_id!r})"

    # ── DreamLake control plane ──────────────────────────────────────────

    def _dreamlake(self, method: str, suffix: str, body=None) -> dict:
        if not self._client._token:
            raise DashWriterError(401, "DreamLake authentication is required")
        try:
            with self._client.http() as http:
                response = http.request(method, self._base + suffix, json=body)
        except httpx.HTTPError:
            raise DashWriterError(None, f"DreamLake managed-experiments {suffix} unavailable") from None
        return self._parse(response, f"DreamLake managed-experiments {suffix}")

    @staticmethod
    def _parse(response: httpx.Response, what: str) -> dict:
        try:
            data = response.json()
        except ValueError:
            data = response.text
        if not response.is_success:
            raise _fail(response.status_code, _error_code(data), data, what)
        if not isinstance(data, dict):
            raise DashWriterError(None, f"{what} returned an invalid response")
        return data

    def ensure_setup(self) -> dict:
        """Check Dash setup for the project; run it (plan-hash flow) when ``auto_setup``."""
        status = self._dreamlake("GET", "/status")
        if status.get("status") == "ready":
            return status
        if not self.auto_setup:
            code = "MANAGED_SETUP_REQUIRED" if status.get("status") in (None, "not-enabled") else "MANAGED_SETUP_INCOMPLETE"
            raise _fail(409, code, status, "Dash setup check")
        if not status.get("canSetup"):
            raise DashWriterError(403, "Dash is not set up for this project and this account cannot set it up (needs WRITE)",
                                  code="MANAGED_FORBIDDEN", body=status)
        plan = self._dreamlake("GET", "/setup")
        if not isinstance(plan.get("planHash"), str) or not isinstance(plan.get("revision"), int):
            raise DashWriterError(None, "Dash setup plan was invalid", body=plan)
        result = self._dreamlake("POST", "/setup", {"expectedRevision": plan["revision"], "planHash": plan["planHash"],
                                                    "idempotencyKey": str(uuid.uuid4())})
        if result.get("status") != "ready":
            raise DashWriterError(None, f"Dash setup did not complete (status {result.get('status')!r})", body=result)
        return result

    def _refresh(self, force: bool = False) -> str:
        with self._lock:
            if not force and self._token and self._expires_at - self._clock() > self.refresh_margin:
                return self._token
            body = {"mode": "write", **({"workloadId": self.workload_id} if self.workload_id
                                         else {"idempotencyKey": str(uuid.uuid4())})}
            session = self._dreamlake("POST", "/session", body)
            token, expires, api_url, project_id = (session.get(k) for k in ("token", "expiresAt", "apiUrl", "projectId"))
            if not isinstance(token, str) or not isinstance(expires, (int, float)) or not isinstance(api_url, str) \
                    or not isinstance(project_id, str) or not _KEY.fullmatch(project_id):
                raise DashWriterError(None, "DreamLake returned an invalid Dash session")
            url = urlsplit(api_url)
            loopback = url.hostname in ("localhost", "127.0.0.1", "::1")
            if url.scheme != "https" and not (url.scheme == "http" and loopback):
                raise DashWriterError(None, "DreamLake returned a non-HTTPS Dash API URL; refusing to send a token")
            if self.project_id is not None and project_id != self.project_id:
                raise DashWriterError(409, "Dash session switched projects", code="MANAGED_PROJECT_CHANGED")
            self._token, self._expires_at, self._api_url, self.project_id = token, float(expires), api_url.rstrip("/"), project_id
            self.workload_id = session.get("workloadId") or self.workload_id
            return token

    # ── Dash data plane ──────────────────────────────────────────────────

    def _send(self, method: str, path: str, *, body=None, params=None, idempotent: bool) -> dict:
        """One managed API call with token refresh and bounded, jittered retries.

        Retried: 409 OPERATION_CONFLICT (rolled back server-side), and for idempotent
        requests 503 / transport errors. 401 re-mints the token once (the request was
        refused before it ran). Appends (metrics, logs, files) are never retried on 503.
        """
        attempt, reauthed = 0, False
        while True:
            token = self._refresh()
            if self._http is None:
                self._http = httpx.Client(transport=self._client._transport, timeout=30)
            url = f"{self._api_url}/projects/{quote(self.project_id, safe='')}{path}"
            try:
                response = self._http.request(method, url, json=body, params=params,
                                              headers={"Authorization": f"Bearer {token}"})
            except httpx.HTTPError:
                if idempotent and attempt < _MAX_RETRIES:
                    self._backoff(attempt)
                    attempt += 1
                    continue
                raise DashWriterError(None, "Dash API unavailable; reconcile before retrying writes") from None
            if response.status_code == 401 and not reauthed:
                reauthed = True
                self._refresh(force=True)
                continue
            if not response.is_success and attempt < _MAX_RETRIES:
                try:
                    code = _error_code(response.json())
                except ValueError:
                    code = None
                if (response.status_code == 409 and code in _RETRY_CODES) or (response.status_code == 503 and idempotent):
                    self._backoff(attempt)
                    attempt += 1
                    continue
            return self._parse(response, "Dash API request")

    def _backoff(self, attempt: int) -> None:
        self._sleep(random.uniform(0, min(2.0, 0.1 * 2 ** attempt)))

    def _call(self, method: str, suffix: str, **kw) -> dict:
        if self.run_id is None:
            raise RuntimeError("the Dash run is not open; use `with dash_writer(...) as run` or run.open()")
        return self._send(method, f"/runs/{quote(self.run_id, safe='')}{suffix}", **kw)

    # ── lifecycle ────────────────────────────────────────────────────────

    def open(self) -> "DashRun":
        """Check setup, open a write session, create or reuse the run, set RUNNING."""
        self.ensure_setup()
        self._refresh(force=True)
        created = self._send("PUT", f"/runs/{quote(self.run_key, safe='')}", idempotent=True)
        experiment = created.get("experiment")
        if not isinstance(experiment, dict) or not isinstance(experiment.get("id"), str):
            raise DashWriterError(None, "Dash returned an invalid run")
        self.experiment, self.run_id = experiment, experiment["id"]
        self.set_status("RUNNING")
        return self

    def close(self, status: str = "COMPLETED") -> None:
        """Flush buffers and set a terminal status."""
        try:
            self.flush()
        finally:
            try:
                self.set_status(status)
            finally:
                if self._http is not None:
                    self._http.close()
                    self._http = None

    def __enter__(self) -> "DashRun":
        return self.open()

    def __exit__(self, exc_type, exc, tb) -> bool:
        status = "COMPLETED" if exc_type is None else "CANCELLED" if issubclass(exc_type, KeyboardInterrupt) else "FAILED"
        if exc_type is None:
            self.close(status)
        else:
            try:
                self.close(status)
            except Exception:
                pass  # never mask the caller's exception
        return False

    # ── surface ──────────────────────────────────────────────────────────

    def set_status(self, status: str) -> dict:
        if status not in _STATUSES:
            raise ValueError(f"status must be one of {', '.join(_STATUSES)}")
        result = self._call("POST", "/status", body={"status": status}, idempotent=True)
        if self.experiment is not None:
            self.experiment = {**self.experiment, **result}
        return result

    def log(self, message: str, level: str = "info", **metadata) -> dict:
        lvl = _LEVELS.get(level.lower() if isinstance(level, str) else "")
        if not isinstance(message, str) or lvl is None:
            raise ValueError(f"message must be a string and level one of {', '.join(sorted(set(_LEVELS.values())))}")
        entry = {"timestamp": _now_iso(), "level": lvl, "message": message, **({"metadata": metadata} if metadata else {})}
        return self._call("POST", "/logs", body={"entries": [entry]}, idempotent=False)

    def files(self, prefix: str | None = None) -> _Files:
        return _Files(self, prefix)

    def _buffer(self, metric: str, point: dict) -> None:
        with self._lock:
            pending = self._pending.setdefault(metric, [])
            pending.append(point)
            full = len(pending) >= self._flush_every
        if full:
            self._flush_metric(metric)

    def _flush_metric(self, metric: str) -> None:
        with self._lock:
            points = self._pending.pop(metric, [])
            while points:
                batch, points = points[:_MAX_POINTS], points[_MAX_POINTS:]
                try:
                    self._call("POST", f"/metrics/{quote(metric, safe='')}", body={"dataPoints": batch}, idempotent=False)
                except Exception:
                    # Keep unsent points so a later flush can retry them.
                    self._pending[metric] = batch + points + self._pending.get(metric, [])
                    raise

    def flush(self) -> None:
        """Send every buffered metric point (at most 1000 per request)."""
        with self._lock:
            for metric in list(self._pending):
                self._flush_metric(metric)


def dash_writer(namespace: str, project: str, *, client=None, run_key: str | None = None,
                refresh_margin: float = 15, auto_setup: bool = False, flush_every: int = _MAX_POINTS) -> DashRun:
    """Return a :class:`DashRun` writing to the project's managed Dash service.

    ``client`` is a :class:`dreamlake.DreamLakeClient` (default: one built from the
    saved login / ``DREAMLAKE_API_KEY``). ``run_key`` names the run idempotently
    (default: a timestamped key); reusing a key reopens that run. ``auto_setup``
    runs project Dash setup when it is missing (needs WRITE). Nothing is sent until
    the run is entered (or ``open()`` is called).
    """
    return DashRun(namespace, project, client=client, run_key=run_key, refresh_margin=refresh_margin,
                   auto_setup=auto_setup, flush_every=flush_every)
