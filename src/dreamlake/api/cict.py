"""CI/CT through GitHub Actions (channel A): GitHub connection, runner pools and runs.

The server owns validation and access; this module checks shapes before a
request is sent and never forwards server error text.
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from urllib.parse import quote, urlencode

import httpx

_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_ID = re.compile(r"[a-f0-9]{24}\Z")
_RUN = re.compile(r"\d{1,12}-\d{1,20}\Z")
_ENV = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_FILE = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_./-]{0,1023}\Z")
_CODE = re.compile(r"[a-z_]{1,64}\Z")
_LOGIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\[\]-]{0,99}")
_MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])\Z")
_MACHINE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}\Z")
_WORKFLOW = re.compile(r"[A-Za-z0-9._-]{1,100}\.ya?ml\Z")
_REF = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._/-]{0,199}\Z")
_INPUT = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,99}\Z")
_REQUEST = re.compile(r"[A-Za-z0-9_-]{8,64}\Z")


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
        """Machine catalog (what one job gets on each), the namespace's access, limit, occupancy,
        this month's GPU time and ``authorizer`` (who authorizes evidence writes and whether they
        still can; jobs wait while ``writable`` is false). Re-authorize with :meth:`set_source`."""
        return self._request("GET", self._ns(namespace) + "/cict/platform")

    def start(self, namespace, *, source_id=None):
        """Start using DreamLake GPU: use ``source_id`` or create ``cict-evidence``; the caller authorizes evidence writes."""
        body = {} if source_id is None else {"sourceId": _text(source_id, _ID, "Source ID")}
        return self._request("POST", self._ns(namespace) + "/cict/platform/activate", body=body)

    def set_source(self, namespace, source_id):
        """Change where DreamLake GPU runs save results. Passing the current Source re-authorizes:
        the caller becomes the evidence authorizer."""
        return self._request("PATCH", self._ns(namespace) + "/cict/platform", body={"sourceId": _text(source_id, _ID, "Source ID")})


class CictAdmin(_Base):
    """DreamLake GPU administration, for admins of the platform namespace."""

    def overview(self, *, month=None):
        """Namespaces and machine types, with a month's GPU time (``YYYY-MM``, default this month)
        per namespace and the platform ``total``."""
        if month is not None and (not isinstance(month, str) or not _MONTH.fullmatch(month)):
            raise CictError("month must be YYYY-MM")
        return self._request("GET", "/cict/platform/admin" + ("?" + urlencode({"month": month}) if month else ""))

    def providers(self):
        """Slurm providers in the platform namespace and what one job gets on each (from its launch layer)."""
        return self._request("GET", "/cict/platform/admin/providers")["providers"]

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
        """Create or change a machine type: ``label``, ``title``, ``provider_id``, ``enabled``,
        ``max_concurrent``, ``env``. What one job gets (GPUs, CPUs, memory, time limit) is read from
        the provider's launch layer; a layer without a GPU is refused (``launch_spec_unavailable``)."""
        names = {"label": "label", "title": "title", "provider_id": "providerId", "enabled": "enabled",
                 "max_concurrent": "maxConcurrent", "env": "env"}
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

    def usage(self, namespace, *, month=None, sender=None, limit=None):
        """DreamLake GPU time in a month (``YYYY-MM``, default this month): totals, ``byWorkflow``,
        ``bySender`` (the GitHub user who triggered each job) and ``runs`` (newest first; ``sender``
        narrows them, ``limit`` 1-500 caps them; ``runsTotal`` counts them)."""
        query = {}
        if month is not None:
            if not isinstance(month, str) or not _MONTH.fullmatch(month):
                raise CictError("month must be YYYY-MM")
            query["month"] = month
        if sender is not None:
            query["sender"] = _text(sender, _LOGIN, "GitHub login")
        if limit is not None:
            if type(limit) is not int or not 1 <= limit <= 500:
                raise CictError("limit must be between 1 and 500")
            query["limit"] = limit
        path = self._jobs._ns(namespace) + "/cict/usage" + ("?" + urlencode(query) if query else "")
        return self._jobs._request("GET", path)

    def onboarding(self, namespace):
        """Where the namespace stands on CI/CT and the next step.

        Reports GitHub, DreamLake GPU and the own-machine steps (enrolled login node, service
        account, Slurm provider, association, launch layer, runner pool), each ``done`` or not with
        ``how`` hints (``ui``, ``cli``, ``api``, ``human``), plus ``ready``, ``next`` and, once ready,
        the ``workflow`` runs-on line. Only installing the GitHub App needs a person.
        """
        return self._jobs._request("GET", self._jobs._ns(namespace) + "/cict/onboarding")

    def dispatch(self, namespace, *, repository, workflow, ref=None, inputs=None, installation_id=None, request_id=None):
        """Run a GitHub Actions workflow (``workflow_dispatch``) through the namespace's GitHub App
        installation, as the App. Its DreamLake jobs run, queue and are billed like any other; the
        runs then carry ``dispatchedBy``. Needs ``lakeshore:use``.

        ``ref`` defaults to the repository's default branch and ``installation_id`` to the one
        active installation. ``inputs`` must be inputs the workflow declares. A retry with the same
        ``request_id`` returns the first answer. Returns the dispatch: ``state`` is ``dispatched``
        (with ``workflowRunUrl``), ``failed`` (``error`` and GitHub's ``message``) or ``unknown``
        (GitHub's answer was lost: check the repository's Actions page before running it again).
        """
        repository = _text(repository, _REPOSITORY, "repository (owner/repo)")
        workflow = _text(workflow, _WORKFLOW, "workflow file name")
        values = {}
        for name, value in dict(inputs or {}).items():
            if not isinstance(name, str) or not _INPUT.fullmatch(name) or not isinstance(value, str) or len(value) > 1024:
                raise CictError("inputs must map input names to text of at most 1024 characters")
            values[name] = value
        if len(values) > 25:
            raise CictError("At most 25 inputs")
        if installation_id is None:
            active = [i for i in self.github.installations(namespace) if i.get("state") == "active"]
            if len(active) != 1:
                raise CictError("Pass installation_id: several GitHub accounts are connected" if active
                                else "No GitHub account is connected to this namespace")
            installation_id = active[0]["installationId"]
        if type(installation_id) is not int or installation_id <= 0:
            raise CictError("Invalid installation ID")
        if ref is None:
            ref = self.dispatch_branches(namespace, installation_id=installation_id, repository=repository)["defaultBranch"]
        ref = _text(ref, _REF, "ref")
        if ".." in ref or ref.endswith("/") or ref.endswith(".lock"):
            raise CictError("Invalid ref")
        request_id = _text(request_id or "py-" + uuid.uuid4().hex, _REQUEST, "request ID")
        body = {"installationId": installation_id, "repository": repository, "workflow": workflow, "ref": ref,
                "inputs": values, "requestId": request_id}
        return self._jobs._request("POST", self._jobs._ns(namespace) + "/cict/dispatches", body=body)["dispatch"]

    def dispatches(self, namespace, *, limit=None):
        """Workflow runs asked for from DreamLake, newest first (``limit`` 1-100, default 20)."""
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 100):
            raise CictError("limit must be between 1 and 100")
        query = {} if limit is None else {"limit": limit}
        path = self._jobs._ns(namespace) + "/cict/dispatches" + ("?" + urlencode(query) if query else "")
        return self._jobs._request("GET", path)["dispatches"]

    def dispatch_branches(self, namespace, *, installation_id, repository):
        """A repository's ``defaultBranch`` and ``branches`` (``None`` when the App may not list them)."""
        if type(installation_id) is not int or installation_id <= 0:
            raise CictError("Invalid installation ID")
        query = urlencode({"installationId": installation_id, "repository": _text(repository, _REPOSITORY, "repository (owner/repo)")})
        return self._jobs._request("GET", self._jobs._ns(namespace) + "/cict/dispatch/branches?" + query)

    def jobs(self, namespace, *, limit=None):
        """Raw recorded GitHub job events, including jobs that matched no pool."""
        query = {} if limit is None else {"limit": _limit(limit)}
        path = self._jobs._ns(namespace) + "/github/jobs" + ("?" + urlencode(query) if query else "")
        return self._jobs._request("GET", path)["jobs"]
