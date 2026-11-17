import json

import httpx
import pytest

from dreamlake.api._client import DreamLakeClient
from dreamlake.api.connections import (
    ConnectionApiError,
    ConnectionConflict,
    ConnectionInvalid,
    ConnectionNotFound,
)
from dreamlake.api.sources import SourceError

CID, OP, SID = "c" * 24, "a" * 24, "d" * 24
SECRET = "entry-sentinel-secret-7f3c9a"
# Shapes as dreamlake-server src/connections/v1 publishes them.
DETAIL = {"id": CID, "label": "Team bucket", "connectorKind": "s3", "authKind": "access-key",
          "settingsRevision": 2, "lifecycle": "active", "configured": {"secretAccessKey": True}}
CONNECTORS = {"connectors": [{"kind": "s3", "category": "object-store", "product": {"mode": "select", "field": "profileId"},
                              "auth": [{"authKind": "access-key", "label": "Access key", "mode": "fields", "create": [], "reconnect": []}]}]}
ENTRY = {"operationId": OP, "namespace": "acme", "intent": "create", "connectorKind": "s3", "authKind": "access-key",
         "connectionId": None, "expectedSettingsRevision": None, "suggestedLabel": None, "state": "pending",
         "expiresAt": "2026-11-17T00:30:00.000Z", "url": f"https://app.test/acme/connections?entry={OP}", "resultConnectionId": None}


def fixture(handler):
    return DreamLakeClient(dl_url="https://fixture.test", token="synthetic", transport=httpx.MockTransport(handler))


def envelope(status, code, **details):
    return httpx.Response(status, json={"error": {"code": code, "message": "refused", "retryable": False,
                                                  "requestId": "req-1", **({"details": details} if details else {})}})


def test_every_operation_hits_the_canonical_route_with_backend_bodies_and_keys():
    calls = []

    def serve(req):
        calls.append(req)
        path = req.url.path
        if path.endswith("/connection-connectors"):
            return httpx.Response(200, json=CONNECTORS)
        if "connection-entry-operations" in path or "connection-oauth-operations" in path:
            return httpx.Response(201 if req.method == "POST" else 200, json=ENTRY)
        if req.method == "DELETE":
            return httpx.Response(204)
        if path.endswith(":check"):
            return httpx.Response(200, json={"status": "unverified", "retryable": False, "checkedAt": "2026-11-17T00:00:00.000Z"})
        if path.endswith("/affected-sources") or (req.method == "GET" and path.endswith("/connections")):
            return httpx.Response(200, json={"items": [], "nextCursor": None})
        return httpx.Response(201 if req.method == "POST" and path.endswith("/connections") else 200, json=DETAIL)

    c = fixture(serve).connections
    assert c.connectors("acme") == CONNECTORS["connectors"]
    c.list("acme", limit=5, label_contains="team", lifecycle="active")
    c.get("acme", CID)
    c.affected_sources("acme", CID, limit=10)
    c.check("acme", CID)
    c.create("acme", label="Team bucket", connector_kind="s3", auth_kind="access-key", idempotency_key="k1",
             credential={"accessKeyId": "AKIAFAKE", "secretAccessKey": SECRET}, private_config={"profileId": "aws"})
    c.rename("acme", CID, label="Archive", expected_settings_revision=2, idempotency_key="k2")
    c.reconnect("acme", CID, credential={"secretAccessKey": SECRET}, expected_settings_revision=2, idempotency_key="k3")
    c.disable("acme", CID, expected_settings_revision=3, idempotency_key="k4")
    c.enable("acme", CID, expected_settings_revision=4, idempotency_key="k5")
    assert c.delete("acme", CID, expected_settings_revision=5, idempotency_key="k6") is None
    c.oauth_begin("acme", intent="create", connector_kind="huggingface", label="Hub")
    c.oauth_status("acme", OP)
    assert c.entry_begin("acme", intent="create", connector_kind="s3", auth_kind="access-key", suggested_label="Team bucket")["url"].endswith(OP)
    c.entry_status("acme", OP)
    c.entry_cancel("acme", OP)

    base = "/namespaces/acme"
    assert [(r.method, r.url.path) for r in calls] == [
        ("GET", f"{base}/connection-connectors"),
        ("GET", f"{base}/connections"),
        ("GET", f"{base}/connections/{CID}"),
        ("GET", f"{base}/connections/{CID}/affected-sources"),
        ("POST", f"{base}/connections/{CID}:check"),
        ("POST", f"{base}/connections"),
        ("PATCH", f"{base}/connections/{CID}"),
        ("POST", f"{base}/connections/{CID}:reconnect"),
        ("POST", f"{base}/connections/{CID}:disable"),
        ("POST", f"{base}/connections/{CID}:enable"),
        ("DELETE", f"{base}/connections/{CID}"),
        ("POST", f"{base}/connection-oauth-operations"),
        ("GET", f"{base}/connection-oauth-operations/{OP}"),
        ("POST", f"{base}/connection-entry-operations"),
        ("GET", f"{base}/connection-entry-operations/{OP}"),
        ("POST", f"{base}/connection-entry-operations/{OP}:cancel"),
    ]
    assert dict(calls[1].url.params) == {"limit": "5", "labelContains": "team", "lifecycle": "active"}
    bodies = {i: json.loads(calls[i].content) for i in (5, 6, 7, 8, 10, 11, 13)}
    assert bodies[5] == {"label": "Team bucket", "connectorKind": "s3", "authKind": "access-key",
                         "credential": {"accessKeyId": "AKIAFAKE", "secretAccessKey": SECRET}, "privateConfig": {"profileId": "aws"}}
    assert bodies[6] == {"expectedSettingsRevision": 2, "label": "Archive"}
    assert bodies[7] == {"expectedSettingsRevision": 2, "credential": {"secretAccessKey": SECRET}}
    assert bodies[8] == {"expectedSettingsRevision": 3}
    assert bodies[10] == {"expectedSettingsRevision": 5}
    assert bodies[11] == {"intent": "create", "connectorKind": "huggingface", "returnTo": "/settings/connections", "label": "Hub"}
    assert bodies[13] == {"intent": "create", "connectorKind": "s3", "authKind": "access-key", "suggestedLabel": "Team bucket"}
    assert [calls[i].headers.get("idempotency-key") for i in (5, 6, 7, 8, 9, 10)] == ["k1", "k2", "k3", "k4", "k5", "k6"]


