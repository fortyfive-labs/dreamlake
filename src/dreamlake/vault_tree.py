"""Owner-only policy tree metadata; never reads values or prompts."""
import re
from datetime import datetime
from .vault import VaultError


def validate_tree(value, prefix, limit):
    def fail():
        raise VaultError("Invalid vault tree metadata")

    def obj(v, keys):
        if not isinstance(v, dict) or set(v) - set(keys):
            fail()

    def path(v):
        return isinstance(v, str) and len(v) <= 512 and re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", v)

    def within(v, p):
        return v == p or v.startswith(p + "/")

    def text(v, maximum=2048):
        return isinstance(v, str) and len(v) <= maximum

    obj(value, ("schemaVersion", "prefix", "observedAt", "rows", "nextCursor"))
    if type(value.get("schemaVersion")) is not int or value["schemaVersion"] != 1 or value.get("prefix") != prefix or not text(value.get("observedAt")):
        fail()
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z", value["observedAt"]):
        fail()
    try:
        datetime.fromisoformat(value["observedAt"].replace("Z", "+00:00"))
    except ValueError:
        fail()
    rows = value.get("rows")
    if not isinstance(rows, list) or len(rows) > limit:
        fail()
    previous = ""
    for row in rows:
        obj(row, ("kind", "path", "parentPath", "entry", "policy"))
        if not {"kind", "path", "parentPath", "policy"} <= set(row):
            fail()
        name = row.get("path")
        if row.get("kind") not in ("entry", "prefix") or not path(name) or not within(name, prefix) or row.get("parentPath") != (None if name == prefix else name.rsplit("/", 1)[0]):
            fail()
        order = name + "\0" + row["kind"]
        if previous and order <= previous:
            fail()
        previous = order
        if row["kind"] == "entry":
            entry = row.get("entry")
            obj(entry, ("name", "type", "env", "keyName", "fileName", "deleteAt", "purgeAt", "expiresAt", "revision"))
            revision = entry.get("revision")
            if entry.get("name") != name or entry.get("type") not in ("string", "totp", "hotp") or not (type(revision) is int and 0 < revision <= 9007199254740991 or isinstance(revision, str) and re.fullmatch(r"[1-9][0-9]*", revision)):
                fail()
            if any(k != "revision" and v is not None and not text(v) for k, v in entry.items()):
                fail()
        elif "entry" in row:
            fail()
        policy = row.get("policy")
        keys = ("state", "reason", "governingPrefix", "keyRef", "keyId", "provider", "epoch", "migration")
        obj(policy, keys)
        if set(policy) != set(keys) or policy["state"] not in ("known", "unknown", "unavailable"):
            fail()
        governed = policy["governingPrefix"]
        if governed is not None and (not path(governed) or not within(name, governed)):
            fail()
        if any(policy[k] is not None and not text(policy[k]) for k in ("reason", "keyRef", "keyId", "provider", "epoch")):
            fail()
        if policy["state"] == "known" and (not policy["keyId"] or not policy["keyRef"] or policy["provider"] not in ("aws-kms", "gcp-kms")):
            fail()
        if policy["state"] != "known" and any(policy[k] is not None for k in ("keyId", "keyRef", "provider")):
            fail()
        migration = policy["migration"]
        obj(migration, ("state", "requestId"))
        if set(migration) != {"state", "requestId"} or migration["state"] not in ("none", "migrating", "completed", "unknown") or migration["requestId"] is not None and not text(migration["requestId"], 128):
            fail()
    cursor = value.get("nextCursor")
    if "nextCursor" not in value or cursor is not None and (not isinstance(cursor, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,2048}", cursor) or not rows):
        fail()
    return value


def tree(vault, *, prefix, limit=100, cursor=None):
    if not isinstance(prefix, str) or len(prefix) > 512 or not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", prefix):
        raise VaultError("Tree requires an explicit prefix")
    if type(limit) is not int or not 1 <= limit <= 200:
        raise VaultError("Invalid page size")
    params = {"prefix": prefix, "limit": str(limit)}
    if cursor is not None:
        if not isinstance(cursor, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,2048}", cursor):
            raise VaultError("Invalid cursor")
        params["cursor"] = cursor
    return validate_tree(vault._request("GET", "/v1/vault/tree", params=params), prefix, limit)
