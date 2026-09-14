"""Account-authenticated tracked execution; no implicit prompts or process exits."""
from __future__ import annotations

import math
import re
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import quote, urlencode

import httpx

from ._run_source import RunConfigurationError, collect_source

_TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}
_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


class RunError(Exception):
    """Sanitized request error with identity for explicit reconciliation."""

    def __init__(self, message, *, status=None, request_id=None, run_id=None):
        super().__init__(message)
        self.status = status
        self.request_id = request_id
        self.run_id = run_id


class RunAuthorizationError(RunError):
    pass


class RunConflictError(RunError):
    pass


class RunWaitTimeout(RunError, TimeoutError):
    """Local wait expired; the remote run is not cancelled."""


def _text(value, label):
    if not isinstance(value, str) or not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise RunConfigurationError(f"{label} must be nonempty text without control characters")
    return value


def _path(namespace, run_id=None):
    if not isinstance(namespace, str) or not _SEGMENT.fullmatch(namespace):
        raise RunConfigurationError("namespace must be a single namespace name")
    result = f"/namespaces/{quote(namespace, safe='')}/runs"
    if run_id is not None:
        result += "/" + quote(_text(run_id, "run_id"), safe="")
    return result


class Runs:
    """Submit explicit source and inspect durable run state through the main API."""

    def __init__(self, client):
        self._client = client

    def _request(self, method, path, *, body=None, request_id=None, run_id=None):
        if not self._client._token:
            raise RunAuthorizationError("DreamLake authentication is required", status=401,
                                        request_id=request_id, run_id=run_id)
        try:
            with self._client.http() as client:
                response = client.request(method, path, json=body)
            if not response.is_success:
                error = RunAuthorizationError if response.status_code in {401, 403} else RunConflictError if response.status_code == 409 else RunError
                raise error(f"Run API request failed (HTTP {response.status_code})",
                            status=response.status_code, request_id=request_id, run_id=run_id)
            data = response.json()
            if not isinstance(data, dict):
                raise TypeError
            return data
        except (httpx.HTTPError, ValueError, TypeError):
            raise RunError("Run API response unavailable or invalid; reconcile before retrying writes",
                           request_id=request_id, run_id=run_id) from None

    @staticmethod
    def _run(data, *, request_id=None, run_id=None):
        run = data.get("run")
        if not isinstance(run, dict) or not isinstance(run.get("id"), str) or not isinstance(run.get("status"), str):
            raise RunError("Run API returned an invalid run record", request_id=request_id, run_id=run_id)
        return run

    def submit(self, target: str, *, kind: str, argv: Sequence[str],
               include: Sequence[str] = (), cwd: str | Path | None = None,
               request_id: str | None = None, enrollment_id: str | None = None,
               timeout_seconds: int = 3600, placement: Mapping | None = None,
               resources: Mapping | None = None) -> dict:
        """Submit and immediately return a durable run record; call wait explicitly.

        Only include paths are read/uploaded. argv is passed unchanged to uv run
        or uvx on the target. Reuse request_id with identical inputs after an
        uncertain response; there is no automatic write retry.
        """
        if not isinstance(target, str) or len(target.split("/")) != 3 or any(not _SEGMENT.fullmatch(p) for p in target.split("/")):
            raise RunConfigurationError("target must be namespace/group/host")
        if not isinstance(kind, str) or kind not in {"uv-run", "uvx"}:
            raise RunConfigurationError("kind must be uv-run or uvx")
        if isinstance(argv, (str, bytes)) or not isinstance(argv, Sequence) or not argv or any(not isinstance(arg, str) or "\0" in arg for arg in argv):
            raise RunConfigurationError("argv must be a nonempty sequence of strings without NUL")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 86400:
            raise RunConfigurationError("timeout_seconds must be an integer from 1 to 86400")
        request_id = str(uuid.uuid4()) if request_id is None else _text(request_id, "request_id")
        if enrollment_id is not None:
            _text(enrollment_id, "enrollment_id")
        selected = None
        limits = None
        if placement is not None:
            if not isinstance(placement, Mapping) or set(placement) != {"providerId", "associationId", "associationRevision"}:
                raise RunConfigurationError("placement requires providerId, associationId and associationRevision")
            selected = dict(placement)
            for key in ("providerId", "associationId"):
                if not isinstance(selected[key], str) or not re.fullmatch(r"[a-fA-F0-9]{24}", selected[key]):
                    raise RunConfigurationError("Invalid placement ID")
                selected[key] = selected[key].lower()
            revision = selected["associationRevision"]
            if type(revision) is not int or not 1 <= revision <= 9007199254740991 or kind != "uv-run":
                raise RunConfigurationError("Placement requires a positive revision and uv-run")
        if resources is not None:
            if selected is None or not isinstance(resources, Mapping) or set(resources) - {"cpus", "memoryMib", "gpus"}:
                raise RunConfigurationError("resources requires placement and CPU, memory or GPU fields")
            limits = {"cpus": 1, "memoryMib": 512, "gpus": 0, **resources}
            for key, low, high in (("cpus", 1, 65535), ("memoryMib", 1, 4294967295), ("gpus", 0, 65535)):
                if type(limits[key]) is not int or not low <= limits[key] <= high:
                    raise RunConfigurationError("Invalid resource limit")
        body = {"requestId": request_id, "target": target,
                "execution": {"kind": kind, "argv": list(argv)},
                "source": {"files": collect_source(include, cwd)}, "timeoutSeconds": timeout_seconds}
        if selected is not None:
            body["placement"] = selected
        if limits is not None:
            body["resources"] = limits
        if enrollment_id is not None:
            body["enrollmentId"] = enrollment_id
        return self._run(self._request("POST", _path(target.split("/")[0]), body=body,
                                       request_id=request_id), request_id=request_id)

    def status(self, namespace: str, run_id: str) -> dict:
        """Return the persisted run state; connectivity is not terminal success."""
        return self._run(self._request("GET", _path(namespace, run_id), run_id=run_id), run_id=run_id)

    def logs(self, namespace: str, run_id: str, *, cursor: str | None = None,
             limit: int = 65536) -> dict:
        """Return byte-log entries and nextCursor/done; chunks may split UTF-8."""
        path = _path(namespace, run_id)
        if type(limit) is not int or not 1 <= limit <= 262144:
            raise RunConfigurationError("limit must be an integer from 1 to 262144")
        query = {"limit": limit}
        if cursor is not None:
            if not isinstance(cursor, str):
                raise RunConfigurationError("cursor must be a string")
            query["cursor"] = cursor
        return self._request("GET", path + "/logs?" + urlencode(query), run_id=run_id)

    def cancel(self, namespace: str, run_id: str) -> dict:
        """Record cancellation intent; cancel_requested is not proof of stopping."""
        return self._run(self._request("POST", _path(namespace, run_id) + "/cancel",
                                       body={}, run_id=run_id), run_id=run_id)

    def wait(self, namespace: str, run_id: str, *, timeout_seconds: float = 3600,
             poll_seconds: float = 1) -> dict:
        """Wait for a terminal record; local timeout never cancels remote work."""
        for value, label in [(timeout_seconds, "timeout_seconds"), (poll_seconds, "poll_seconds")]:
            if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
                raise RunConfigurationError(f"{label} must be finite and nonnegative")
        if poll_seconds == 0:
            raise RunConfigurationError("poll_seconds must be positive")
        deadline = time.monotonic() + timeout_seconds
        while True:
            run = self.status(namespace, run_id)
            if run["status"] in _TERMINAL:
                return run
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RunWaitTimeout("Local wait expired; inspect the run or explicitly cancel it", run_id=run_id)
            time.sleep(min(poll_seconds, remaining))
