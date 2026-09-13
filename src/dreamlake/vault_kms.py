"""Metadata-only personal prefix KMS management; operator provisioning is separate."""
import re
from datetime import datetime
from .vault import VaultError, VaultWriteError, VaultHttpError, _write_request_id


def _prefix(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*/?", value):
        raise VaultError("An explicit personal prefix is required")
    return value.rstrip("/")


def _ref(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise VaultError("Invalid operator key reference")
    return value


def _instant(raw):
    if not isinstance(raw, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", raw):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _view(value, operation=False):
    if not isinstance(value, dict) or value.get("provider") not in ("aws-kms", "gcp-kms"):
        raise VaultError("Invalid KMS response")
    result = {"prefix": _prefix(value.get("prefix")), "provider": value["provider"]}
    if operation:
        if value.get("state") != "committed" or not _instant(value.get("committedAt")):
            raise VaultError("Invalid KMS receipt")
        return dict(result, keyRef=_ref(value.get("keyRef")), requestId=_write_request_id(value.get("requestId")), state="committed", committedAt=value["committedAt"])
    result["governingPrefix"] = None if value.get("governingPrefix") is None else _prefix(value["governingPrefix"])
    result["keyRef"] = None if value.get("keyRef") is None else _ref(value["keyRef"])
    if not isinstance(value.get("overlappingPrefixes"), list):
        raise VaultError("Invalid KMS overlaps")
    result["overlappingPrefixes"] = [_prefix(p) for p in value["overlappingPrefixes"]]
    for key in ("hasEntries", "hasWriteReceipts", "hasHotpReceipts", "canActivate", "migrationRequired", "migrationSupported", "canMigrate"):
        if key in value:
            if type(value[key]) is not bool:
                raise VaultError("Invalid KMS state")
            result[key] = value[key]
    for key in ("entryCount", "writeReceiptCount", "hotpReceiptCount"):
        if key in value:
            if type(value[key]) is not int or not 0 <= value[key] <= 9007199254740991:
                raise VaultError("Invalid KMS count")
            result[key] = value[key]
    if "selectedKeyRef" in value:
        if value.get("readiness") != "not-probed":
            raise VaultError("Invalid KMS preview")
        result.update(selectedKeyRef=_ref(value["selectedKeyRef"]), readiness="not-probed")
    if "availableKeys" in value:
        keys = value["availableKeys"]
        if not isinstance(keys, list) or len(keys) > 1001:
            raise VaultError("Invalid KMS registry")
        result["availableKeys"] = []
        for key in keys:
            if not isinstance(key, dict) or key.get("provider") not in ("aws-kms", "gcp-kms"):
                raise VaultError("Invalid KMS registry")
            result["availableKeys"].append({"ref": _ref(key.get("ref")), "provider": key["provider"]})
    return result


def _migration_view(value):
    if not isinstance(value, dict) or value.get("provider") not in ("aws-kms", "gcp-kms") or value.get("state") not in ("migrating", "completed"):
        raise VaultError("Invalid KMS migration response")
    if type(value.get("migrated")) is not int or value["migrated"] < 0 or value["migrated"] > 9007199254740991 or not _instant(value.get("startedAt")):
        raise VaultError("Invalid KMS migration progress")
    completion_valid = (_instant(value.get("completedAt")) is not None and _instant(value["completedAt"]) >= _instant(value["startedAt"])) if value["state"] == "completed" else value.get("completedAt") is None
    if not completion_valid:
        raise VaultError("Invalid KMS migration completion")
    return dict(prefix=_prefix(value.get("prefix")), keyRef=_ref(value.get("keyRef")), requestId=_write_request_id(value.get("requestId")), provider=value["provider"], state=value["state"], migrated=value["migrated"], startedAt=value["startedAt"], completedAt=value.get("completedAt"))


class VaultKms:
    def __init__(self, vault):
        self._vault = vault

    def show(self, *, prefix):
        """Inspect governing prefix and tenant-authorized operator key refs."""
        prefix = _prefix(prefix)
        result = _view(self._vault._request("GET", "/v1/vault/kms", params={"prefix": prefix}))
        if result["prefix"] != prefix:
            raise VaultError("Mismatched KMS response")
        return result

    def preview(self, *, prefix, key_ref):
        """Read-only eligibility; does not probe KMS or reserve the prefix."""
        prefix, key_ref = _prefix(prefix), _ref(key_ref)
        result = _view(self._vault._request("POST", "/v1/vault/kms/preview", json={"prefix": prefix, "keyRef": key_ref}))
        if result["prefix"] != prefix or result.get("selectedKeyRef") != key_ref:
            raise VaultError("Mismatched KMS response")
        return result

    def activate(self, *, prefix, key_ref, request_id):
        """Activate only an empty prefix. Persist/reuse the same tuple on retry.

        Never prompts or provisions cloud permissions. Unknown outcomes retain
        request_id; status() returning404 does not prove an in-flight commit failed.
        """
        prefix, key_ref, request_id = _prefix(prefix), _ref(key_ref), _write_request_id(request_id)
        try:
            result = _view(self._vault._request("PUT", "/v1/vault/kms", json={"prefix": prefix, "keyRef": key_ref}, headers={"Idempotency-Key": request_id}), True)
            if (result["prefix"], result["keyRef"], result["requestId"]) != (prefix, key_ref, request_id):
                raise VaultError("Mismatched KMS receipt")
            return result
        except VaultError as error:
            raise VaultWriteError(request_id, error.status if isinstance(error, VaultHttpError) else None) from None

    def migrate(self, *, prefix, key_ref, request_id):
        """Start resumable migration; persist identical prefix/key/ID before sending.

        Changes the future-write key after a guarded commit. Call resume() for
        bounded ciphertext batches; no background job or OTP generation occurs.
        """
        prefix, key_ref, request_id = _prefix(prefix), _ref(key_ref), _write_request_id(request_id)
        try:
            result = _migration_view(self._vault._request("POST", "/v1/vault/kms/migrations", json={"prefix": prefix, "keyRef": key_ref}, headers={"Idempotency-Key": request_id}))
            if (result["prefix"], result["keyRef"], result["requestId"]) != (prefix, key_ref, request_id):
                raise VaultError("Mismatched KMS migration receipt")
            return result
        except VaultError as error:
            raise VaultWriteError(request_id, error.status if isinstance(error, VaultHttpError) else None) from None

    def resume(self, *, request_id, limit=50, prefix=None):
        """Attempt at most limit ciphertext rows (1-100). Retain ID on uncertainty."""
        request_id = _write_request_id(request_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise VaultError("Invalid KMS migration limit")
        prefix = _prefix(prefix) if prefix is not None else None
        body = {"limit": limit, **({"expectedPrefix": prefix} if prefix is not None else {})}
        try:
            result = _migration_view(self._vault._request("POST", "/v1/vault/kms/migrations/" + request_id + "/resume", json=body))
            if result["requestId"] != request_id or (prefix is not None and result["prefix"] != prefix):
                raise VaultError("Mismatched KMS migration receipt")
            return result
        except VaultError as error:
            raise VaultWriteError(request_id, error.status if isinstance(error, VaultHttpError) else None) from None

    def status(self, *, request_id, prefix=None):
        """Retrieve activation/migration metadata. Absence is not proof of no commit."""
        request_id = _write_request_id(request_id)
        prefix = _prefix(prefix) if prefix is not None else None
        value = self._vault._request("GET", "/v1/vault/kms/operations/" + request_id)
        result = _view(value, True) if isinstance(value, dict) and value.get("state") == "committed" else _migration_view(value)
        if result["requestId"] != request_id or (prefix is not None and result["prefix"] != prefix):
            raise VaultError("Mismatched KMS receipt")
        return result
