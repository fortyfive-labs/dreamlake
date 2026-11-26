"""Cross-namespace Project sharing follows the scoped REST contract, without retries."""
import json

import httpx
import pytest

from dreamlake.api._client import DreamLakeClient
from dreamlake.api.projects import ProjectAccessError

PROJECT = "507f1f77bcf86cd799439011"
SHARE = "share-1"


def share(role=None, proposed="READ", *, revision=1, source="acme", target="lab", slug="research", share_id=SHARE):
    return {
        "id": share_id,
        "projectId": PROJECT,
        "projectSlug": slug,
        "sourceNamespace": source,
        "targetNamespace": target,
        "role": role,
        "proposedRole": proposed,
        "revision": revision,
    }


def client_for(handler):
    return DreamLakeClient(dl_url="https://fixture.test", token="synthetic", transport=httpx.MockTransport(handler))


def recorder(response):
    calls = []

    def serve(request):
        calls.append(request)
        if callable(response):
            return response(request)
        return httpx.Response(response.status_code, content=response.content, headers=response.headers)

    return calls, client_for(serve).projects


def test_full_lifecycle_uses_the_contract_paths_methods_and_bodies():
    def serve(request):
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.method == "GET":
            return httpx.Response(200, json={"shares": [share(source="a/b", target="lab x", slug="research team")]})
        if request.url.path.endswith("/accept"):
            return httpx.Response(200, json=share("READ", None, revision=2, source="a/b", target="lab x"))
        return httpx.Response(201, json=share(source="a/b", target="lab x", slug="research team"))

    calls, projects = recorder(serve)
    invited = projects.share_with_namespace(namespace="a/b", project="research team", target_namespace="lab x", role="READ")
    assert invited["role"] is None and invited["proposedRole"] == "READ"
    assert projects.namespace_shares(namespace="a/b", project="research team")["shares"][0]["id"] == SHARE
    pending = projects.shared_projects(namespace="lab x")["shares"][0]
    accepted = projects.accept_shared_project(namespace="lab x", share_id=pending["id"], revision=pending["revision"])
    assert accepted["role"] == "READ" and accepted["projectId"] == PROJECT and accepted["sourceNamespace"] == "a/b"
    assert projects.leave_shared_project(namespace="lab x", share_id=SHARE) is None
    assert projects.decline_shared_project(namespace="lab x", share_id="s/2") is None
    assert projects.revoke_namespace_share(namespace="a/b", project="research team", share_id="s/2") is None

    source = b"/namespaces/a%2Fb/projects/research%20team/namespace-shares"
    target = b"/namespaces/lab%20x/shared-projects"
    assert [(r.method, r.url.raw_path) for r in calls] == [
        ("POST", source),
        ("GET", source),
        ("GET", target),
        ("POST", target + b"/share-1/accept"),
        ("DELETE", target + b"/share-1"),
        ("DELETE", target + b"/s%2F2"),
        ("DELETE", source + b"/s%2F2"),
    ]
    assert json.loads(calls[0].content) == {"targetNamespace": "lab x", "role": "READ"}
    assert json.loads(calls[3].content) == {"revision": 1}
    assert all(r.headers["authorization"] == "Bearer synthetic" for r in calls)


def test_project_id_is_accepted_for_source_calls():
    calls, projects = recorder(lambda r: httpx.Response(200, json=(
        {"shares": [share(slug="renamed")]} if r.method == "GET" else share("WRITE", None, slug="renamed"))))
    assert projects.share_with_namespace(namespace="acme", project=PROJECT, target_namespace="lab", role="WRITE")["role"] == "WRITE"
    assert len(projects.namespace_shares(namespace="acme", project=PROJECT)["shares"]) == 1
    assert all(("/projects/" + PROJECT + "/namespace-shares") in r.url.path for r in calls)


def test_pending_upgrade_keeps_the_old_role_until_accepted():
    upgrade = share("READ", "WRITE", revision=3)
    calls, projects = recorder(lambda r: httpx.Response(200, json=(
        {"shares": [upgrade]} if r.method == "GET"
        else share("WRITE", None, revision=4) if r.url.path.endswith("/accept") else upgrade)))
    assert projects.share_with_namespace(namespace="acme", project="research", target_namespace="lab", role="WRITE") == upgrade
    listed = projects.shared_projects(namespace="lab")["shares"][0]
    assert (listed["role"], listed["proposedRole"]) == ("READ", "WRITE")
    assert projects.accept_shared_project(namespace="lab", share_id=SHARE, revision=3)["role"] == "WRITE"
    assert json.loads(calls[-1].content) == {"revision": 3}


