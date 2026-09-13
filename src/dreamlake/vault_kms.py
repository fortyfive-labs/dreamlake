"""Metadata-only personal prefix KMS management; operator provisioning is separate."""
import re
from .vault import VaultError, VaultWriteError, VaultHttpError, _write_request_id


def _prefix(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*/?", value):
        raise VaultError("An explicit personal prefix is required")
    return value.rstrip("/")


def _ref(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise VaultError("Invalid operator key reference")
    return value


def _view(value, operation=False):
    if not isinstance(value, dict) or value.get("provider") not in ("aws-kms", "gcp-kms"):
        raise VaultError("Invalid KMS response")
    result = {"prefix": _prefix(value.get("prefix")), "provider": value["provider"]}
    if operation:
        if value.get("state") != "committed" or not isinstance(value.get("committedAt"), str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", value["committedAt"]):
            raise VaultError("Invalid KMS receipt")
        return dict(result, keyRef=_ref(value.get("keyRef")), requestId=_write_request_id(value.get("requestId")), state="committed", committedAt=value["committedAt"])
    result["governingPrefix"] = None if value.get("governingPrefix") is None else _prefix(value["governingPrefix"])
    result["keyRef"] = None if value.get("keyRef") is None else _ref(value["keyRef"])
    if not isinstance(value.get("overlappingPrefixes"), list):
        raise VaultError("Invalid KMS overlaps")
    result["overlappingPrefixes"] = [_prefix(p) for p in value["overlappingPrefixes"]]
    for key in ("hasEntries", "hasWriteReceipts", "hasHotpReceipts", "canActivate", "migrationRequired", "migrationSupported"):
        if key in value:
            if type(value[key]) is not bool:
                raise VaultError("Invalid KMS state")
            result[key] = value[key]
    if "selectedKeyRef" in value:
        if value.get("readiness") != "not-probed":
            raise VaultError("Invalid KMS preview")
        result.update(selectedKeyRef=_ref(value["selectedKeyRef"]), readiness="not-probed")
    if "availableKeys" in value:
        keys = value["availableKeys"]
        if not isinstance(keys, list) or len(keys) > 1000:
            raise VaultError("Invalid KMS registry")
        result["availableKeys"] = []
        for key in keys:
            if not isinstance(key, dict) or key.get("provider") not in ("aws-kms", "gcp-kms"):
                raise VaultError("Invalid KMS registry")
            result["availableKeys"].append({"ref": _ref(key.get("ref")), "provider": key["provider"]})
    return result


class VaultKms:
    def __init__(self, vault):
        self._vault = vault

    def show(self, *, prefix):
        """Inspect governing prefix and tenant-authorized operator key refs."""
        return _view(self._vault._request("GET", "/v1/vault/kms", params={"prefix": _prefix(prefix)}))

    def preview(self, *, prefix, key_ref):
        """Read-only eligibility; does not probe KMS or reserve the prefix."""
        return _view(self._vault._request("POST", "/v1/vault/kms/preview", json={"prefix": _prefix(prefix), "keyRef": _ref(key_ref)}))

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

    def status(self, *, request_id):
        """Retrieve committed metadata. No receipt is not a no-commit guarantee."""
        request_id = _write_request_id(request_id)
        result = _view(self._vault._request("GET", "/v1/vault/kms/operations/" + request_id), True)
        if result["requestId"] != request_id:
            raise VaultError("Mismatched KMS receipt")
        return result
