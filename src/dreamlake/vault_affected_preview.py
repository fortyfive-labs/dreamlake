"""Bounded policy metadata only; never ciphertext provenance or execution authority."""
import re
from datetime import datetime
from .vault import VaultError


def _fail():
    raise VaultError("Invalid affected-entry preview")


def _matches(value, pattern):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _path(value):
    return isinstance(value, str) and len(value) <= 512 and _matches(value, r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*")


def _within(name, prefix):
    return name == prefix or name.startswith(prefix + "/")


def _date(value):
    if value is None:
        return None
    if not _matches(value, r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z"):
        _fail()
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail()
    return value


def affected_options(limit, cursor):
    if limit is None and cursor is None:
        return None
    if type(limit) is not int or not 1 <= limit <= 200 or (cursor is not None and not _matches(cursor, r"[A-Za-z0-9_-]{1,4096}")):
        _fail()
    return dict(limit=limit, **({"cursor": cursor} if cursor is not None else {}))


def affected_view(value, prefix, key_ref):
    if not isinstance(value, dict) or type(value.get("schemaVersion")) is not int or value["schemaVersion"] != 1 or value.get("consistency") != "page-snapshot" or value.get("includesRetired") is not True or value.get("comparison") != "policy-only" or "blockedReason" not in value or value["blockedReason"] not in (None, "active-migration", "overlapping-boundary") or not isinstance(value.get("entries"), list) or len(value["entries"]) > 200 or "nextCursor" not in value or (value["nextCursor"] is not None and not _matches(value["nextCursor"], r"[A-Za-z0-9_-]{1,4096}")):
        _fail()
    entries, previous = [], None
    for row in value["entries"]:
        if not isinstance(row, dict) or not _matches(row.get("id"), r"[A-Za-z0-9_-]{1,128}") or not _path(row.get("name")) or not _within(row["name"], prefix) or type(row.get("revision")) is not int or not 1 <= row["revision"] <= 9007199254740991 or row.get("type") not in ("string", "totp", "hotp"):
            _fail()
        position = (row["name"], row["id"])
        if previous is not None and position <= previous:
            _fail()
        previous = position
        current, proposed = row.get("currentPolicy"), row.get("proposedPolicy")
        if not isinstance(current, dict) or any(k not in current for k in ("prefix", "keyRef", "provider", "epoch")) or (current["prefix"] is not None and not (_path(current["prefix"]) and _within(row["name"], current["prefix"]))) or (current["keyRef"] is not None and not _matches(current["keyRef"], r"[A-Za-z0-9_-]{1,64}")) or current["provider"] not in ("aws-kms", "gcp-kms") or (current["epoch"] is not None and not _matches(current["epoch"], r"[A-Za-z0-9_-]{1,128}")):
            _fail()
        if "proposedPolicy" not in row or (proposed is not None if value["blockedReason"] is not None else not isinstance(proposed, dict) or proposed.get("prefix") != prefix or proposed.get("keyRef") != key_ref or proposed.get("provider") not in ("aws-kms", "gcp-kms")):
            _fail()
        if any(k not in row for k in ("deleteAt", "purgeAt", "expiresAt")):
            _fail()
        entries.append(dict(id=row["id"], name=row["name"], revision=row["revision"], type=row["type"], deleteAt=_date(row["deleteAt"]), purgeAt=_date(row["purgeAt"]), expiresAt=_date(row["expiresAt"]), currentPolicy={k: current[k] for k in ("prefix", "keyRef", "provider", "epoch")}, proposedPolicy=None if proposed is None else {k: proposed[k] for k in ("prefix", "keyRef", "provider")}))
    if value["nextCursor"] is not None and not entries:
        _fail()
    return dict(schemaVersion=1, consistency="page-snapshot", includesRetired=True, comparison="policy-only", blockedReason=value["blockedReason"], entries=entries, nextCursor=value["nextCursor"])
