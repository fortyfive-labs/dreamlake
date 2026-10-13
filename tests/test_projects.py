import json
import httpx
import pytest
from dreamlake.api._client import DreamLakeClient
from dreamlake.api.projects import ProjectAccessError


def test_project_grants_and_attachments_use_existing_scoped_contracts():
    requests = []
    def serve(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "synthetic", "removed": True})
    client = DreamLakeClient(dl_url="https://fixture.test", token="synthetic", transport=httpx.MockTransport(serve))
    projects = client.projects
    resource = "000000000000000000000001"
    projects.grant("a/b", "research team", "READ", user="@ge")
    projects.grant("a/b", "research team", "WRITE", team="team-id")
    projects.attach("a/b", "research team", "note", resource)
    projects.detach("a/b", "research team", "env", resource)
    projects.attach("a/b", "research team", "source", resource)
    projects.detach("a/b", "research team", "source", resource)
    projects.revoke("a/b", "research team", "grant/id")
    assert requests[0].url.raw_path == b"/namespaces/a%2Fb/projects/research%20team/grants"
    assert json.loads(requests[0].content) == {"role": "READ", "granteeSlug": "ge"}
    assert json.loads(requests[1].content) == {"role": "WRITE", "teamId": "team-id"}
    assert json.loads(requests[2].content) == {"resourceType": "note", "resourceId": resource}
    assert [r.method for r in requests] == ["POST", "POST", "POST", "DELETE", "PUT", "DELETE", "DELETE"]
    assert requests[4].url.raw_path.endswith(b"/sources/" + resource.encode())
    assert requests[6].url.raw_path.endswith(b"/grants/grant%2Fid")
    assert all(r.headers["authorization"] == "Bearer synthetic" for r in requests)


def test_invalid_inputs_do_not_send_and_denials_do_not_retry_or_publish_links():
    calls = []
    def serve(request):
        calls.append(request)
        return httpx.Response(403, json={"error": "RESOURCE_SHARE_FORBIDDEN", "shareToken": "never-echo"})
    client = DreamLakeClient(token="synthetic", transport=httpx.MockTransport(serve))
    with pytest.raises(ValueError):
        client.projects.grant("acme", "research", "OWNER", user="ge")
    with pytest.raises(ValueError):
        client.projects.grant("acme", "research", "READ", user="ge", team="team")
    with pytest.raises(ValueError):
        client.projects.attach("acme", "research", "note", "not-an-id")
    assert calls == []
    with pytest.raises(ProjectAccessError) as caught:
        client.projects.attach("acme", "research", "note", "000000000000000000000001")
    assert caught.value.status == 403
    assert caught.value.code == "RESOURCE_SHARE_FORBIDDEN"
    assert "never-echo" not in str(caught.value)
    assert len(calls) == 1
