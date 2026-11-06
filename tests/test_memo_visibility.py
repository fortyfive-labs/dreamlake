import json

import httpx
import pytest

from dreamlake.api._client import DreamLakeClient
from dreamlake.api.memos import MemoReadOnly, WriteOutcomeUnknown

ID = "6ae3f318608512b4ba34860c"
NS = "fortyfive"


def client(handler):
    return DreamLakeClient(dl_url="https://example.test", token="test", transport=httpx.MockTransport(handler))


def row(visibility="private", **extra):
    return {"id": ID, "namespaceSlug": NS, "name": "Memo", "slug": "memo", "visibility": visibility,
            "permissions": {"setVisibility": True}, **extra}


@pytest.mark.parametrize("alias", ["memos", "notes"])
@pytest.mark.parametrize("visibility", ["private", "unlisted", "public"])
def test_visibility_updates_only_metadata_and_reads_back(alias, visibility):
    calls = []
    def handler(req):
        calls.append(req)
        assert req.url.path == f"/namespaces/{NS}/notes/{ID}"
        return httpx.Response(200, json=row(visibility if req.method == "PATCH" else "private"))
    memo = getattr(client(handler), alias)(ID, namespace=NS)
    assert memo.set_visibility(visibility=visibility).visibility == visibility
    assert [r.method for r in calls] == ["GET", "PATCH"]
    assert json.loads(calls[-1].content) == {"visibility": visibility}


@pytest.mark.parametrize("visibility", ["private", "unlisted", "public"])
def test_create_accepts_visibility(visibility):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={"note": row(visibility)})
    assert client(handler).memos.create(namespace=NS, name="Memo", visibility=visibility).id == ID
    assert len(calls) == 1 and calls[0].method == "POST"
    assert json.loads(calls[0].content) == {"name": "Memo", "visibility": visibility}


def test_invalid_visibility_refuses_before_resolution():
    def never(req):
        raise AssertionError("unexpected request")
    with pytest.raises(ValueError):
        client(never).memos(ID).set_visibility(visibility="team")
    with pytest.raises(ValueError):
        client(never).memos.create(namespace=NS, visibility="team")


def test_denied_visibility_cannot_patch():
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, json=row(permissions={"setVisibility": False}))
    with pytest.raises(MemoReadOnly):
        client(handler).memos(ID, namespace=NS).set_visibility(visibility="unlisted")
    assert [r.method for r in calls] == ["GET"]


@pytest.mark.parametrize("failure", ["lost", "wrong-id", "wrong-visibility", "bad-json"])
def test_uncertain_visibility_write_never_retries(failure):
    calls = []
    def handler(req):
        calls.append(req)
        if req.method == "GET":
            return httpx.Response(200, json=row())
        if failure == "lost":
            raise httpx.ReadTimeout("lost acknowledgement")
        if failure == "bad-json":
            return httpx.Response(200, text="not json")
        return httpx.Response(200, json=row("private" if failure == "wrong-visibility" else "unlisted", id="b" * 24 if failure == "wrong-id" else ID))
    with pytest.raises(WriteOutcomeUnknown):
        client(handler).memos(ID, namespace=NS).set_visibility(visibility="unlisted")
    assert [r.method for r in calls] == ["GET", "PATCH"]
