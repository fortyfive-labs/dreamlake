"""DreamLake-native experiment runs. Historical Dash runs remain on Dash."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import random
import re
import time
from urllib.parse import quote

import httpx

_KEY = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_ID = re.compile(r"[0-9a-f]{24}\Z")


class ExperimentRequestError(Exception):
    """A native Experiment request failed; writes are never retried implicitly.

    ``status`` is the HTTP status (None for transport/shape failures), ``code``
    the server's ``error.code`` string when the response carried one (e.g.
    ``OPERATION_CONFLICT``, ``NATIVE_RUN_ACTIVE``), and ``body`` the parsed JSON
    response (or raw text). ``str(error)`` never echoes the body.
    """

    def __init__(self, status: int | None, message: str, *, code: str | None = None, body=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.body = body


def _error_code(body) -> str | None:
    err = body.get("error") if isinstance(body, dict) else None
    code = err.get("code") if isinstance(err, dict) else None
    return code if isinstance(code, str) else None


_CONFLICT_CODE = "OPERATION_CONFLICT"
_MAX_CONFLICT_RETRIES = 5


class Experiments:
    """Project-scoped logging API using DreamLake storage and permissions."""

    def __init__(self, client):
        self._client = client

    @staticmethod
    def _base(namespace: str, project: str) -> str:
        for name, value in (("namespace", namespace), ("project", project)):
            if not isinstance(value, str) or not value or len(value.encode()) > 256 or any(ord(c) < 33 or ord(c) == 127 for c in value):
                raise ValueError(f"{name} must be a nonempty path segment")
        return f"/native-experiments/ns/{quote(namespace, safe='')}/projects/{quote(project, safe='')}"

    @staticmethod
    def _run_id(run_id: str) -> str:
        if not isinstance(run_id, str) or not _ID.fullmatch(run_id):
            raise ValueError("run_id must be a DreamLake native Experiment ID")
        return run_id

    _sleep = staticmethod(time.sleep)

    def _send(self, method: str, path: str, *, body=None, params=None, data=None, files=None,
              retry_conflicts: bool = False):
        if not self._client._token:
            raise ExperimentRequestError(401, "DreamLake authentication is required")
        attempt = 0
        while True:
            try:
                with self._client.http() as http:
                    response = http.request(method, path, json=body, params=params, data=data, files=files)
            except httpx.HTTPError:
                raise ExperimentRequestError(None, "Experiment API unavailable; reconcile before retrying writes") from None
            if response.is_success:
                return response
            try:
                err_body = response.json()
            except ValueError:
                err_body = response.text
            code = _error_code(err_body)
            # Only the server's own transactional OPERATION_CONFLICT is retried: that
            # transaction rolled back, unlike NATIVE_RUN_ACTIVE / WRITE_PROTECTED which are
            # stable states. Opt-in, bounded, with jittered exponential backoff.
            if retry_conflicts and response.status_code == 409 and code == _CONFLICT_CODE \
                    and attempt < _MAX_CONFLICT_RETRIES:
                self._sleep(random.uniform(0, min(2.0, 0.1 * 2 ** attempt)))
                attempt += 1
                continue
            suffix = f", {code}" if code else ""
            raise ExperimentRequestError(
                response.status_code, f"Experiment API request failed (HTTP {response.status_code}{suffix})",
                code=code, body=err_body)

    def _request(self, method: str, path: str, *, body=None, params=None, data=None, files=None,
                 retry_conflicts: bool = False) -> dict:
        response = self._send(method, path, body=body, params=params, data=data, files=files,
                              retry_conflicts=retry_conflicts)
        try:
            result = response.json()
            if not isinstance(result, dict):
                raise TypeError
            return result
        except (ValueError, TypeError):
            raise ExperimentRequestError(None, "Experiment API returned an invalid response; reconcile before retrying writes") from None

    def status(self, namespace: str, project: str) -> dict:
        return self._request("GET", self._base(namespace, project) + "/status")

    def session(self, namespace: str, project: str, mode: str = "write") -> dict:
        if mode not in {"read", "write"}:
            raise ValueError("mode must be read or write")
        return self._request("POST", self._base(namespace, project) + "/session", body={"mode": mode})

    def create_run(self, namespace: str, project: str, key: str) -> dict:
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise ValueError("key must contain 1-128 letters, digits, underscores or hyphens")
        return self._request("PUT", self._base(namespace, project) + f"/runs/{quote(key, safe='')}")["experiment"]

    def list_runs(self, namespace: str, project: str, *, cursor: str | None = None,
                  limit: int = 50, include_archived: bool = False) -> dict:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100")
        return self._request("GET", self._base(namespace, project) + "/runs",
                             params={"limit": limit, "includeArchived": str(include_archived).lower(),
                                     **({"cursor": cursor} if cursor is not None else {})})

    def get_run(self, namespace: str, project: str, run_id: str) -> dict:
        return self._request("GET", self._base(namespace, project) + f"/runs/{self._run_id(run_id)}")["experiment"]

    def list_trash(self, namespace: str, project: str, *, cursor: str | None = None,
                   limit: int = 50) -> dict:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100")
        return self._request("GET", self._base(namespace, project) + "/trash",
                             params={"limit": limit, **({"cursor": cursor} if cursor is not None else {})})

    def delete_run(self, namespace: str, project: str, run_id: str) -> dict:
        """Move a terminal run to trash; running runs must finish first."""
        return self._request("DELETE", self._base(namespace, project) + f"/runs/{self._run_id(run_id)}")["experiment"]

    def restore_deleted(self, namespace: str, project: str, run_id: str) -> dict:
        """Restore a trashed run without changing its archive state."""
        return self._request("POST", self._base(namespace, project) + f"/runs/{self._run_id(run_id)}/restore")["experiment"]

    def archive(self, namespace: str, project: str, run_id: str, archived: bool = True) -> dict:
        if type(archived) is not bool:
            raise ValueError("archived must be a boolean")
        return self._request("PATCH", self._base(namespace, project) + f"/runs/{self._run_id(run_id)}",
                             body={"archived": archived})["experiment"]

    def set_parameters(self, namespace: str, project: str, run_id: str, data: dict, *,
                       retry_conflicts: bool = False) -> dict:
        if not isinstance(data, dict) or not data:
            raise ValueError("data must be a nonempty mapping")
        return self._request("POST", self._base(namespace, project) + f"/runs/{self._run_id(run_id)}/parameters",
                             body={"data": data}, retry_conflicts=retry_conflicts)["parameters"]

    def log(self, namespace: str, project: str, run_id: str, entries: list[dict], *,
            retry_conflicts: bool = False) -> dict:
        if not isinstance(entries, list) or not 1 <= len(entries) <= 1000:
            raise ValueError("entries must contain 1-1000 log records")
        return self._request("POST", self._base(namespace, project) + f"/runs/{self._run_id(run_id)}/logs",
                             body={"entries": entries}, retry_conflicts=retry_conflicts)

    def append_metric(self, namespace: str, project: str, run_id: str, name: str, points: list[dict], *,
                      retry_conflicts: bool = False) -> dict:
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name):
            raise ValueError("metric name must be a single safe segment")
        if not isinstance(points, list) or not 1 <= len(points) <= 1000:
            raise ValueError("points must contain 1-1000 records")
        return self._request("POST", self._base(namespace, project) + f"/runs/{self._run_id(run_id)}/metrics/{quote(name, safe='')}",
                             body={"dataPoints": points}, retry_conflicts=retry_conflicts)

    def set_status(self, namespace: str, project: str, run_id: str, status: str, *,
                   retry_conflicts: bool = False) -> dict:
        if status not in {"RUNNING", "COMPLETED", "FAILED", "CANCELLED"}:
            raise ValueError("invalid Experiment status")
        return self._request("POST", self._base(namespace, project) + f"/runs/{self._run_id(run_id)}/status",
                             body={"status": status}, retry_conflicts=retry_conflicts)["experiment"]

    @staticmethod
    def _metric_name(name: str) -> str:
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name):
            raise ValueError("metric name must be a single safe segment")
        return quote(name, safe="")

    def list_metrics(self, namespace: str, project: str, run_id: str) -> list:
        return self._request("GET", self._base(namespace, project) + f"/runs/{self._run_id(run_id)}/metrics")["metrics"]

    def read_metric(self, namespace: str, project: str, run_id: str, name: str, *,
                    start_index: int = 0, limit: int = 1000) -> dict:
        """Read points of one metric: ``{"rows": [...], "nextCursor": str | None}``.

        ``start_index`` is the 0-based index of the first point (the server's
        ``cursor``); ``limit`` is 1-1000. ``nextCursor`` is None once a page comes
        back short of ``limit``.
        """
        if type(start_index) is not int or start_index < 0:
            raise ValueError("start_index must be a nonnegative integer")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer from 1 to 1000")
        return self._request("GET", self._base(namespace, project) +
                             f"/runs/{self._run_id(run_id)}/metrics/{self._metric_name(name)}",
                             params={"limit": limit, "cursor": start_index})

    def upload_file(self, namespace: str, project: str, run_id: str, path, *, name: str | None = None,
                    prefix: str = "/", description: str | None = None, tags: list[str] | None = None,
                    metadata: dict | None = None, retry_conflicts: bool = False) -> dict:
        """Upload a local file to a run (direct multipart; size and sha256 are sent for server verification).

        Returns the server's file record. ``name`` overrides the stored filename.
        """
        filename = name if name is not None else os.path.basename(os.fspath(path))
        if not filename or "/" in filename or "\\" in filename:
            raise ValueError("file name must be a nonempty single segment")
        with open(path, "rb") as fh:
            content = fh.read()
        fields = {"sizeBytes": str(len(content)), "checksum": hashlib.sha256(content).hexdigest(), "prefix": prefix}
        if description is not None:
            fields["description"] = description
        if tags:
            fields["tags"] = ",".join(tags)
        if metadata is not None:
            fields["metadata"] = json.dumps(metadata)
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        # Form fields precede the file part: the server reads them off the file part's field map.
        return self._request("POST", self._base(namespace, project) + f"/runs/{self._run_id(run_id)}/files",
                             data=fields, files={"file": (filename, content, ctype)},
                             retry_conflicts=retry_conflicts)

    def list_files(self, namespace: str, project: str, run_id: str, *, prefix: str | None = None,
                   tags: list[str] | None = None) -> dict:
        params = {**({"prefix": prefix} if prefix is not None else {}), **({"tags": ",".join(tags)} if tags else {})}
        return self._request("GET", self._base(namespace, project) + f"/runs/{self._run_id(run_id)}/files",
                             params=params or None)

    def get_file(self, namespace: str, project: str, run_id: str, file_id: str) -> dict:
        return self._request("GET", self._base(namespace, project) +
                             f"/runs/{self._run_id(run_id)}/files/{quote(file_id, safe='')}")["file"]

    def download_file(self, namespace: str, project: str, run_id: str, file_id: str, dest=None) -> bytes:
        """Download a run file's bytes; also write them to ``dest`` when given."""
        content = self._send("GET", self._base(namespace, project) +
                             f"/runs/{self._run_id(run_id)}/files/{quote(file_id, safe='')}/download").content
        if dest is not None:
            with open(dest, "wb") as fh:
                fh.write(content)
        return content
