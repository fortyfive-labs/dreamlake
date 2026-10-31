"""Project policy controls match the CLI/TS scoped contract, without retries."""
import json

import httpx
import pytest

from dreamlake.api._client import DreamLakeClient
from dreamlake.api.projects import ProjectAccessError

PROJECT = "507f1f77bcf86cd799439011"


def policy(mode="workspace", visibility="private", role="ADMIN", *, namespace="acme", slug="research"):
    return {
        "projectId": PROJECT,
        "namespace": namespace,
        "projectSlug": slug,
        "accessMode": mode,
        "visibility": visibility,
        "baseline": {
            "orgMembers": "READ" if mode == "workspace" else None,
            "namespaceOwners": "ADMIN",
            "public": "READ" if visibility == "public" else None,
        },
        "viewerRole": role,
        "manageGrants": role == "ADMIN",
    }


def client_for(handler):
    return DreamLakeClient(dl_url="https://fixture.test", token="synthetic", transport=httpx.MockTransport(handler))


def test_scoped_get_and_patch_use_one_shared_policy_contract():
    requests = []

    def serve(request):
        requests.append(request)
        return httpx.Response(200, json=policy(
            "restricted" if request.method == "PATCH" else "workspace",
            namespace="a/b", slug="research team",
        ))

    projects = client_for(serve).projects
    assert projects.access(namespace="a/b", project="research team")["accessMode"] == "workspace"
    result = projects.set_access(namespace="a/b", project="research team", access_mode="restricted", visibility="private")
    assert result["projectId"] == PROJECT
    assert [r.method for r in requests] == ["GET", "PATCH"]
    assert all(r.url.raw_path == b"/namespaces/a%2Fb/projects/research%20team/access" for r in requests)
    assert json.loads(requests[1].content) == {"accessMode": "restricted", "visibility": "private"}
    assert all(r.headers["authorization"] == "Bearer synthetic" for r in requests)


def test_project_id_is_accepted_and_omitted_visibility_is_not_silently_written():
    requests = []

    def serve(request):
        requests.append(request)
        return httpx.Response(200, json=policy("restricted"))

    assert client_for(serve).projects.set_access(namespace="acme", project=PROJECT, access_mode="restricted")["projectId"] == PROJECT
    assert json.loads(requests[0].content) == {"accessMode": "restricted"}
    assert requests[0].url.path.endswith("/" + PROJECT + "/access")


@pytest.mark.parametrize("kwargs", [
    {"namespace": "", "project": "research", "access_mode": "restricted"},
    {"namespace": "acme", "project": " ", "access_mode": "restricted"},
    {"namespace": "acme", "project": "research", "access_mode": "private"},
    {"namespace": "acme", "project": "research", "access_mode": []},
    {"namespace": "acme", "project": "research", "access_mode": "restricted", "visibility": "public"},
    {"namespace": "acme", "project": "research", "access_mode": "workspace", "visibility": "internal"},
])
def test_invalid_input_never_sends(kwargs):
    calls = []
    client = client_for(lambda r: calls.append(r) or httpx.Response(200, json=policy()))
    with pytest.raises(ValueError):
        client.projects.set_access(**kwargs)
    assert calls == []


@pytest.mark.parametrize("status,code", [(404, "NOT_FOUND"), (403, "FORBIDDEN"), (409, "PROJECT_ACCESS_VISIBILITY_CONFLICT")])
def test_denied_or_conflicting_policy_writes_are_not_retried(status, code):
    calls = []
    client = client_for(lambda r: calls.append(r) or httpx.Response(status, json={"code": code, "private": "not-in-error"}))
    with pytest.raises(ProjectAccessError) as caught:
        client.projects.set_access(namespace="acme", project=PROJECT, access_mode="restricted")
    assert caught.value.status == status
    assert caught.value.code == code
    assert "not-in-error" not in str(caught.value)
    assert len(calls) == 1


@pytest.mark.parametrize("modify", [
    lambda d: d.update(projectId="not-an-id"),
    lambda d: d.update(namespace="another"),
    lambda d: d.update(projectSlug="another"),
    lambda d: d.update(accessMode=[]),
    lambda d: d.update(baseline=None),
    lambda d: d.update(manageGrants=False),
    lambda d: d.update(viewerRole=[]),
    lambda d: d["baseline"].update(orgMembers=[]),
])
def test_malformed_or_wrong_resource_reads_are_not_reported_as_valid(modify):
    value = policy()
    modify(value)
    client = client_for(lambda _: httpx.Response(200, json=value))
    with pytest.raises(ProjectAccessError, match="INVALID_PROJECT_ACCESS_RESPONSE"):
        client.projects.access(namespace="acme", project="research")


def test_policy_write_receipt_must_confirm_the_requested_mode_and_visibility():
    client = client_for(lambda _: httpx.Response(200, json=policy("workspace")))
    with pytest.raises(ProjectAccessError, match="INVALID_PROJECT_ACCESS_RESPONSE"):
        client.projects.set_access(namespace="acme", project="research", access_mode="restricted")


@pytest.mark.parametrize("role", ["READ", "WRITE", "ADMIN"])
def test_get_can_report_each_effective_project_role(role):
    client = client_for(lambda _: httpx.Response(200, json=policy(role=role)))
    assert client.projects.access(namespace="acme", project="research")["viewerRole"] == role


def test_restricted_policy_cannot_claim_public_or_workspace_baseline_access():
    for invalid in [policy("restricted", visibility="public"), policy("restricted")]:
        if invalid["visibility"] == "private":
            invalid["baseline"]["orgMembers"] = "READ"
        client = client_for(lambda _: httpx.Response(200, json=invalid))
        with pytest.raises(ProjectAccessError, match="INVALID_PROJECT_ACCESS_RESPONSE"):
            client.projects.access(namespace="acme", project="research")


def test_unsupported_server_and_transport_failures_never_downgrade_or_retry():
    calls = []

    def unsupported(request):
        calls.append(request)
        return httpx.Response(404, json={"error": "route_not_found"})

    with pytest.raises(ProjectAccessError, match="route_not_found"):
        client_for(unsupported).projects.access(namespace="acme", project="research")
    assert len(calls) == 1 and calls[0].url.path.endswith("/access")

    calls.clear()

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("acknowledgment unavailable", request=request)

    with pytest.raises(httpx.ReadTimeout):
        client_for(timeout).projects.set_access(namespace="acme", project="research", access_mode="restricted")
    assert len(calls) == 1


def test_wrong_project_id_receipt_does_not_confirm_a_different_resource():
    value = policy()
    value["projectId"] = "507f1f77bcf86cd799439012"
    client = client_for(lambda _: httpx.Response(200, json=value))
    with pytest.raises(ProjectAccessError, match="INVALID_PROJECT_ACCESS_RESPONSE"):
        client.projects.access(namespace="acme", project=PROJECT)