def test_new_share_defaults_to_read():
    calls, projects = recorder(httpx.Response(201, json=share()))
    assert projects.share_with_namespace(namespace="acme", project="research", target_namespace="lab")["proposedRole"] == "READ"
    assert json.loads(calls[0].content) == {"targetNamespace": "lab", "role": "READ"}


def test_downgrade_is_confirmed_as_immediately_in_effect():
    calls, projects = recorder(httpx.Response(200, json=share("READ", None, revision=5)))
    result = projects.share_with_namespace(namespace="acme", project="research", target_namespace="lab", role="READ")
    assert (result["role"], result["proposedRole"]) == ("READ", None)
    assert len(calls) == 1


def test_namespaces_are_trimmed_and_at_prefix_removed_like_project_moves():
    def serve(request):
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.method == "GET":
            return httpx.Response(200, json={"shares": [share()]})
        return httpx.Response(200, json=share("READ", None) if request.url.path.endswith("/accept") else share())

    calls, projects = recorder(serve)
    projects.share_with_namespace(namespace=" @acme", project="research", target_namespace="@lab ")
    projects.namespace_shares(namespace="@acme", project="research")
    projects.revoke_namespace_share(namespace="@acme", project="research", share_id=SHARE)
    projects.shared_projects(namespace="@lab")
    projects.accept_shared_project(namespace="@lab", share_id=SHARE, revision=1)
    projects.decline_shared_project(namespace=" @lab ", share_id=SHARE)
    assert json.loads(calls[0].content) == {"targetNamespace": "lab", "role": "READ"}
    assert [r.url.path for r in calls] == [
        "/namespaces/acme/projects/research/namespace-shares",
        "/namespaces/acme/projects/research/namespace-shares",
        "/namespaces/acme/projects/research/namespace-shares/share-1",
        "/namespaces/lab/shared-projects",
        "/namespaces/lab/shared-projects/share-1/accept",
        "/namespaces/lab/shared-projects/share-1",
    ]


def test_namespace_case_is_left_to_the_server():
    calls, projects = recorder(httpx.Response(201, json=share(target="ACME")))
    projects.share_with_namespace(namespace="acme", project="research", target_namespace="ACME")
    assert json.loads(calls[0].content)["targetNamespace"] == "ACME"


def test_empty_listings_are_valid():
    _, projects = recorder(httpx.Response(200, json={"shares": []}))
    assert projects.namespace_shares(namespace="acme", project="research") == {"shares": []}
    assert projects.shared_projects(namespace="lab") == {"shares": []}


@pytest.mark.parametrize("kwargs", [
    {"namespace": "", "project": "research", "target_namespace": "lab", "role": "READ"},
    {"namespace": "acme", "project": " ", "target_namespace": "lab", "role": "READ"},
    {"namespace": "acme", "project": "research", "target_namespace": "", "role": "READ"},
    {"namespace": "acme", "project": "research", "target_namespace": None, "role": "READ"},
    {"namespace": "acme", "project": "research", "target_namespace": "lab", "role": "ADMIN"},
    {"namespace": "acme", "project": "research", "target_namespace": "lab", "role": "read"},
    {"namespace": "acme", "project": "research", "target_namespace": "lab", "role": None},
    {"namespace": "acme", "project": "research", "target_namespace": "lab", "role": []},
    {"namespace": "acme", "project": "research", "target_namespace": "acme", "role": "READ"},
    {"namespace": "acme", "project": "research", "target_namespace": "@acme", "role": "READ"},
    {"namespace": "@acme", "project": "research", "target_namespace": " acme ", "role": "READ"},
    {"namespace": "acme", "project": "research", "target_namespace": "@", "role": "READ"},
    {"namespace": " @ ", "project": "research", "target_namespace": "lab", "role": "READ"},
])
def test_invalid_invitation_never_sends(kwargs):
    calls, projects = recorder(httpx.Response(201, json=share()))
    with pytest.raises(ValueError):
        projects.share_with_namespace(**kwargs)
    assert calls == []


@pytest.mark.parametrize("revision", [None, "1", 1.0, True, -1])
def test_accept_requires_the_exact_reviewed_revision_and_never_looks_it_up(revision):
    calls, projects = recorder(httpx.Response(200, json=share("READ", None)))
    with pytest.raises(ValueError):
        projects.accept_shared_project(namespace="lab", share_id=SHARE, revision=revision)
    with pytest.raises(TypeError):
        projects.accept_shared_project(namespace="lab", share_id=SHARE)
    assert calls == []


