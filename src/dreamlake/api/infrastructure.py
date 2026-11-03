"""Reviewed Infrastructure registration moves, preserving Terraform state identity.

Like Notes/Project moves, execution consumes the exact saved review. It never
re-previews, follows redirects, retries writes, or falls back to another API.
"""
from __future__ import annotations

from copy import deepcopy
import re

import httpx

_UUID = re.compile(r"[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}\Z")
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}\Z")
_REQUIRED = set("expectedSourceName destinationNamespaceSlug destinationProjectSlug expectedSourceNamespaceId expectedSourceProjectId expectedVersion expectedDestinationNamespaceId expectedDestinationProjectId expectedDigest idempotencyKey".split())


class InfrastructureError(Exception):
    """Sanitized API error. ``outcome`` is unknown when execution needs reconciliation."""

    def __init__(self, code, *, status=None, outcome="rejected", stack_id=None, operation_id=None):
        super().__init__(code)
        self.code, self.status, self.outcome = code, status, outcome
        self.stack_id, self.operation_id = stack_id, operation_id


def _match(value, pattern):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _uuid(value):
    if not _match(value, _UUID):
        raise ValueError("An immutable stack UUID is required")
    return value


def _path(value):
    return (isinstance(value, str) and 0 < len(value) <= 255
            and len(value.split("/")) <= 16
            and all(_match(part, _LABEL) for part in value.split("/")))


def _opaque(value):
    return isinstance(value, str) and 0 < len(value) <= 200 and not re.search(r"[\x00-\x20]", value)


def _stack(value):
    if (not isinstance(value, dict)
            or not all(isinstance(value.get(k), str) for k in
                       ("id", "namespaceId", "projectId", "name", "environment", "repository", "directory", "createdAt"))
            or not _match(value["id"], _UUID)
            or (value.get("path") is not None and not _path(value["path"]))
            or type(value.get("version")) is not int or not 0 <= value["version"] <= 2**53 - 1
            or type(value.get("locked")) is not bool):
        raise ValueError("Invalid Infrastructure stack metadata")
    return deepcopy(value)


def _review(value, stack_id):
    if not isinstance(value, dict):
        raise ValueError("Invalid saved Infrastructure move review")
    stack = _stack(value.get("stack"))
    intent = value.get("request")
    if not isinstance(intent, dict):
        raise ValueError("Invalid saved Infrastructure move request")
    keys = set(intent)
    if (not _REQUIRED <= keys or keys - _REQUIRED - {"destinationName", "destinationPath", "expectedSourcePath"}
            or not _match(intent["expectedSourceName"], _LABEL)
            or ("destinationName" in intent and not _match(intent["destinationName"], _LABEL))
            or ("destinationPath" in intent) != ("expectedSourcePath" in intent)
            or ("destinationPath" in intent and (not _path(intent["destinationPath"])
                or (intent["expectedSourcePath"] is not None and not _path(intent["expectedSourcePath"]))))
            or not all(_match(intent[k], _SLUG) for k in ("destinationNamespaceSlug", "destinationProjectSlug"))
            or not all(_opaque(intent[k]) for k in ("expectedSourceNamespaceId", "expectedSourceProjectId", "expectedDestinationNamespaceId", "expectedDestinationProjectId"))
            or type(intent["expectedVersion"]) is not int or not 0 <= intent["expectedVersion"] <= 2**53 - 1
            or (intent["expectedDigest"] is not None and not _match(intent["expectedDigest"], re.compile(r"[a-f0-9]{64}\Z")))
            or not _match(intent["idempotencyKey"], _UUID)):
        raise ValueError("Invalid saved Infrastructure move request")
    source, destination = value.get("source"), value.get("destination")
    blockers = value.get("blockers")
    if (type(value.get("apiVersion")) is not int or value["apiVersion"] != 1
            or value.get("semantics") != "admission-linearized"
            or stack["id"].lower() != stack_id.lower()
            or not isinstance(source, dict) or not isinstance(destination, dict)
            or not isinstance(blockers, list)
            or any(not isinstance(b, dict) or not isinstance(b.get("code"), str) for b in blockers)
            or type(value.get("executable")) is not bool
            or value["executable"] != (not stack["locked"] and not blockers)
            or stack["name"] != intent["expectedSourceName"] or stack["version"] != intent["expectedVersion"]
            or any(stack[k] != source.get(k) or stack[k] != intent["expectedSource" + k[0].upper() + k[1:]] for k in ("namespaceId", "projectId"))
            or any(destination.get(k) != intent["expectedDestination" + k[0].upper() + k[1:]] for k in ("namespaceId", "projectId"))
            or any(destination.get(k) != intent[k] for k in ("destinationNamespaceSlug", "destinationProjectSlug"))
            or ("destinationPath" in intent and stack.get("path") != intent["expectedSourcePath"])):
        raise ValueError("Invalid saved Infrastructure move review")
    if (source["projectId"] != destination["projectId"] and stack.get("path") is not None
            and "destinationPath" not in intent):
        raise ValueError("Cross-project move review must pin the source and destination paths")
    return deepcopy(value)


