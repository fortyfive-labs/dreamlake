import httpx
import pytest
from dreamlake.api._client import DreamLakeClient
from dreamlake.api.ownership import OwnershipError


def test_routes_exact_retry_and_namespace_encoding():
    requests = []
    def serve(request):
        requests.append(request)
        return httpx.Response(200, json={"apiVersion": 1, "id": "operation"})
    client = DreamLakeClient(dl_url="https://fixture.test", token="synthetic", transport=httpx.MockTransport(serve))
    ownership = client.ownership
    request = {"apiVersion": 1, "expectedOwner": "legacy-id", "idempotencyKey": "stable", "previewHash": "reviewed"}
    ownership.preview("a/b", request)
    ownership.create("a/b", request)
    ownership.create("a/b", request)
    ownership.list("a/b", "outgoing")
    ownership.status("a/b", "operation")
    ownership.accept("a/b", "operation")
    ownership.reject("a/b", "operation")
    ownership.cancel("a/b", "operation")
    ownership.capabilities("a/b")
    assert requests[0].url.raw_path.startswith(b"/namespaces/a%2Fb/ownership/transfers/preview")
    assert requests[1].content == requests[2].content
    assert requests[3].url.params["direction"] == "outgoing"
    assert [r.method for r in requests] == ["POST", "POST", "POST", "GET", "GET", "POST", "POST", "POST", "GET"]
    assert all(r.headers["authorization"] == "Bearer synthetic" for r in requests)


def test_denial_is_structured_and_not_retried():
    calls = []
    def serve(request):
        calls.append(request)
        return httpx.Response(403, json={"apiVersion": 1, "code": "LOST_AUTHORITY", "private": "do not echo"})
    client = DreamLakeClient(token="synthetic", transport=httpx.MockTransport(serve))
    with pytest.raises(OwnershipError) as caught:
        client.ownership.accept("acme", "operation")
    assert caught.value.code == "LOST_AUTHORITY"
    assert caught.value.status == 403
    assert "do not echo" not in str(caught.value)
    assert len(calls) == 1


def test_invalid_direction_does_not_send_request():
    client = DreamLakeClient(token="synthetic", transport=httpx.MockTransport(lambda _: pytest.fail("unexpected request")))
    with pytest.raises(ValueError):
        client.ownership.list("acme", "invalid")


def test_resource_inspection_manifest_and_restore_gates():
    calls = []
    def serve(request):
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(422, json={"apiVersion": 1, "code": "SOURCE_DELETE_TERMINAL"})
        return httpx.Response(200, json={"apiVersion": 1, "ownerNamespaceId": "owner"})
    client = DreamLakeClient(token="synthetic", transport=httpx.MockTransport(serve))
    resource_id = "000000000000000000000001"
    assert client.ownership.inspect("a/b", "source", resource_id)["ownerNamespaceId"] == "owner"
    client.ownership.manifest("a/b", "source", resource_id)
    with pytest.raises(OwnershipError, match="SOURCE_DELETE_TERMINAL"):
        client.ownership.restore("a/b", "source", resource_id)
    with pytest.raises(ValueError):
        client.ownership.inspect("a/b", "note", resource_id)
    assert len(calls) == 3
    assert calls[0].url.raw_path == b"/namespaces/a%2Fb/ownership/resources/source/000000000000000000000001"
    assert calls[1].url.raw_path.endswith(b"/manifest")
