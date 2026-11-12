"""CI/CT through GitHub Actions (channel A): GitHub connection, runner pools and runs.

The server owns validation and access; this module checks shapes before a
request is sent and never forwards server error text.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from urllib.parse import quote, urlencode

import httpx

_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_ID = re.compile(r"[a-f0-9]{24}\Z")
_RUN = re.compile(r"\d{1,12}-\d{1,20}\Z")
_ENV = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_FILE = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_./-]{0,1023}\Z")
_CODE = re.compile(r"[a-z_]{1,64}\Z")
_MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])\Z")
_MACHINE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}\Z")


class CictError(Exception):
    """A refused or failed CI/CT request; ``code`` is the server's stable error code."""

    def __init__(self, message, *, status=None, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


class CictNotFound(CictError):
    pass


def _text(value, pattern, label):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise CictError(f"Invalid {label}")
    return value


def _limit(value):
    if value is None:
        return None
    if type(value) is not int or not 1 <= value <= 200:
        raise CictError("limit must be between 1 and 200")
    return value


class _Base:
    def __init__(self, client):
        self._client = client

    def _ns(self, namespace):
        return "/namespaces/" + quote(_text(namespace, _SEGMENT, "namespace"), safe="")

    def _request(self, method, path, *, body=None):
        if not self._client._token:
            raise CictError("DreamLake authentication is required", status=401, code="unauthorized")
        try:
            with self._client.http() as http:
                response = http.request(method, path, json=body, follow_redirects=False)
        except (httpx.HTTPError, ValueError):
            raise CictError("CI/CT response unavailable") from None
        try:
            data = response.json()
        except ValueError:
            data = None
        if not response.is_success:
            code = data.get("code") or data.get("error") if isinstance(data, dict) else None
            code = code if isinstance(code, str) and _CODE.fullmatch(code) else None
            error = CictNotFound if response.status_code == 404 else CictError
            raise error(f"CI/CT request failed (HTTP {response.status_code})" + (f": {code}" if code else ""),
                        status=response.status_code, code=code)
        if not isinstance(data, dict):
            raise CictError("CI/CT response is invalid", status=response.status_code)
        return data


class CictRuns(_Base):
    """Each GitHub Actions job that asked for a DreamLake label, with the runner that served it."""

    def list(self, namespace, *, limit=None):
        query = {} if limit is None else {"limit": _limit(limit)}
        path = self._ns(namespace) + "/cict/runs" + ("?" + urlencode(query) if query else "")
        return self._request("GET", path)["runs"]

    def get(self, namespace, run_id):
        """``run_id`` is ``<installationId>-<jobId>``."""
        return self._request("GET", self._ns(namespace) + "/cict/runs/" + _text(run_id, _RUN, "run ID"))["run"]

    def comparison(self, namespace, run_id):
        """The run compared case by case with its baseline; ``None`` until its evidence is written.

        The baseline is the latest earlier run of the same repository, workflow
        and job on the default branch whose evidence was written.
        """
        path = self._ns(namespace) + "/cict/runs/" + _text(run_id, _RUN, "run ID") + "/comparison"
        return self._request("GET", path)["comparison"]

    def result(self, namespace, run_id, *, file="result.json"):
        """Read one evidence file (default ``result.json``) as JSON; ``None`` if absent.

        The bytes come from the run's evidence Source through a short-lived read
        capability; the DreamLake token is never sent to storage.
        """
        file = _text(file, _FILE, "evidence file path")
        if any(part in {"", ".", ".."} for part in file.split("/")):
            raise CictError("Invalid evidence file path")
        run = self.get(namespace, run_id)
        evidence = run.get("evidence")
        if not evidence:
            return None
        source = _text(evidence.get("sourceId"), _ID, "evidence Source ID")
        try:
            capability = self._request("POST", self._ns(namespace) + "/sources/" + source + "/read-capabilities",
                                       body={"path": f"{evidence['prefix']}/{file}", "intent": "preview"})
        except CictNotFound:
            return None
        url = capability.get("url")
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            raise CictError("Source returned an invalid read capability")
        headers = capability.get("allowedHeaders") or {}
        try:
            with httpx.Client(timeout=30, transport=self._client._transport) as http:
                response = http.get(url, headers=headers, follow_redirects=False)
        except httpx.HTTPError:
            raise CictError(f"Reading {file} failed") from None
        if response.status_code == 404:
            return None
        if not response.is_success:
            raise CictError(f"Reading {file} failed (HTTP {response.status_code})", status=response.status_code)
        try:
            return response.json()
        except ValueError:
            raise CictError(f"{file} is not JSON") from None


class CictPools(_Base):
    """Route workflow ``runs-on`` labels to DreamLake machines."""

    def list(self, namespace):
        return self._request("GET", self._ns(namespace) + "/github/runner-pools")["pools"]

    def create(self, namespace, *, name, provider_id, labels=None, evidence_source_id=None, evidence_prefix=None, env=None):
        """Create a pool that starts single-use runners on a Slurm provider.

        Without ``labels`` the pool serves ``dreamlake-<name>``. ``env`` holds
        machine facts every job sees (cache paths and the like), never secrets.
        """
        body = {"name": name, "target": {"kind": "slurm", "providerId": _text(provider_id, _ID, "provider ID")}}
        if labels is not None:
            if isinstance(labels, str) or not isinstance(labels, Sequence) or not all(isinstance(x, str) for x in labels):
                raise CictError("labels must be a list of strings")
            body["labels"] = list(labels)
        if evidence_prefix is not None and evidence_source_id is None:
            raise CictError("evidence_prefix requires evidence_source_id")
        if evidence_source_id is not None:
            body["evidence"] = {"sourceId": _text(evidence_source_id, _ID, "evidence Source ID")}
            if evidence_prefix is not None:
                body["evidence"]["prefix"] = evidence_prefix
        if env is not None:
            if not isinstance(env, Mapping) or not all(isinstance(k, str) and _ENV.fullmatch(k) and isinstance(v, str) for k, v in env.items()):
                raise CictError("env must map uppercase names to strings")
            body["env"] = dict(env)
        return self._request("POST", self._ns(namespace) + "/github/runner-pools", body=body)

    _UNSET = object()

    def update(self, namespace, pool_id, *, provider_id=None, labels=None, evidence_source_id=None,
               evidence_prefix=None, no_evidence=False, env=_UNSET):
        """Edit a pool for the next job; running jobs keep what they started with.

        ``no_evidence=True`` stops keeping evidence; ``env`` replaces every
        variable, and ``env=None`` removes them. The name cannot change.
        """
        body = {}
        if provider_id is not None:
            body["target"] = {"kind": "slurm", "providerId": _text(provider_id, _ID, "provider ID")}
        if labels is not None:
            if isinstance(labels, str) or not isinstance(labels, Sequence) or not all(isinstance(x, str) for x in labels):
                raise CictError("labels must be a list of strings")
            body["labels"] = list(labels)
        if no_evidence:
            if evidence_source_id is not None:
                raise CictError("Pass evidence_source_id or no_evidence, not both")
            body["evidence"] = None
        elif evidence_source_id is not None:
            body["evidence"] = {"sourceId": _text(evidence_source_id, _ID, "evidence Source ID")}
            if evidence_prefix is not None:
                body["evidence"]["prefix"] = evidence_prefix
        elif evidence_prefix is not None:
            raise CictError("evidence_prefix requires evidence_source_id")
        if env is not self._UNSET:
            if env is not None and (not isinstance(env, Mapping) or not all(isinstance(k, str) and _ENV.fullmatch(k) and isinstance(v, str) for k, v in env.items())):
                raise CictError("env must map uppercase names to strings")
            body["env"] = None if env is None else dict(env)
        if not body:
            raise CictError("Nothing to change")
        return self._request("PATCH", self._ns(namespace) + "/github/runner-pools/" + _text(pool_id, _ID, "pool ID"), body=body)

    def delete(self, namespace, pool_id):
        """Delete a pool; refused with ``runner_pool_busy`` while it still serves running jobs."""
        return self._request("DELETE", self._ns(namespace) + "/github/runner-pools/" + _text(pool_id, _ID, "pool ID"))

    def targets(self, namespace):
        """Slurm providers a pool can target, each with ``usable`` and the ``reason`` it cannot."""
        return self._request("GET", self._ns(namespace) + "/github/runner-targets")["targets"]


class CictGithub(_Base):
    """GitHub App installations bound to a namespace."""

    def installations(self, namespace):
        return self._request("GET", self._ns(namespace) + "/github/installations")["installations"]

    def connect(self, namespace):
        """Return the GitHub App install link.

        Open it within 15 minutes in a browser signed in to DreamLake as the
        same user; the DreamLake setup page completes the binding.
        """
        return self._request("POST", self._ns(namespace) + "/github/installations/start", body={})["installUrl"]


class CictPlatform(_Base):
    """DreamLake GPU: machines DreamLake provides, chosen by ``runs-on`` label."""

    def status(self, namespace):
        """Machine catalog, the namespace's access, limit, occupancy and this month's GPU time."""
        return self._request("GET", self._ns(namespace) + "/cict/platform")

    def start(self, namespace, *, source_id=None):
        """Start using DreamLake GPU: use ``source_id`` or create ``cict-evidence``; the caller authorizes evidence writes."""
        body = {} if source_id is None else {"sourceId": _text(source_id, _ID, "Source ID")}
        return self._request("POST", self._ns(namespace) + "/cict/platform/activate", body=body)

    def set_source(self, namespace, source_id):
        """Change where DreamLake GPU runs save results."""
        return self._request("PATCH", self._ns(namespace) + "/cict/platform", body={"sourceId": _text(source_id, _ID, "Source ID")})


class CictAdmin(_Base):
    """DreamLake GPU administration, for admins of the platform namespace."""

    def overview(self):
        return self._request("GET", "/cict/platform/admin")

    def set_namespace(self, namespace, *, state=None, max_concurrent=None):
        """Allow (``"allowed"``), pause (``"suspended"``) or limit a namespace."""
        body = {}
        if state is not None:
            if state not in ("allowed", "suspended"):
                raise CictError("state must be 'allowed' or 'suspended'")
            body["state"] = state
        if max_concurrent is not None:
            if type(max_concurrent) is not int or not 0 <= max_concurrent <= 100:
                raise CictError("max_concurrent must be between 0 and 100")
            body["maxConcurrent"] = max_concurrent
        return self._request("PUT", "/cict/platform/admin/namespaces/" + quote(_text(namespace, _SEGMENT, "namespace"), safe=""), body=body)

    def put_machine(self, machine_id, **fields):
        """Create or change a machine type: ``label``, ``title``, ``provider_id``, ``enabled``, ``max_concurrent``, ``env``, ``spec``."""
        names = {"label": "label", "title": "title", "provider_id": "providerId", "enabled": "enabled",
                 "max_concurrent": "maxConcurrent", "env": "env", "spec": "spec"}
        unknown = set(fields) - set(names)
        if unknown:
            raise CictError("Unknown machine field: " + ", ".join(sorted(unknown)))
        body = {names[k]: v for k, v in fields.items() if v is not None}
        return self._request("PUT", "/cict/platform/admin/machines/" + _text(machine_id, _MACHINE, "machine ID"), body=body)


class Cict:
    """CI/CT on DreamLake machines through GitHub Actions."""

    def __init__(self, client):
        self.runs = CictRuns(client)
        self.pools = CictPools(client)
        self.github = CictGithub(client)
        self.platform = CictPlatform(client)
        self.admin = CictAdmin(client)
        self._jobs = _Base(client)

    def usage(self, namespace, *, month=None):
        """DreamLake GPU time in a month (``YYYY-MM``, default this month), by repository and workflow."""
        if month is not None and (not isinstance(month, str) or not _MONTH.fullmatch(month)):
            raise CictError("month must be YYYY-MM")
        path = self._jobs._ns(namespace) + "/cict/usage" + ("?" + urlencode({"month": month}) if month else "")
        return self._jobs._request("GET", path)

    def jobs(self, namespace, *, limit=None):
        """Raw recorded GitHub job events, including jobs that matched no pool."""
        query = {} if limit is None else {"limit": _limit(limit)}
        path = self._jobs._ns(namespace) + "/github/jobs" + ("?" + urlencode(query) if query else "")
        return self._jobs._request("GET", path)["jobs"]