def test_revision_zero_is_sent_exactly():
    calls, projects = recorder(httpx.Response(200, json=share("READ", None, revision=1)))
    projects.accept_shared_project(namespace="lab", share_id=SHARE, revision=0)
    assert [r.method for r in calls] == ["POST"] and json.loads(calls[0].content) == {"revision": 0}


@pytest.mark.parametrize("call", [
    lambda p: p.namespace_shares(namespace="acme", project=""),
    lambda p: p.revoke_namespace_share(namespace="acme", project="research", share_id=""),
    lambda p: p.revoke_namespace_share(namespace="", project="research", share_id=SHARE),
    lambda p: p.shared_projects(namespace=" "),
    lambda p: p.accept_shared_project(namespace="lab", share_id="", revision=1),
    lambda p: p.decline_shared_project(namespace="lab", share_id=None),
    lambda p: p.leave_shared_project(namespace="", share_id=SHARE),
])
def test_missing_identifiers_never_send(call):
    calls, projects = recorder(httpx.Response(204))
    with pytest.raises(ValueError):
        call(projects)
    assert calls == []


def test_stale_accept_reports_the_server_conflict_once_without_refetch_or_retry():
    calls, projects = recorder(httpx.Response(409, json={"code": "PROJECT_SHARE_REVISION_CONFLICT", "private": "not-in-error"}))
    with pytest.raises(ProjectAccessError) as caught:
        projects.accept_shared_project(namespace="lab", share_id=SHARE, revision=1)
    assert (caught.value.status, caught.value.code) == (409, "PROJECT_SHARE_REVISION_CONFLICT")
    assert "not-in-error" not in str(caught.value)
    assert [r.method for r in calls] == ["POST"]


MUTATIONS = [
    lambda p: p.share_with_namespace(namespace="acme", project="research", target_namespace="lab", role="READ"),
    lambda p: p.revoke_namespace_share(namespace="acme", project="research", share_id=SHARE),
    lambda p: p.accept_shared_project(namespace="lab", share_id=SHARE, revision=1),
    lambda p: p.decline_shared_project(namespace="lab", share_id=SHARE),
    lambda p: p.leave_shared_project(namespace="lab", share_id=SHARE),
]


@pytest.mark.parametrize("call", MUTATIONS)
@pytest.mark.parametrize("status,code", [(403, "FORBIDDEN"), (404, "NOT_FOUND"), (500, "INTERNAL"), (503, "UNAVAILABLE")])
def test_denied_or_failed_mutations_are_sent_once(call, status, code):
    calls, projects = recorder(httpx.Response(status, json={"error": {"code": code}}))
    with pytest.raises(ProjectAccessError) as caught:
        call(projects)
    assert (caught.value.status, caught.value.code) == (status, code)
    assert len(calls) == 1


@pytest.mark.parametrize("call", MUTATIONS)
def test_ambiguous_transport_outcome_is_surfaced_without_retry(call):
    calls = []

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("acknowledgment unavailable", request=request)

    with pytest.raises(httpx.ReadTimeout):
        call(client_for(timeout).projects)
    assert len(calls) == 1


@pytest.mark.parametrize("modify", [
    lambda d: d.update(id=""),
    lambda d: d.update(projectId="not-an-id"),
    lambda d: d.update(projectSlug=None),
    lambda d: d.update(sourceNamespace=""),
    lambda d: d.update(targetNamespace=[]),
    lambda d: d.update(targetNamespace=d["sourceNamespace"]),
    lambda d: d.update(role="ADMIN"),
    lambda d: d.update(proposedRole="ADMIN"),
    lambda d: d.pop("role"),
    lambda d: d.pop("proposedRole"),
    lambda d: d.update(role=None, proposedRole=None),
    lambda d: d.update(role="WRITE", proposedRole="READ"),
    lambda d: d.update(role="READ", proposedRole="READ"),
    lambda d: d.update(role="WRITE", proposedRole="WRITE"),
    lambda d: d.update(revision="1"),
    lambda d: d.update(revision=True),
    lambda d: d.update(revision=-1),
])
def test_malformed_shares_are_not_reported_as_valid(modify):
    value = share()
    modify(value)
    _, projects = recorder(httpx.Response(200, json={"shares": [share(), value]}))
    for read in (lambda: projects.namespace_shares(namespace="acme", project="research"),
                 lambda: projects.shared_projects(namespace="lab")):
        with pytest.raises(ProjectAccessError, match="INVALID_PROJECT_SHARE_RESPONSE") as caught:
            read()
        assert caught.value.status == 502
    calls, projects = recorder(httpx.Response(201, json=value))
    with pytest.raises(ProjectAccessError, match="PROJECT_SHARE_UNCONFIRMED"):
        projects.share_with_namespace(namespace="acme", project="research", target_namespace="lab", role="READ")
    assert len(calls) == 1


