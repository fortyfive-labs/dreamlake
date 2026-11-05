"""Legacy Python helpers translated to current server Bindr contracts."""
import json
from unittest.mock import Mock

import httpx
import pytest

import dreamlake
from dreamlake.api._client import BindrMembershipError, DreamLakeClient


@pytest.fixture(autouse=True)
def no_live_network(monkeypatch):
    def forbid(*args, **kwargs):
        pytest.fail("Unexpected live HTTP request")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbid)


def client_for(handler):
    return DreamLakeClient(dl_url="https://api.invalid", token="explicit", transport=httpx.MockTransport(handler))


def test_create_files_first_node_atomically_then_each_remaining_node():
    seen = []
    def handle(request):
        assert request.headers["Authorization"] == "Bearer explicit"
        body = json.loads(request.content)
        seen.append((request.url.raw_path.decode(), body))
        if len(seen) == 1:
            return httpx.Response(201, json={"bindr": {"id": "id/1", "name": "set", "memberCount": 1}})
        return httpx.Response(200, json={"added": True, "memberCount": 2})
    result = client_for(handle).create_bindr("@alice", "@project", "set", ["one", {"type": "node", "id": "two"}, "one"])
    assert seen == [
        ("/namespaces/%40alice/projects/%40project/bindrs", {"name": "set", "member": {"nodeId": "one"}}),
        ("/bindrs/id%2F1/members", {"nodeId": "two"}),
    ]
    assert result == {"id": "id/1", "name": "set", "memberCount": 2, "members": [{"nodeId": "one"}, {"nodeId": "two"}]}


def test_empty_create_uses_no_member_field():
    def handle(request):
        assert json.loads(request.content) == {"name": "empty"}
        return httpx.Response(201, json={"bindr": {"id": "b", "name": "empty", "memberCount": 0}})
    assert client_for(handle).create_bindr("ns", "project", "empty")["members"] == []


@pytest.mark.parametrize("transport_failure", [False, True])
def test_create_failure_preserves_confirmed_progress(transport_failure):
    seen = []
    def handle(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(201, json={"bindr": {"id": "b", "name": "set"}})
        if len(seen) == 2:
            return httpx.Response(200, json={"added": True, "memberCount": 2})
        if transport_failure:
            raise httpx.ReadTimeout("unknown outcome", request=request)
        return httpx.Response(403, json={"error": "denied"})
    with pytest.raises(BindrMembershipError) as error:
        client_for(handle).create_bindr("ns", "project", "set", ["one", "two", "three", "four"])
    assert error.value.bindr_id == "b"
    assert error.value.completed_node_ids == ["one", "two"]
    assert error.value.failed_node_id == "three"
    assert isinstance(error.value.__cause__, httpx.HTTPError)
    assert len(seen) == 3


def test_name_resolution_and_full_member_pagination():
    seen = []
    def handle(request):
        seen.append(request.url.path)
        if request.url.path.endswith("/projects/project/bindrs"):
            return httpx.Response(200, json={"bindrs": [{"id": "wrong", "name": "a-set"}, {"id": "b", "name": "set"}]})
        assert request.url.path == "/bindrs/b"
        page = int(request.url.params["page"])
        assert request.url.params["pageSize"] == "100"
        assert request.url.params["recursive"] == "true"
        return httpx.Response(200, json={"bindr": {"id": "b", "name": "set"}, "members": [{"nodeId": f"n{page}"}], "page": page, "totalPages": 2, "total": 2})
    result = client_for(handle).get_bindr("ns", "project", "set")
    assert result == {"id": "b", "name": "set", "members": [{"nodeId": "n1"}, {"nodeId": "n2"}], "memberCount": 2}
    assert len(seen) == 3
    assert dreamlake._normalize_member_ref(result["members"][0]) == {"type": "node", "id": "n1"}


@pytest.mark.parametrize("rows,error", [([], httpx.HTTPStatusError), ([{"id": "a", "name": "set"}, {"id": "b", "name": "set"}], ValueError)])
def test_missing_or_ambiguous_names_never_mutate(rows, error):
    seen = []
    def handle(request):
        seen.append(request.method)
        return httpx.Response(200, json={"bindrs": rows})
    with pytest.raises(error):
        client_for(handle).add_bindr_members("ns", "project", "set", ["n"])
    assert seen == ["GET"]


def test_add_members_uses_canonical_id_and_refreshes_results():
    added = []
    def handle(request):
        if request.url.path.endswith("/projects/project/bindrs"):
            return httpx.Response(200, json={"bindrs": [{"id": "b", "name": "set"}]})
        if request.method == "POST":
            assert request.url.path == "/bindrs/b/members"
            added.append(json.loads(request.content)["nodeId"])
            return httpx.Response(200, json={"added": True, "memberCount": len(added)})
        assert request.url.path == "/bindrs/b"
        return httpx.Response(200, json={"bindr": {"id": "b", "name": "set"}, "members": [{"nodeId": n} for n in added], "totalPages": 1, "total": len(added)})
    result = client_for(handle).add_bindr_members("ns", "project", "set", [{"nodeId": "one"}, {"type": "node", "id": "two"}])
    assert added == ["one", "two"]
    assert result["memberCount"] == 2


@pytest.mark.parametrize("members", [["ok", {"type": "bindr", "id": "b"}], ["ok", ""], [{"nodeId": None}]])
def test_invalid_members_rejected_before_any_request(members):
    def handle(request):
        pytest.fail("Invalid membership must fail before a request")
    client = client_for(handle)
    with pytest.raises(ValueError):
        client.create_bindr("ns", "project", "set", members)
    with pytest.raises(ValueError):
        client.add_bindr_members("ns", "project", "set", members)


def test_top_level_helper_rejects_nested_bindr_members_before_request():
    with pytest.raises(ValueError, match="parent hierarchy"):
        dreamlake.bindr("set", project="project@ns", bindrs=["child"])


def test_member_failure_does_not_trigger_bindr_creation(monkeypatch):
    client = Mock()
    client.get_bindr.return_value = {"id": "b", "members": []}
    client.lookup_node.return_value = {"id": "n"}
    request = httpx.Request("POST", "https://api.invalid/bindrs/b/members")
    client.add_bindr_members.side_effect = httpx.HTTPStatusError("gone", request=request, response=httpx.Response(404, request=request))
    monkeypatch.setattr(dreamlake, "_get_client", lambda: client)
    with pytest.raises(httpx.HTTPStatusError):
        dreamlake.bindr("set", project="project@ns", members=["/file"])
    client.create_bindr.assert_not_called()


def test_read_failure_preserves_http_error():
    client = client_for(lambda request: httpx.Response(403, json={"error": "denied"}))
    with pytest.raises(httpx.HTTPStatusError) as error:
        client.get_bindr_by_id("b")
    assert error.value.response.status_code == 403


def test_download_uses_canonical_node_ids(monkeypatch, tmp_path):
    client = Mock()
    client.get_bindr.return_value = {"id": "b", "name": "set", "members": [{"nodeId": "file-node"}]}
    monkeypatch.setattr(dreamlake, "_get_client", lambda: client)
    download = Mock(return_value=1)
    monkeypatch.setattr(dreamlake, "_download_node_member", download)
    assert dreamlake.download_bindr("set", project="project@ns", to=str(tmp_path)) == tmp_path
    download.assert_called_once_with(client, "file-node", tmp_path)
