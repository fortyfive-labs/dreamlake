"""Provider metadata and owned Slurm associations; no automatic write retries."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from urllib.parse import quote, urlencode

import httpx

_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_ID = re.compile(r"[a-f0-9]{24}\Z")
_REQUEST = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_CODES = set("unauthorized forbidden namespace_not_found provider_not_found enrollment_not_found association_not_found operation_not_found invalid_provider_declaration invalid_provider_association invalid_provider_retirement invalid_request_id invalid_resource_id invalid_pagination runner_not_supported request_id_conflict provider_name_conflict association_exists provider_revision_conflict association_revision_conflict provider_retired association_retired legacy_provider_requires_upgrade runner_configuration_mismatch provider_service_unavailable".split())


class ProviderError(Exception):
    """Sanitized error carrying the caller's request ID for explicit recovery."""

    def __init__(self, message, *, status=None, code=None, request_id=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.request_id = request_id


class ProviderConfigurationError(ProviderError, ValueError):
    pass


class ProviderAuthorizationError(ProviderError):
    pass


class ProviderConflictError(ProviderError):
    pass


class ProviderNotFound(ProviderError):
    pass


def _text(value, pattern, label):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ProviderConfigurationError(f"Invalid {label}")
    return value


def _namespace(namespace):
    return "/namespaces/" + quote(_text(namespace, _SEGMENT, "namespace"), safe="")


def _resource(namespace, provider_id):
    return _namespace(namespace) + "/lakeshore-providers/" + _text(provider_id, _ID, "provider ID")


def _fields(value, allowed):
    if not isinstance(value, Mapping) or set(value) - set(allowed):
        raise ProviderConfigurationError("Provider input contains unsupported fields or is not an object")


def _payload(value, kind):
    if kind == "declaration":
        _fields(value, ["version", "name", "kind", "config"])
        if not isinstance(value.get("kind"), str) or value["kind"] not in {"slurm-ssh", "k8s"}:
            raise ProviderConfigurationError("Provider kind must be slurm-ssh or k8s")
        config = value.get("config")
        _fields(config, ["cluster", "partitions", "accounts", "credentialRef"] if value["kind"] == "slurm-ssh" else ["cluster", "context", "namespace", "credentialRef"])
        if "credentialRef" in config:
            _fields(config["credentialRef"], ["namespace", "entryName"])
    else:
        _fields(value, ["providerRevision", "enrollmentId", "runner"])
        runner = value.get("runner")
        _fields(runner, ["kind", "partition", "account", "staging", "environment"])
        if runner.get("kind") != "slurm":
            raise ProviderConfigurationError("Only Slurm associations are implemented")
        _fields(runner.get("staging"), ["submissionRoot", "computeRoot"])
        _fields(runner.get("environment"), ["uvExecutable", "pythonExecutable"])
    try:
        # Snapshot the caller's data and reject non-JSON/NaN without echoing it.
        raw = json.dumps(dict(value), allow_nan=False, ensure_ascii=False)
        if len(raw.encode()) > 32768:
            raise ValueError
        return json.loads(raw)
    except (TypeError, ValueError):
        raise ProviderConfigurationError("Provider input must be JSON data of at most 32 KiB") from None


def _integer(value, label, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ProviderConfigurationError(f"Invalid {label}")
    return value


class ProviderOperations:
    def __init__(self, providers):
        self._providers = providers

    def get(self, namespace, operation_id):
        """Read the historical committed receipt; no mutation is repeated."""
        path = _namespace(namespace) + "/provider-operations/" + _text(operation_id, _ID, "operation ID")
        return self._providers._request("GET", path, operation_id=operation_id)

    def find(self, namespace, request_id):
        """Recover a receipt when the write response, including its ID, was lost."""
        rid = _text(request_id, _REQUEST, "request ID")
        return self._providers._request("GET", _namespace(namespace) + "/provider-operations/by-request/" + quote(rid, safe=""), request_id=rid)


class Providers:
    """Configuration and receipts, not provisioning or scheduler readiness."""

    def __init__(self, client):
        self._client = client
        self.operations = ProviderOperations(self)

    def _request(self, method, path, *, body=None, request_id=None, operation_id=None):
        if not self._client._token:
            raise ProviderAuthorizationError("DreamLake authentication is required", status=401, code="unauthorized", request_id=request_id)
        if body is not None and len(json.dumps(body, ensure_ascii=False).encode()) > 32768:
            raise ProviderConfigurationError("Provider request exceeds 32 KiB", request_id=request_id)
        try:
            with self._client.http() as client:
                response = client.request(method, path, json=body, follow_redirects=False)
        except (httpx.HTTPError, ValueError):
            raise ProviderError("Provider response unavailable; recover a submitted write by its request ID", request_id=request_id) from None
        try:
            data = response.json()
        except ValueError:
            data = None
        if not response.is_success:
            code = data.get("code") if isinstance(data, dict) else None
            code = code if isinstance(code, str) and code in _CODES else None
            error = ProviderAuthorizationError if response.status_code in {401, 403} else ProviderConflictError if response.status_code == 409 else ProviderNotFound if response.status_code == 404 else ProviderError
            raise error(f"Provider request failed (HTTP {response.status_code})" + (f": {code}" if code else ""), status=response.status_code, code=code, request_id=request_id)
        if not isinstance(data, dict):
            raise ProviderError("Provider response is invalid; recover submitted writes by request ID", status=response.status_code, request_id=request_id)
        if method == "POST" or "/provider-operations/" in path:
            resource = data.get("resource")
            if (data.get("status") != "committed" or (operation_id is not None and data.get("operationId") != operation_id) or not isinstance(data.get("requestId"), str) or not _REQUEST.fullmatch(data["requestId"]) or (request_id is not None and data["requestId"] != request_id)
                    or not isinstance(data.get("operationId"), str) or not _ID.fullmatch(data["operationId"])
                    or not isinstance(resource, dict) or not isinstance(resource.get("id"), str) or not _ID.fullmatch(resource["id"])):
                raise ProviderError("Provider returned an invalid receipt; recover by request ID", status=response.status_code, request_id=request_id)
        return data

    def register(self, namespace, *, declaration, request_id):
        """Store a declaration. Supply a stable request_id before making the call."""
        rid = _text(request_id, _REQUEST, "request ID")
        return self._request("POST", _namespace(namespace) + "/lakeshore-providers/registrations",
                             body={"requestId": rid, "declaration": _payload(declaration, "declaration")}, request_id=rid)

    def associate(self, namespace, provider_id, *, association, request_id):
        """Bind the caller's enrollment; this does not verify runner readiness."""
        rid = _text(request_id, _REQUEST, "request ID")
        return self._request("POST", _resource(namespace, provider_id) + "/associations",
                             body={**_payload(association, "association"), "requestId": rid}, request_id=rid)

    def get(self, namespace, provider):
        """Read an active provider by ID or name through existing collection APIs."""
        return self._request("GET", _namespace(namespace) + "/lakeshore-providers/" + quote(_text(provider, _SEGMENT, "provider"), safe=""))

    def list(self, namespace, *, page=1, page_size=50):
        query = {"page": _integer(page, "page", 100000), "pageSize": _integer(page_size, "page size", 200)}
        return self._request("GET", _namespace(namespace) + "/lakeshore-providers?" + urlencode(query))

    def status(self, namespace, provider_id, *, page=1, page_size=50):
        query = {"page": _integer(page, "page", 100000), "pageSize": _integer(page_size, "page size", 100)}
        return self._request("GET", _resource(namespace, provider_id) + "/status?" + urlencode(query))

    def retire(self, namespace, provider_id, *, revision, request_id, association_id=None):
        """Retire metadata only; no job/host/infrastructure/credential is stopped."""
        rid = _text(request_id, _REQUEST, "request ID")
        path = _resource(namespace, provider_id)
        if association_id is not None:
            path += "/associations/" + _text(association_id, _ID, "association ID")
        return self._request("POST", path + "/retire", body={"requestId": rid, "revision": _integer(revision, "revision", 2147483647)}, request_id=rid)