@pytest.mark.parametrize("body", [None, [], {"shares": None}, {"shares": {}}, {"items": []}, {"shares": ["share-1"]}])
def test_listing_without_a_shares_array_is_rejected(body):
    _, projects = recorder(httpx.Response(200, json=body))
    with pytest.raises(ProjectAccessError, match="INVALID_PROJECT_SHARE_RESPONSE"):
        projects.shared_projects(namespace="lab")
    with pytest.raises(ProjectAccessError, match="INVALID_PROJECT_SHARE_RESPONSE"):
        projects.namespace_shares(namespace="acme", project="research")


def test_listings_for_another_scope_are_rejected():
    for wrong in (share(source="other"), share(slug="other")):
        _, projects = recorder(httpx.Response(200, json={"shares": [wrong]}))
        with pytest.raises(ProjectAccessError, match="INVALID_PROJECT_SHARE_RESPONSE"):
            projects.namespace_shares(namespace="acme", project="research")
    _, projects = recorder(httpx.Response(200, json={"shares": [share(target="other")]}))
    with pytest.raises(ProjectAccessError, match="INVALID_PROJECT_SHARE_RESPONSE"):
        projects.shared_projects(namespace="lab")
    other = dict(share(), projectId="507f1f77bcf86cd799439012")
    _, projects = recorder(httpx.Response(200, json={"shares": [other]}))
    with pytest.raises(ProjectAccessError, match="INVALID_PROJECT_SHARE_RESPONSE"):
        projects.namespace_shares(namespace="acme", project=PROJECT)


@pytest.mark.parametrize("receipt", [
    share(target="other"),
    share(source="other"),
    share(slug="other"),
    share(None, "WRITE"),
    share("WRITE", None),
])
def test_invitation_receipt_must_confirm_the_requested_target_project_and_role(receipt):
    calls, projects = recorder(httpx.Response(201, json=receipt))
    with pytest.raises(ProjectAccessError, match="PROJECT_SHARE_UNCONFIRMED") as caught:
        projects.share_with_namespace(namespace="acme", project="research", target_namespace="lab", role="READ")
    assert caught.value.outcome == "unknown" and caught.value.share_id == SHARE
    assert len(calls) == 1


@pytest.mark.parametrize("receipt", [
    share(None, "READ"),
    share("READ", "WRITE"),
    share("READ", None, share_id="another"),
    share("READ", None, target="other"),
])
def test_accept_receipt_must_confirm_this_share_is_active_with_no_remaining_proposal(receipt):
    calls, projects = recorder(httpx.Response(200, json=receipt))
    with pytest.raises(ProjectAccessError, match="PROJECT_SHARE_UNCONFIRMED") as caught:
        projects.accept_shared_project(namespace="lab", share_id=SHARE, revision=1)
    assert caught.value.status == 502 and caught.value.outcome == "unknown"
    assert len(calls) == 1


@pytest.mark.parametrize("response", [
    httpx.Response(201, text="invalid receipt"),
    httpx.Response(204),
    httpx.Response(200, json=[]),
])
def test_unreadable_success_is_an_unknown_outcome_not_a_safe_to_retry_failure(response):
    for call in (MUTATIONS[0], MUTATIONS[2]):
        calls, projects = recorder(response)
        with pytest.raises(ProjectAccessError, match="PROJECT_SHARE_UNCONFIRMED") as caught:
            call(projects)
        assert caught.value.outcome == "unknown" and caught.value.share_id is None
        assert len(calls) == 1


def test_unsupported_server_fails_without_fallback():
    calls, projects = recorder(httpx.Response(404, json={"error": "route_not_found"}))
    with pytest.raises(ProjectAccessError, match="route_not_found"):
        projects.shared_projects(namespace="lab")
    with pytest.raises(ProjectAccessError, match="route_not_found"):
        projects.share_with_namespace(namespace="acme", project="research", target_namespace="lab", role="READ")
    assert [r.url.path for r in calls] == [
        "/namespaces/lab/shared-projects", "/namespaces/acme/projects/research/namespace-shares"]


def test_sharing_methods_take_keyword_arguments_only():
    _, projects = recorder(httpx.Response(200, json={"shares": []}))
    with pytest.raises(TypeError):
        projects.shared_projects("lab")
    with pytest.raises(TypeError):
        projects.share_with_namespace("acme", "research", "lab", "READ")
