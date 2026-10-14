import copy
import json

import httpx
import pytest

from dreamlake import DreamLakeClient
from dreamlake.api.ownership import OwnershipError
from dreamlake.api.project_transfer import validate_plan

PROJECT = "000000000000000000000001"
NOTE = "000000000000000000000002"
OPERATION = "000000000000000000000003"


@pytest.fixture
def fixture():
    requests = []
    control = {"mode": "ready", "owner": False, "moved": False, "state": "pending", "status": "completed", "plan": None, "detached_other": None}

    def serve(request):
        body = json.loads(request.content) if request.content else None
        requests.append({"method": request.method, "path": request.url.raw_path.decode(), "body": body})
        assert request.headers["authorization"] == "Bearer fixture"
        if request.url.path.endswith("/capabilities"):
            return httpx.Response(200, json={"apiVersion": 1, "namespaceId": "target-ns" if "/destination/" in request.url.path else "source-ns",
                                           "capabilities": {"ownership.accept": {"allowed": control["owner"]}}})
        if request.url.path.endswith("/projects/research"):
            return httpx.Response(404 if control["moved"] else 200, json={"id": PROJECT})
        if request.url.path.endswith("/transfer-plan"):
            if control["mode"] == "unsupported":
                return httpx.Response(422, json={"apiVersion": 1, "code": "UNSUPPORTED_PROJECT_RESOURCE", "private": "never-echo"})
            return httpx.Response(200, json=control["plan"] or {"apiVersion": 1, "resource": {"type": "project", "id": PROJECT}, "ownerNamespaceId": "source-ns",
                                            "version": "version-1", "projectName": "research", "noteNames": [{"id": NOTE, "name": "note"}],
                                            "manifest": [{"type": "project", "id": PROJECT}] + ([] if control["mode"] == "partial" else [{"type": "note", "id": NOTE}]),
                                            "executable": control["mode"] != "disabled", "limits": {"maxNotes": 20, "maxTreeNodes": 200}})
        if request.url.path.endswith("/transfers/preview"):
            return httpx.Response(200, json={"apiVersion": 1, "resource": body["resource"], "sourceNamespaceId": body["expectedOwner"],
                                            "destinationNamespaceId": body["destinationNamespaceId"], "expectedVersion": body["expectedVersion"],
                                            "manifest": body["manifest"], "previewHash": "review-v1", "acceptedPolicy": body["policy"],
                                            "blockers": [{"code": "NAME_COLLISION", "action": "Choose explicit name"}] if control["mode"] == "blocked" else [],
                                            "summary": {"includedCount": len(body["manifest"]), "retainedFilings": 2 if body["policy"]["preserveExternalFilings"] else 0,
                                                        "detachedFilings": (0 if body["policy"]["preserveExternalFilings"] else 2) + (control["detached_other"] or 0), "becomesPrivate": True,
                                                        **({} if control["detached_other"] is None else {"detachedOtherFilings": control["detached_other"]})},
                                            "destinationAuthority": {"action": "ownership.accept", "namespaceId": "target-ns", "allowed": control["owner"]},
                                            "externalFilings": [{"filingId": "external-1"}, {"filingId": "external-2"}],
                                            "preservation": {"projectGrants": [{"id": "old-grant"}], "noteShares": []}})
        operation = {"apiVersion": 1, "id": OPERATION, "resource": {"type": "project", "id": PROJECT},
                     "sourceNamespaceId": "source-ns", "destinationNamespaceId": "target-ns", "previewHash": "review-v1"}
        if request.url.path.endswith("/transfers") and request.method == "POST":
            if control["mode"] == "uncertain":
                raise httpx.ReadTimeout("synthetic lost create response", request=request)
            return httpx.Response(200, json={**operation, "state": control["state"]})
        if request.url.path.endswith("/accept"):
            return httpx.Response(200, json={**operation, "state": "preparing"})
        if request.url.path.endswith(OPERATION):
            return httpx.Response(200, json={**operation, "state": control["status"]})
        pytest.fail(f"Unexpected request {request.method} {request.url.path}")

    client = DreamLakeClient(dl_url="https://fixture.invalid", token="fixture", transport=httpx.MockTransport(serve))
    return client.projects, requests, control


