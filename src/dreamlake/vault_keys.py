"""Scoped vault bearer key management. Tokens are deliberately absent from repr."""
from dataclasses import dataclass, field
import re
from uuid import uuid4
from .vault import VaultError, resolve_selector


def duration_seconds(value, maximum=9007199254740991):
    if type(value) is int:
        seconds = value
    elif isinstance(value, str) and (match := re.fullmatch(r"([1-9][0-9]*)(s|m|h|d)?", value)):
        seconds = int(match[1]) * {None: 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[match[2]]
    else:
        raise VaultError("Invalid TTL")
    if not 1 <= seconds <= maximum:
        raise VaultError("Invalid TTL")
    return seconds


def _key_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9-]{36}", value):
        raise VaultError("Invalid key ID")
    return value


def key_metadata(value):
    if not isinstance(value, dict):
        raise VaultError("Invalid vault key response")
    _key_id(value.get("id"))
    if not isinstance(value.get("scopes"), list):
        raise VaultError("Invalid vault key response")
    scopes = []
    for scope in value["scopes"]:
        if not isinstance(scope, dict) or not isinstance(scope.get("name"), str) or not isinstance(scope.get("entryId"), str):
            raise VaultError("Invalid vault key response")
        _key_id(scope["entryId"])
        resolve_selector(scope["name"])
        fields = scope.get("fields")
        if fields is not None and not (isinstance(fields, list) and all(isinstance(f, str) and re.fullmatch(r"[A-Za-z0-9_-]+", f) for f in fields)):
            raise VaultError("Invalid vault key response")
        scopes.append({"name": scope["name"], "entryId": scope["entryId"], "fields": fields})
    if not isinstance(value.get("createdAt"), str) or not isinstance(value.get("expiresAt"), str):
        raise VaultError("Invalid vault key response")
    for key in ("createdAt", "expiresAt", "renewUntil", "consumedAt", "revokedAt"):
        if key not in value or value[key] is not None and (not isinstance(value[key], str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z", value[key])):
            raise VaultError("Invalid vault key response")
    if type(value.get("maxTtlSeconds")) is not int or value["maxTtlSeconds"] < 1 or any(type(value.get(key)) is not bool for key in ("renewable", "oneTime")):
        raise VaultError("Invalid vault key response")
    result = {k: value[k] for k in ("id", "createdAt", "expiresAt", "maxTtlSeconds", "renewable", "renewUntil", "oneTime", "consumedAt", "revokedAt")}
    request_id = value.get("requestId")
    if request_id is not None and (not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id)):
        raise VaultError("Invalid vault key response")
    if request_id is not None:
        result["requestId"] = request_id
    result["scopes"] = scopes
    return result


@dataclass(frozen=True)
class IssuedVaultKey:
    key: dict
    _token: str | None = field(repr=False)
    replayed: bool = False

    @property
    def token(self):
        if self._token is None:
            raise VaultError("Original token unavailable; revoke and reissue with a new request ID if delivery was lost")
        return self._token


class VaultKeys:
    def __init__(self, vault):
        self._vault = vault

    def create(self, name=None, *, credential=None, ttl, prefix="", renewable=False, one_time=False, max_lifetime=None, request_id=None):
        """Issue scoped retrieval key, returned once as result.token (repr redacted).

        Explicitly persist or hand off token; ordinary serialization/logging of
        token remains the caller's responsibility. No automatic retry on failure.
        """
        if name is not None and credential is not None:
            raise VaultError("Choose name or credential")
        selected = name if name is not None else credential
        selected = [selected] if isinstance(selected, str) else selected
        if not isinstance(selected, list) or not selected:
            raise VaultError("Provide entry or field scopes")
        selectors = [resolve_selector(n, prefix) for n in selected]
        if any("=" in s for s in selectors):
            raise VaultError("Output aliases are not key scopes")
        if type(renewable) is not bool or type(one_time) is not bool or renewable and one_time:
            raise VaultError("Invalid renewal/one-time options")
        ttl_seconds = duration_seconds(ttl)
        data = dict(selectors=selectors, ttlSeconds=ttl_seconds, renewable=renewable, oneTime=one_time)
        if max_lifetime is not None:
            maximum = duration_seconds(max_lifetime, 2147483647)
            if not renewable or maximum < ttl_seconds:
                raise VaultError("Invalid maximum lifetime")
            data["maxLifetimeSeconds"] = maximum
        request_id = str(uuid4()) if request_id is None else request_id
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id):
            raise VaultError("Invalid request ID")
        try:
            result = self._vault._request("POST", "/v1/vault/keys", json=data, headers={"Idempotency-Key": request_id})
        except VaultError:
            raise VaultError(f"Vault key issuance failed; reconcile request ID {request_id} before retrying") from None
        try:
            if not isinstance(result, dict):
                raise VaultError("Invalid vault key response")
            metadata = key_metadata(result.get("key"))
            if metadata.get("requestId") != request_id or type(result.get("replayed")) is not bool:
                raise VaultError("Invalid vault key response")
            if result.get("tokenUnavailable") is True and "token" in result:
                raise VaultError("Invalid vault key response")
            if result["replayed"]:
                if result.get("tokenUnavailable") is not True:
                    raise VaultError("Invalid vault key response")
                return IssuedVaultKey(metadata, None, replayed=True)
            token = result.get("token")
            if not isinstance(token, str) or not re.fullmatch(r"dlv1_[A-Za-z0-9_-]{43}", token):
                raise VaultError("Invalid vault key response")
            return IssuedVaultKey(metadata, token)
        except VaultError:
            raise VaultError(f"Invalid vault key response; reconcile request ID {request_id} before retrying") from None


    def list(self):
        """Metadata only; issued tokens cannot be retrieved again."""
        result = self._vault._request("GET", "/v1/vault/keys")
        if not isinstance(result, dict) or not isinstance(result.get("keys"), list):
            raise VaultError("Invalid vault key response")
        return [key_metadata(k) for k in result["keys"]]

    def renew(self, key_id, *, ttl):
        result = self._vault._request("POST", f"/v1/vault/keys/{_key_id(key_id)}/renew", json={"ttlSeconds": duration_seconds(ttl)})
        return key_metadata(result.get("key") if isinstance(result, dict) else None)

    def revoke(self, key_id):
        result = self._vault._request("DELETE", f"/v1/vault/keys/{_key_id(key_id)}")
        return key_metadata(result.get("key") if isinstance(result, dict) else None)
