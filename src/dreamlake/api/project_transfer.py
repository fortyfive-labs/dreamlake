"""Whole-Project convenience over the versioned ownership journal.

No per-Note transfers or implicit retries. A reviewed request is reusable even
when a successful previous attempt has already moved the source Project.
"""
import copy
import re
import shlex
import time
from typing import NotRequired, TypedDict
from uuid import uuid4
import httpx

from .ownership import OwnershipError


class ProjectTransferPolicy(TypedDict):
    visibility: str
    retainedGrantIds: list[str]
    preserveAccess: bool
    preserveExternalFilings: bool


class ProjectTransferPlan(TypedDict):
    apiVersion: int
    resource: dict
    ownerNamespaceId: str
    version: str
    manifest: list[dict]
    projectName: str
    noteNames: list[dict]
    resourceNames: NotRequired[list[dict]]
    # Dependent Dataset/Task/task-event rows ride with the Project outside the
    # manifest; the backend reports them as counts only.
    managedCounts: NotRequired[dict]
    executable: bool
    limits: dict


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _id(value):
    return isinstance(value, str) and re.fullmatch(r"[a-fA-F0-9]{24}", value) is not None


_RESOURCE_TYPES = {"project", "note", "artifact", "env", "annotation", "source"}
_MANAGED_COUNT_KEYS = ("datasets", "tasks", "taskEvents")
_MAX_SAFE_INTEGER = 2**53 - 1


def _integer(value, minimum=0):
    return type(value) is int and minimum <= value <= _MAX_SAFE_INTEGER


def _managed_counts(value):
    # Unknown additive keys from newer servers pass through; the three known
    # dependent counts are required whenever the object is present.
    return isinstance(value, dict) and all(_integer(value.get(key)) for key in _MANAGED_COUNT_KEYS)


def _ref(value):
    return (isinstance(value, dict) and set(value) == {"type", "id"}
            and value.get("type") in _RESOURCE_TYPES and _id(value.get("id")))


def _key(ref):
    return ref["type"], ref["id"].lower()


def validate_plan(plan, project_id):
    try:
        manifest, notes, limits = plan["manifest"], plan["noteNames"], plan["limits"]
        max_resources = limits.get("maxResources", limits["maxNotes"] + 1)
        valid = (
            type(plan["apiVersion"]) is int and plan["apiVersion"] == 1
            and _ref(plan["resource"]) and plan["resource"] == {"type": "project", "id": project_id}
            and _text(plan["ownerNamespaceId"]) and _text(plan["version"]) and _text(plan["projectName"])
            and isinstance(plan["executable"], bool)
            and isinstance(manifest, list) and isinstance(notes, list)
            and _integer(limits["maxNotes"]) and _integer(limits["maxTreeNodes"], 1)
            and _integer(max_resources, 1) and 1 <= len(manifest) <= max_resources
            and ("managedCounts" not in plan or _managed_counts(plan["managedCounts"]))
            and manifest[0] == plan["resource"]
            and all(_ref(ref) and (i == 0 or ref["type"] != "project") for i, ref in enumerate(manifest))
            and len({_key(ref) for ref in manifest}) == len(manifest)
        )
        if valid:
            note_refs = [ref for ref in manifest if ref["type"] == "note"]
            valid = (len(note_refs) <= limits["maxNotes"] and len(notes) == len(note_refs)
                     and all(_id(note["id"]) and _text(note["name"])
                             and {"type": "note", "id": note["id"]} in note_refs for note in notes)
                     and len({note["id"].lower() for note in notes}) == len(notes))
        if valid and "resourceNames" in plan:
            names, members = plan["resourceNames"], manifest[1:]
            valid = (isinstance(names, list) and len(names) == len(members)
                     and all(_id(row["id"]) and _text(row["name"])
                             and {"type": row["type"], "id": row["id"]} in members for row in names)
                     and len({_key(row) for row in names}) == len(members)
                     and all(any(row["type"] == "note" and row["id"] == note["id"] and row["name"] == note["name"]
                                 for row in names) for note in notes)
                     and (all(ref["type"] == "note" for ref in members) or "maxResources" in limits))
        elif valid:
            # Older Note-only servers provide the complete member names here.
            valid = all(ref["type"] == "note" for ref in manifest[1:])
    except (KeyError, TypeError, AttributeError, IndexError):
        valid = False
    if not valid:
        raise OwnershipError("INVALID_WHOLE_PROJECT_PLAN", 502)
    return plan


def _resource_counts(plan):
    return {kind: sum(ref["type"] == kind for ref in plan["manifest"])
            for kind in ("project", "note", "artifact", "env", "annotation", "source")}