def test_whole_project_preview_is_complete_scoped_and_read_only(fixture):
    projects, requests, _ = fixture
    review = projects.move("a/b", "research", "@destination")
    assert review["ready"] is True
    assert review["request"]["manifest"] == [{"type": "project", "id": PROJECT}, {"type": "note", "id": NOTE}]
    assert review["request"]["policy"] == {"visibility": "private", "retainedGrantIds": [], "preserveAccess": False, "preserveExternalFilings": False}
    assert review["preview"]["summary"]["retainedFilings"] == 0
    assert review["preview"]["summary"]["detachedFilings"] == 2
    assert review["request"]["expectedVersion"] == "version-1"
    assert review["request"]["previewHash"] == "review-v1"
    assert review["preview"]["preservation"]["projectGrants"][0]["id"] == "old-grant"
    assert requests[2]["path"] == f"/namespaces/a%2Fb/ownership/resources/project/{PROJECT}/transfer-plan"
    assert all(r["method"] == "GET" or r["path"].endswith("/preview") for r in requests)


def test_cross_user_submission_requires_review_and_never_accepts_for_recipient(fixture):
    projects, requests, _ = fixture
    review = projects.move("source", "research", "destination", preserve_access=True)
    baseline = copy.deepcopy(review)
    result = projects.move("source", "research", "destination", dry_run=False, confirm=True, reviewed=review)
    assert result["operation"]["state"] == "pending"
    assert "ownership accept" in result["nextAction"]
    writes = [r for r in requests if r["path"].endswith("/transfers")]
    assert len(writes) == 1 and writes[0]["body"] == review["request"]
    assert not any(r["path"].endswith("/accept") for r in requests)
    assert result["preview"]["summary"]["detachedFilings"] == 2
    assert review == baseline


def test_external_filing_retention_is_opt_in_and_exact_saved_policy_survives_replay(fixture):
    projects, requests, _ = fixture
    review = projects.move("source", "research", "destination", preserve_external_filings=True)
    assert review["request"]["policy"]["preserveExternalFilings"] is True
    assert review["preview"]["summary"]["retainedFilings"] == 2
    assert review["preview"]["summary"]["detachedFilings"] == 0
    projects.move("source", "research", "destination", dry_run=False, confirm=True, reviewed=review)
    assert next(r for r in requests if r["path"].endswith("/transfers"))["body"] == review["request"]
    detached = projects.move("source", "research", "destination")
    before = len([r for r in requests if r["path"].endswith("/transfers")])
    with pytest.raises(ValueError, match="Preservation flags differ"):
        projects.move("source", "research", "destination", dry_run=False, confirm=True,
                      reviewed=detached, preserve_external_filings=True)
    assert len([r for r in requests if r["path"].endswith("/transfers")]) == before


def test_ambiguous_create_does_not_retry_and_exact_review_replays_after_source_moved(fixture):
    projects, requests, control = fixture
    review = projects.move("source", "research", "destination")
    control["mode"] = "uncertain"
    with pytest.raises(httpx.ReadTimeout):
        projects.move("source", "research", "destination", dry_run=False, confirm=True, reviewed=review)
    assert len([r for r in requests if r["path"].endswith("/transfers")]) == 1
    control.update(mode="ready", moved=True, state="completed")
    before = len(requests)
    result = projects.move("source", "research", "destination", dry_run=False, confirm=True, reviewed=review)
    assert result["operation"]["state"] == "completed"
    writes = [r for r in requests if r["path"].endswith("/transfers")]
    assert len(writes) == 2 and writes[0]["body"] == writes[1]["body"]
    assert all(not r["path"].endswith(("/projects/research", "/transfer-plan", "/preview")) for r in requests[before:])


@pytest.mark.parametrize("mode", ["disabled", "blocked", "unsupported", "partial"])
def test_disabled_unsupported_or_partial_plan_never_submits(fixture, mode):
    projects, requests, control = fixture
    control["mode"] = mode
    if mode in {"unsupported", "partial"}:
        with pytest.raises(OwnershipError) as error:
            projects.move("source", "research", "destination")
        assert "never-echo" not in str(error.value)
    else:
        review = projects.move("source", "research", "destination")
        assert review["ready"] is False
        with pytest.raises(OwnershipError):
            projects.move("source", "research", "destination", dry_run=False, confirm=True,
                          preview_hash="review-v1", idempotency_key="stable")
    assert not any(r["path"].endswith("/transfers") for r in requests)