class Infrastructure:
    def __init__(self, client):
        self.client = client

    def _request(self, method, path, payload=None, *, executing=False, params=None):
        identity = ({"stack_id": path.split("/")[3], "operation_id": payload["idempotencyKey"]}
                    if executing else {})
        try:
            with self.client.http() as http:
                response = http.request(method, path, json=payload, params=params, follow_redirects=False)
        except httpx.HTTPError:
            raise InfrastructureError("MOVE_OUTCOME_UNKNOWN" if executing else "INFRASTRUCTURE_UNREACHABLE",
                                      outcome="unknown" if executing else "rejected", **identity) from None
        if not response.is_success:
            try:
                body = response.json()
                code = body.get("error") if isinstance(body, dict) else None
            except ValueError:
                code = None
            # These 409s explicitly mean a database mutation may have committed.
            # Keep only allowlisted codes and our already-validated request IDs.
            reconciliation = isinstance(code, str) and code in {
                "stack_path_reconciliation_required", "stack_move_reconciliation_required"}
            unknown = executing and (response.status_code >= 500 or reconciliation)
            safe_code = code if reconciliation else "MOVE_OUTCOME_UNKNOWN" if unknown else "INFRASTRUCTURE_REQUEST_FAILED"
            raise InfrastructureError(safe_code, status=response.status_code,
                                      outcome="unknown" if unknown else "rejected", **identity)
        try:
            return response.json()
        except ValueError:
            raise InfrastructureError("MOVE_OUTCOME_UNKNOWN" if executing else "INVALID_INFRASTRUCTURE_RESPONSE",
                                      outcome="unknown" if executing else "rejected", **identity) from None

    def status(self, stack_id):
        """Read metadata by immutable UUID; never download Terraform state secrets."""
        stack = _stack(self._request("GET", "/infrastructure/stacks/" + _uuid(stack_id)))
        if stack["id"].lower() != stack_id.lower():
            raise ValueError("Infrastructure returned a different stack UUID")
        return stack

    def preview_move(self, stack_id, *, destination_namespace, destination_project,
                     destination_name=None, destination_path=None):
        """Read a server-authorized review. Omitted cross-project paths preserve placement."""
        _uuid(stack_id)
        if not _match(destination_namespace, _SLUG) or not _match(destination_project, _SLUG):
            raise ValueError("Explicit destination namespace and project slugs are required")
        body = {"destinationNamespaceSlug": destination_namespace, "destinationProjectSlug": destination_project}
        if destination_name is not None:
            if not _match(destination_name, _LABEL):
                raise ValueError("Invalid destination name")
            body["destinationName"] = destination_name
        if destination_path is not None:
            if not _path(destination_path):
                raise ValueError("Invalid destination path")
            body["destinationPath"] = destination_path
        review = _review(self._request("POST", "/infrastructure/stacks/" + stack_id + "/move/preview", body), stack_id)
        intent = review["request"]
        inferred_path = (destination_path is None and review["source"]["projectId"] != review["destination"]["projectId"]
                         and review["stack"].get("path") is not None)
        expected_path = review["stack"].get("path") if inferred_path else destination_path
        if (intent["destinationNamespaceSlug"] != destination_namespace
                or intent["destinationProjectSlug"] != destination_project
                or intent.get("destinationName") != destination_name
                or intent.get("destinationPath") != expected_path):
            raise ValueError("Infrastructure preview changed the requested destination")
        return review

    def execute_move(self, stack_id, reviewed, *, confirm=False):
        """Execute exactly this review once; retain it for an explicit idempotent retry.

        On an unknown outcome, inspect ``status(stack_id)`` and reconcile before
        retrying this same review. Never create a replacement registration.
        """
        _uuid(stack_id)
        if confirm is not True:
            raise ValueError("Executing a saved move review requires confirm=True")
        saved = _review(reviewed, stack_id)
        if not saved["executable"]:
            raise ValueError("Saved move review is blocked; obtain a new review after resolving blockers")
        intent = saved["request"]
        result = self._request("POST", "/infrastructure/stacks/" + stack_id + "/move", intent, executing=True)
        try:
            stack = _stack(result.get("stack"))
            if (type(result.get("apiVersion")) is not int or result["apiVersion"] != 1
                    or type(result.get("moved")) is not bool or type(result.get("replayed")) is not bool
                    or (not result["moved"] and (result["replayed"]
                        or saved["source"]["projectId"] != saved["destination"]["projectId"]
                        or intent.get("destinationName", saved["stack"]["name"]) != saved["stack"]["name"]
                        or "destinationPath" in intent))
                    or result.get("operationId") != intent["idempotencyKey"]
                    or stack["id"].lower() != stack_id.lower() or stack["locked"]
                    or stack["namespaceId"] != saved["destination"]["namespaceId"]
                    or stack["projectId"] != saved["destination"]["projectId"]
                    or stack["name"] != intent.get("destinationName", saved["stack"]["name"])
                    or stack.get("path") != intent.get("destinationPath", saved["stack"].get("path"))
                    or any(stack[k] != saved["stack"][k] for k in ("version", "environment", "repository", "directory", "createdAt"))):
                raise ValueError()
        except (ValueError, TypeError, AttributeError, KeyError):
            raise InfrastructureError("MOVE_OUTCOME_UNKNOWN", outcome="unknown", stack_id=stack_id,
                                      operation_id=intent["idempotencyKey"]) from None
        return deepcopy(result)