def _validate_request(request, plan, destination_id):
    try:
        policy = request["policy"]
        names = request.get("destinationNames", [])
        valid = (
            type(request["apiVersion"]) is int and request["apiVersion"] == 1 and request["resource"] == plan["resource"]
            and request["expectedOwner"] == plan["ownerNamespaceId"] and request["expectedVersion"] == plan["version"]
            and request["destinationNamespaceId"] == destination_id and request["manifest"] == plan["manifest"]
            and _text(request["destinationName"]) and len(request["destinationName"]) <= 200
            and _text(request["idempotencyKey"]) and len(request["idempotencyKey"]) <= 200
            and policy["visibility"] == "private" and policy["retainedGrantIds"] == []
            and isinstance(policy["preserveAccess"], bool) and isinstance(policy["preserveExternalFilings"], bool)
            and isinstance(names, list) and len({row["resource"]["id"] for row in names}) == len(names)
            and all(row["resource"]["type"] == "note" and row["resource"] in plan["manifest"]
                    and _text(row["name"]) and len(row["name"]) <= 200 for row in names)
        )
    except (KeyError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise ValueError("Reviewed request must match the complete Project plan and private transfer policy")


def _validate_preview(preview, request, modern_plan=False):
    try:
        summary, blockers = preview["summary"], preview["blockers"]
        valid = (
            type(preview["apiVersion"]) is int and preview["apiVersion"] == 1 and preview["resource"] == request["resource"]
            and preview["sourceNamespaceId"] == request["expectedOwner"]
            and preview["destinationNamespaceId"] == request["destinationNamespaceId"]
            and preview["expectedVersion"] == request["expectedVersion"] and preview["manifest"] == request["manifest"]
            and _text(preview["previewHash"]) and isinstance(blockers, list)
            and all(_text(row["code"]) and _text(row["action"]) for row in blockers)
            and summary["includedCount"] == len(request["manifest"])
            and all(_integer(summary[key]) for key in ("includedCount", "retainedFilings", "detachedFilings"))
            and summary["becomesPrivate"] is True
            and ("managedCounts" not in summary or _managed_counts(summary["managedCounts"]))
            and (not modern_plan and "detachedOtherFilings" not in summary
                 or _integer(summary.get("detachedOtherFilings")) and summary["detachedOtherFilings"] <= summary["detachedFilings"])
        )
    except (KeyError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise OwnershipError("INVALID_TRANSFER_PREVIEW", 502)


_STATES = {"pending", "preparing", "committing", "completed", "rejected", "cancelled", "expired", "aborting", "aborted", "recovery-required"}


def _validate_operation(operation, request):
    try:
        valid = (type(operation["apiVersion"]) is int and operation["apiVersion"] == 1 and _id(operation["id"]) and operation["state"] in _STATES
                 and operation["sourceNamespaceId"] == request["expectedOwner"]
                 and operation["destinationNamespaceId"] == request["destinationNamespaceId"]
                 and operation["resource"] == request["resource"] and operation["previewHash"] == request["previewHash"])
    except (KeyError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise OwnershipError("INVALID_OPERATION_RESPONSE_REUSE_SAVED_REQUEST", 502)


def move_project(projects, namespace, project, to_namespace, *, dry_run=True, confirm=False,
                 preview_hash=None, idempotency_key=None, preserve_access=False,
                 preserve_external_filings=False, destination_name=None, destination_names=None,
                 reviewed=None, accept_if_owner=False, wait=False, wait_timeout=30):
    """Preview by default. Execution requires the exact hash/key or reviewed bundle.

    ``reviewed`` replays a previous returned review unchanged. It is the safe
    recovery path after an ambiguous create result; no write is retried here.
    Acceptance is opt-in and requires current destination-owner authority.
    """
    if not all(_text(value) for value in (namespace, project, to_namespace)):
        raise ValueError("Choose a source namespace, Project, and destination namespace")
    source, destination = namespace.strip().lstrip("@"), to_namespace.strip().lstrip("@")
    if not source or not destination or not project.strip() or source == destination:
        raise ValueError("Choose a source namespace, Project, and different destination namespace")
    if dry_run and confirm:
        raise ValueError("dry_run cannot submit a transfer")
    if wait and not accept_if_owner:
        raise ValueError("wait requires explicit accept_if_owner")
    if not isinstance(wait_timeout, (int, float)) or not 0 <= wait_timeout <= 60:
        raise ValueError("wait_timeout must be between 0 and 60 seconds")
    if not dry_run and not confirm:
        raise ValueError("Submission requires explicit confirm=True")
    if not dry_run and reviewed is None and (not _text(preview_hash) or not _text(idempotency_key)):
        raise ValueError("Submission requires a reviewed preview_hash and stable idempotency_key")
    if reviewed is not None and (dry_run or not confirm or destination_name is not None or destination_names is not None or idempotency_key is not None):
        raise ValueError("Replay the reviewed bundle unchanged with dry_run=False, confirm=True")
    if type(preserve_access) is not bool or type(preserve_external_filings) is not bool:
        raise ValueError("Preservation flags must be booleans")
    if destination_names is not None and not isinstance(destination_names, dict):
        raise ValueError("destination_names must map owned Note IDs to explicit new names")
    ownership = projects.client.ownership
    target = ownership.capabilities(destination)
    if not _text(target.get("namespaceId")):
        raise OwnershipError("INVALID_DESTINATION_NAMESPACE", 502)
    if reviewed is not None:
        review = copy.deepcopy(reviewed)
        if (not isinstance(review, dict) or review.get("source") != source or review.get("destination") != destination
                or review.get("ready") is not True
                or not all(isinstance(review.get(key), dict) for key in ("plan", "request", "preview"))
                or not isinstance(review["plan"].get("resource"), dict)
                or review["plan"].get("projectName") != project.strip()):
            raise ValueError("Replay requires the matching complete Project review bundle")
        plan, request, preview = review["plan"], review["request"], review["preview"]
        validate_plan(plan, plan.get("resource", {}).get("id"))
        authority = ownership.capabilities(source)
        if authority.get("namespaceId") != plan["ownerNamespaceId"]:
            raise OwnershipError("INVALID_SOURCE_NAMESPACE", 502)
        _validate_request(request, plan, target["namespaceId"])
        if (preserve_access and not request.get("policy", {}).get("preserveAccess")
                or preserve_external_filings and not request.get("policy", {}).get("preserveExternalFilings")):
            raise ValueError("Preservation flags differ from the saved reviewed request")
    else:
        plan = projects.transfer_plan(source, project)
        request = {
            "apiVersion": 1, "resource": plan["resource"], "expectedOwner": plan["ownerNamespaceId"],
            "expectedVersion": plan["version"], "destinationNamespaceId": target["namespaceId"],
            "destinationName": destination_name if destination_name is not None else plan["projectName"],
            "manifest": plan["manifest"], "idempotencyKey": idempotency_key or str(uuid4()),
            "policy": {"visibility": "private", "retainedGrantIds": [], "preserveAccess": preserve_access,
                       "preserveExternalFilings": preserve_external_filings},
        }
        if destination_names is not None:
            request["destinationNames"] = [{"resource": {"type": "note", "id": resource_id}, "name": name}
                                           for resource_id, name in destination_names.items()]
        _validate_request(request, plan, target["namespaceId"])
        preview = ownership.preview(source, request)
        review = {"source": source, "destination": destination, "plan": plan, "request": request, "preview": preview,
                  "ready": plan["executable"] and not preview.get("blockers")}
    if target["namespaceId"] == plan["ownerNamespaceId"]:
        raise ValueError("Destination is the current owner")
    _validate_request(request, plan, target["namespaceId"])
    _validate_preview(preview, request, modern_plan="resourceNames" in plan)
    review["resourceCounts"] = _resource_counts(plan)
    reviewed_hash = preview_hash if preview_hash is not None else request.get("previewHash")
    request["previewHash"] = preview["previewHash"]
    if dry_run:
        return review
    if not plan["executable"] or preview["blockers"]:
        raise OwnershipError("TRANSFER_EXECUTION_DISABLED", 422)
    if not _text(reviewed_hash) or reviewed_hash != preview["previewHash"]:
        raise OwnershipError("STALE_PREVIEW", 409)
    destination_authority = preview.get("destinationAuthority", {})
    if accept_if_owner and (destination_authority.get("allowed") is not True
            or destination_authority.get("namespaceId") != target["namespaceId"]
            or destination_authority.get("action") != "ownership.accept"
            or reviewed is not None and target.get("capabilities", {}).get("ownership.accept", {}).get("allowed") is not True):
        raise OwnershipError("DESTINATION_ACCEPTANCE_NOT_AUTHORIZED", 403)
    operation = ownership.create(source, request)
    _validate_operation(operation, request)
    if accept_if_owner and operation["state"] == "pending":
        operation = ownership.accept(destination, operation["id"])
        _validate_operation(operation, request)
    deadline = time.monotonic() + wait_timeout
    while wait and operation["state"] in {"preparing", "committing", "aborting"} and time.monotonic() < deadline:
        try:
            operation = ownership.status(destination, operation["id"], timeout=max(0.001, min(30, deadline - time.monotonic())))
        except httpx.TimeoutException:
            break
        _validate_operation(operation, request)
        if operation["state"] not in {"preparing", "committing", "aborting"} or time.monotonic() >= deadline:
            break
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    command = (f"dreamlake ownership accept {operation['id']} --namespace {shlex.quote(destination)}"
               if operation["state"] == "pending" else f"dreamlake ownership status {operation['id']} --namespace {shlex.quote(source)}")
    return {**review, "operation": operation, "nextAction": command,
            "timedOut": bool(wait and operation["state"] in {"preparing", "committing", "aborting"})}