def test_stale_preview_unverified_acceptance_and_invalid_confirmation_never_submit(fixture):
    projects, requests, _ = fixture
    for options, error in [({"preview_hash": "stale"}, "STALE_PREVIEW"), ({"preview_hash": "review-v1", "accept_if_owner": True}, "DESTINATION_ACCEPTANCE_NOT_AUTHORIZED")]:
        with pytest.raises(OwnershipError, match=error):
            projects.move("source", "research", "destination", dry_run=False, confirm=True, idempotency_key="stable", **options)
    assert not any(r["path"].endswith("/transfers") for r in requests)
    before = len(requests)
    for options in [{"dry_run": False}, {"dry_run": False, "confirm": True}, {"wait": True}, {"wait_timeout": 61}]:
        with pytest.raises(ValueError):
            projects.move("source", "research", "destination", **options)
    assert len(requests) == before


def test_explicit_owner_accept_and_bounded_wait_keep_same_operation(fixture):
    projects, requests, control = fixture
    control["owner"] = True
    review = projects.move("source", "research", "destination")
    result = projects.move("source", "research", "destination", dry_run=False, confirm=True, reviewed=review,
                           accept_if_owner=True, wait=True, wait_timeout=1)
    assert result["operation"]["state"] == "completed" and result["timedOut"] is False
    assert len([r for r in requests if r["path"].endswith("/accept")]) == 1
    assert next(r for r in requests if r["path"].endswith("/accept"))["path"] == f"/namespaces/destination/ownership/transfers/{OPERATION}/accept"
    control["status"] = "preparing"
    pending = projects.move("source", "research", "destination", dry_run=False, confirm=True, reviewed=review,
                            accept_if_owner=True, wait=True, wait_timeout=0)
    assert pending["timedOut"] is True and pending["operation"]["id"] == OPERATION


def test_request_tampering_and_explicit_collision_resolution(fixture):
    projects, requests, _ = fixture
    review = projects.move("source", "research", "destination")
    tampered = copy.deepcopy(review); tampered["request"]["manifest"] = [{"type": "project", "id": PROJECT}]
    with pytest.raises(ValueError):
        projects.move("source", "research", "destination", dry_run=False, confirm=True, reviewed=tampered)
    assert not any(r["path"].endswith("/transfers") for r in requests)
    renamed = projects.move("source", "research", "destination", destination_name="renamed", destination_names={NOTE: "new-note"})
    assert renamed["request"]["destinationName"] == "renamed"
    assert renamed["request"]["destinationNames"] == [{"resource": {"type": "note", "id": NOTE}, "name": "new-note"}]
    with pytest.raises(ValueError):
        projects.move("source", "research", "destination", destination_names={PROJECT: "not-a-note"})


@pytest.mark.parametrize("malformed", [None, [], {"projectName": "research", "resource": None}])
def test_malformed_review_bundle_fails_with_clear_error_before_create(fixture, malformed):
    projects, requests, _ = fixture
    review = projects.move("source", "research", "destination")
    review["plan"] = malformed
    with pytest.raises(ValueError, match="matching complete Project review bundle"):
        projects.move("source", "research", "destination", dry_run=False, confirm=True, reviewed=review)
    assert not any(r["path"].endswith("/transfers") for r in requests)


ARTIFACT = "000000000000000000000004"
ENV = "000000000000000000000005"


def mixed_plan():
    return {"apiVersion": 1, "resource": {"type": "project", "id": PROJECT}, "ownerNamespaceId": "source-ns",
            "version": "mixed-version", "projectName": "research", "executable": True,
            "manifest": [{"type": "project", "id": PROJECT}, {"type": "note", "id": NOTE},
                         {"type": "artifact", "id": ARTIFACT}, {"type": "env", "id": ENV}],
            "noteNames": [{"id": NOTE, "name": "note"}],
            "resourceNames": [{"type": "note", "id": NOTE, "name": "note"},
                              {"type": "artifact", "id": ARTIFACT, "name": "dashboard"},
                              {"type": "env", "id": ENV, "name": "simulation"}],
            "limits": {"maxResources": 512, "maxNotes": 511, "maxTreeNodes": 4096}}


def test_mixed_review_keeps_complete_typed_manifest_and_exact_request_replay(fixture):
    projects, requests, control = fixture
    control.update(plan=mixed_plan(), detached_other=3)
    review = projects.move("source", "research", "destination", preserve_external_filings=True)
    assert review["request"]["manifest"] == control["plan"]["manifest"]
    assert review["resourceCounts"] == {"project": 1, "note": 1, "artifact": 1, "env": 1}
    assert review["preview"]["summary"]["retainedFilings"] == 2
    assert review["preview"]["summary"]["detachedOtherFilings"] == 3
    assert review["preview"]["summary"]["detachedFilings"] == 3
    control.update(moved=True, state="completed")
    before = len(requests)
    result = projects.move("source", "research", "destination", dry_run=False, confirm=True, reviewed=review)
    assert result["operation"]["state"] == "completed"
    assert next(r for r in requests[before:] if r["path"].endswith("/transfers"))["body"] == review["request"]
    assert all(not r["path"].endswith(("/projects/research", "/transfer-plan", "/preview")) for r in requests[before:])
    control["moved"] = False
    for resource_id in (ARTIFACT, ENV):
        with pytest.raises(ValueError):
            projects.move("source", "research", "destination", destination_names={resource_id: "renamed"})


