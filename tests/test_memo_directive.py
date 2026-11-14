import httpx
import pytest
from dreamlake.api._client import DreamLakeClient
from dreamlake.api.memos import MemoError, MemoRef, create_memo

ID = "6ab5aaeed3b4339ea2f4c162"

@pytest.mark.parametrize("legacy", [False, True])
def test_created_handle_uses_id_never_duplicate_title_or_slug(legacy):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={"note": {"id": ID, "name": "Launch", "slug": "launch-2"}})
    client = DreamLakeClient(dl_url="https://example.test", token="test", transport=httpx.MockTransport(handler))
    created = (create_memo("other-team", "Launch", client=client) if legacy else
               client.memos.create(namespace="other-team", name="Launch"))
    assert created.directive == f":memo[{ID}]"
    assert len(calls) == 1
    assert MemoRef(ID, "other-team", "Launch", "launch-2").directive == created.directive
    assert client.memos(ID, namespace="another-team").directive == created.directive
    assert len(calls) == 1


@pytest.mark.parametrize("envelope", [None, {}, {"note": None}, {"note": {}}])
@pytest.mark.parametrize("legacy", [False, True])
def test_missing_create_identity_preserves_uncertain_result_guidance(envelope, legacy):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, json=envelope)
    client = DreamLakeClient(dl_url="https://example.test", token="test", transport=httpx.MockTransport(handler))
    with pytest.raises(MemoError, match="may already exist.*Inspect Memos before creating again"):
        if legacy:
            create_memo("team", "Launch", text="body", client=client)
        else:
            client.memos.create(namespace="team", name="Launch", text="body")
    assert len(calls) == 1
@pytest.mark.parametrize("identity", ["Launch", "launch-2", "team/launch", "", None])
@pytest.mark.parametrize("legacy", [False, True])
def test_unconfirmed_create_never_becomes_slug_lookup_or_body_write(identity, legacy):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={"note": {"id": identity, "name": "Launch"}})
    client = DreamLakeClient(dl_url="https://example.test", token="test", transport=httpx.MockTransport(handler))
    with pytest.raises(MemoError, match="full ID was not confirmed"):
        if legacy:
            create_memo("team", "Launch", text="body", client=client)
        else:
            client.memos.create(namespace="team", name="Launch", text="body")
    assert len(calls) == 1
