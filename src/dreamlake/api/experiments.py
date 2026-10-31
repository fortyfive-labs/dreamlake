"""DreamLake-native experiment runs. Historical Dash runs remain on Dash."""

from __future__ import annotations

import re
from urllib.parse import quote

import httpx

_KEY = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_ID = re.compile(r"[0-9a-f]{24}\Z")


class ExperimentRequestError(Exception):
    """A native Experiment request failed; writes are never retried implicitly."""

    def __init__(self, status: int | None, message: str):
        super().__init__(message)
        self.status = status


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

    def _request(self, method: str, path: str, *, body=None, params=None) -> dict:
        if not self._client._token:
            raise ExperimentRequestError(401, "DreamLake authentication is required")
        try:
            with self._client.http() as http:
                response = http.request(method, path, json=body, params=params)
        except httpx.HTTPError:
            raise ExperimentRequestError(None, "Experiment API unavailable; reconcile before retrying writes") from None
        if not response.is_success:
            raise ExperimentRequestError(response.status_code, f"Experiment API request failed (HTTP {response.status_code})")
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

    def archive(self, namespace: str, project: str, run_id: str, archived: bool = True) -> dict:
        if type(archived) is not bool:
            raise ValueError("archived must be a boolean")
        return self._request("PATCH", self._base(namespace, project) + f"/runs/{self._run_id(run_id)}",
                             body={"archived": archived})["experiment"]

    def set_parameters(self, namespace: str, project: str, run_id: str, data: dict) -> dict:
        if not isinstance(data, dict) or not data:
            raise ValueError("data must be a nonempty mapping")
        return self._request("POST", self._base(namespace, project) + f"/runs/{self._run_id(run_id)}/parameters",
                             body={"data": data})["parameters"]

    def log(self, namespace: str, project: str, run_id: str, entries: list[dict]) -> dict:
        if not isinstance(entries, list) or not 1 <= len(entries) <= 1000:
            raise ValueError("entries must contain 1-1000 log records")
        return self._request("POST", self._base(namespace, project) + f"/runs/{self._run_id(run_id)}/logs",
                             body={"entries": entries})

    def append_metric(self, namespace: str, project: str, run_id: str, name: str, points: list[dict]) -> dict:
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name):
            raise ValueError("metric name must be a single safe segment")
        if not isinstance(points, list) or not 1 <= len(points) <= 1000:
            raise ValueError("points must contain 1-1000 records")
        return self._request("POST", self._base(namespace, project) + f"/runs/{self._run_id(run_id)}/metrics/{quote(name, safe='')}",
                             body={"dataPoints": points})

    def set_status(self, namespace: str, project: str, run_id: str, status: str) -> dict:
        if status not in {"RUNNING", "COMPLETED", "FAILED", "CANCELLED"}:
            raise ValueError("invalid Experiment status")
        return self._request("POST", self._base(namespace, project) + f"/runs/{self._run_id(run_id)}/status",
                             body={"status": status})["experiment"]