def test_total_resource_limit_includes_root_and_note_limit_only_counts_notes():
    plan = mixed_plan()
    plan["limits"]["maxNotes"] = 1
    assert validate_plan(plan, PROJECT) is plan
    plan["limits"]["maxResources"] = 3
    with pytest.raises(OwnershipError):
        validate_plan(plan, PROJECT)
    plan["limits"].update(maxResources=512, maxNotes=0)
    with pytest.raises(OwnershipError):
        validate_plan(plan, PROJECT)
    many = mixed_plan()
    many["manifest"] = [many["resource"]] + [{"type": "note", "id": f"{i + 100:024x}"} for i in range(511)]
    many["noteNames"] = [{"id": ref["id"], "name": ref["id"]} for ref in many["manifest"][1:]]
    many["resourceNames"] = [{"type": "note", **row} for row in many["noteNames"]]
    validate_plan(many, PROJECT)
    names = many.pop("resourceNames")  # Legacy capacity plan with 511 Notes.
    validate_plan(many, PROJECT)
    many["resourceNames"] = names
    many["manifest"].append({"type": "artifact", "id": ARTIFACT})
    many["resourceNames"].append({"type": "artifact", "id": ARTIFACT, "name": "dashboard"})
    with pytest.raises(OwnershipError):
        validate_plan(many, PROJECT)


@pytest.mark.parametrize("case", ["missing_names", "partial_names", "duplicate_names", "wrong_note_name", "extra_note",
                                  "duplicate_member", "source", "workflow", "annotation", "extra_ref_field", "missing_note",
                                  "null_limit", "fractional_limit", "missing_total_limit"])
def test_mixed_plan_fails_closed_for_incomplete_ambiguous_or_unknown_manifest(case):
    plan = mixed_plan()
    if case == "missing_names":
        del plan["resourceNames"]
    elif case == "partial_names":
        plan["resourceNames"].pop()
    elif case == "duplicate_names":
        plan["resourceNames"][1] = plan["resourceNames"][0]
    elif case == "wrong_note_name":
        plan["resourceNames"][0]["name"] = "different"
    elif case == "extra_note":
        plan["noteNames"].append({"id": ARTIFACT, "name": "dashboard"})
    elif case == "duplicate_member":
        plan["manifest"].append(plan["manifest"][1])
    elif case in {"source", "workflow", "annotation"}:
        plan["manifest"][2]["type"] = case
    elif case == "extra_ref_field":
        plan["manifest"][2]["extra"] = "unexpected"
    elif case == "missing_note":
        plan["noteNames"] = []
    elif case == "null_limit":
        plan["limits"]["maxResources"] = None
    elif case == "fractional_limit":
        plan["limits"]["maxResources"] = 1.5
    elif case == "missing_total_limit":
        del plan["limits"]["maxResources"]
    with pytest.raises(OwnershipError, match="INVALID_WHOLE_PROJECT_PLAN"):
        validate_plan(plan, PROJECT)


def test_same_database_id_for_different_resource_collections_is_unambiguous():
    plan = mixed_plan()
    plan["manifest"][2]["id"] = NOTE
    plan["resourceNames"][1]["id"] = NOTE
    validate_plan(plan, PROJECT)


@pytest.mark.parametrize("other", [None, -1, True])
def test_modern_preview_requires_explicit_valid_detached_other_filing_count(fixture, other):
    projects, requests, control = fixture
    control.update(plan=mixed_plan(), detached_other=other)
    with pytest.raises(OwnershipError, match="INVALID_TRANSFER_PREVIEW"):
        projects.move("source", "research", "destination")
    assert not any(r["path"].endswith("/transfers") for r in requests)


def test_zero_detached_other_filings_is_valid_in_modern_preview(fixture):
    projects, _, control = fixture
    control.update(plan=mixed_plan(), detached_other=0)
    assert projects.move("source", "research", "destination")["ready"] is True