def test_typed_errors_carry_the_server_code_and_never_the_credential():
    responses = iter([envelope(404, "CONNECTION_NOT_FOUND"), envelope(409, "CONNECTION_REVISION_CONFLICT"),
                      envelope(422, "CONNECTION_VALIDATION_FAILED"),
                      envelope(409, "CONNECTION_ENTRY_OPERATION_MISMATCH", fields=["connectorKind"])])
    c = fixture(lambda req: next(responses)).connections
    with pytest.raises(ConnectionNotFound) as missing:
        c.get("acme", CID)
    assert (missing.value.code, missing.value.status, missing.value.request_id) == ("CONNECTION_NOT_FOUND", 404, "req-1")
    with pytest.raises(ConnectionConflict):
        c.rename("acme", CID, label="x", expected_settings_revision=1, idempotency_key="k")
    with pytest.raises(ConnectionInvalid) as refused:
        c.create("acme", label="x", connector_kind="s3", auth_kind="access-key", idempotency_key="k",
                 credential={"secretAccessKey": SECRET})
    with pytest.raises(ConnectionConflict) as mismatch:
        c.entry_status("acme", OP)
    assert mismatch.value.details == {"fields": ["connectorKind"]}
    for err in (missing.value, refused.value, mismatch.value):
        assert SECRET not in str(err) and SECRET not in repr(err) and SECRET not in repr(err.__dict__)
    assert SECRET not in repr(c)


def test_writes_refuse_missing_keys_and_revisions_before_sending():
    sent = []
    c = fixture(lambda req: sent.append(req) or httpx.Response(200, json=DETAIL)).connections
    with pytest.raises(ValueError):
        c.create("acme", label="x", connector_kind="s3", auth_kind="access-key", idempotency_key="", credential={"k": SECRET})
    with pytest.raises(ValueError):
        c.disable("acme", CID, expected_settings_revision=None, idempotency_key="k")
    with pytest.raises(ValueError) as bad:
        c.reconnect("acme", CID, credential=SECRET, expected_settings_revision=1, idempotency_key="k")
    assert SECRET not in str(bad.value)
    assert sent == []
    assert isinstance(ConnectionInvalid("X", 400), (ConnectionApiError, ValueError))


def test_external_source_methods_use_the_connection():
    calls = []

    def serve(req):
        calls.append(req)
        return httpx.Response(201 if req.url.path.endswith(":import-external") else 200, json={"id": SID})

    s = fixture(serve).sources
    s.connectors("acme")
    s.import_external("acme", connector_kind="s3", connection_id=CID, target={"bucket": "team-data", "prefix": "episodes/"},
                      name="team-data", idempotency_key="k1")
    s.set_external("acme", SID, connector_kind="s3", connection_id=CID, target={"bucket": "b"},
                   expected_composition_revision=3, idempotency_key="k2")
    s.remove_external("acme", SID, expected_composition_revision=4, idempotency_key="k3")
    assert [(r.method, r.url.path) for r in calls] == [
        ("GET", "/namespaces/acme/source-connectors"),
        ("POST", "/namespaces/acme/sources:import-external"),
        ("PUT", f"/namespaces/acme/sources/{SID}/external"),
        ("DELETE", f"/namespaces/acme/sources/{SID}/external"),
    ]
    assert json.loads(calls[1].content) == {"external": {"connectorKind": "s3", "target": {"bucket": "team-data", "prefix": "episodes/"},
                                                         "connectionId": CID}, "name": "team-data"}
    assert json.loads(calls[2].content) == {"expectedCompositionRevision": 3,
                                            "external": {"connectorKind": "s3", "target": {"bucket": "b"}, "connectionId": CID}}
    assert json.loads(calls[3].content) == {"expectedCompositionRevision": 4}
    assert [r.headers.get("idempotency-key") for r in calls[1:]] == ["k1", "k2", "k3"]
    with pytest.raises(ValueError):
        s.import_external("acme", connector_kind="s3", target={}, idempotency_key="")

    failing = fixture(lambda req: envelope(404, "SOURCE_NOT_FOUND")).sources
    with pytest.raises(SourceError) as missing:
        failing.remove_external("acme", SID, expected_composition_revision=1, idempotency_key="k")
    assert missing.value.code == "SOURCE_NOT_FOUND"


def test_reconnect_keeps_enable_after_success_false_and_refuses_non_bool():
    sent = []
    c = fixture(lambda req: sent.append(req) or httpx.Response(200, json=DETAIL)).connections
    c.reconnect("acme", CID, credential={"secretAccessKey": SECRET}, expected_settings_revision=1,
                idempotency_key="k", enable_after_success=False)
    assert json.loads(sent[0].content)["enableAfterSuccess"] is False
    with pytest.raises(ValueError) as bad:
        c.reconnect("acme", CID, credential={"secretAccessKey": SECRET}, expected_settings_revision=1,
                    idempotency_key="k", enable_after_success="false")
    assert str(bad.value) == "enable_after_success must be True, False or None"  # value never formatted
    assert len(sent) == 1
