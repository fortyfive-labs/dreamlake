"""HTTP contract and safety checks for reviewed Infrastructure moves."""
import copy
import json

import httpx
import pytest

from dreamlake import DreamLakeClient
from dreamlake.api.infrastructure import InfrastructureError

STACK = "11111111-1111-4111-8111-111111111111"
KEY = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def fixture():
    requests = []
    stack = dict(id=STACK, namespaceId="source-ns", projectId="source-project", name="example",
                 path="infra/examples/example", version=4, locked=False, environment="recipe",
                 repository="https://github.com/example/repo", directory="infra", createdAt="2026-11-03")
    control = {"stack": stack, "same_project": False, "status": 200, "invalid_receipt": False,
               "timeout": False, "blocked": False, "replayed": False, "preview_override": None, "error_code": None, "moved": True}

    def serve(request):
        body = json.loads(request.content) if request.content else None
        requests.append((request.method, request.url.path, body))
        assert request.headers["authorization"] == "Bearer fixture"
        if request.method == "GET":
            return httpx.Response(200, json=stack)
        if request.url.path.endswith("/preview"):
            intent = dict(expectedSourceName=stack["name"], destinationNamespaceSlug=body["destinationNamespaceSlug"],
                          destinationProjectSlug=body["destinationProjectSlug"], expectedSourceNamespaceId=stack["namespaceId"],
                          expectedSourceProjectId=stack["projectId"], expectedVersion=stack["version"],
                          expectedDestinationNamespaceId="source-ns", expectedDestinationProjectId="source-project" if control["same_project"] else "archive-project",
                          expectedDigest="a" * 64, idempotencyKey=KEY)
            if "destinationName" in body:
                intent["destinationName"] = body["destinationName"]
            target = body.get("destinationPath", None if control["same_project"] else stack["path"])
            if target is not None:
                intent.update(destinationPath=target, expectedSourcePath=stack["path"])
            value = dict(apiVersion=1, semantics="admission-linearized", stack=stack,
                         source=dict(namespaceId=stack["namespaceId"], projectId=stack["projectId"]),
                         destination=dict(namespaceId=intent["expectedDestinationNamespaceId"], projectId=intent["expectedDestinationProjectId"],
                                          destinationNamespaceSlug=body["destinationNamespaceSlug"], destinationProjectSlug=body["destinationProjectSlug"]),
                         request=intent, executable=not control["blocked"], blockers=[{"code": "LOCKED"}] if control["blocked"] else [])
            if control["preview_override"]:
                control["preview_override"](value)
            return httpx.Response(200, json=value)
        if control["timeout"]:
            raise httpx.ReadTimeout("secret-token-never-echo", request=request)
        if control["status"] != 200:
            return httpx.Response(control["status"], json={"error": control["error_code"] or "secret-token-never-echo", "receipt": {"stackId": "untrusted"}})
        moved = dict(stack, projectId=body["expectedDestinationProjectId"], namespaceId=body["expectedDestinationNamespaceId"],
                     name=body.get("destinationName", stack["name"]), path=body.get("destinationPath", stack["path"]))
        if control["invalid_receipt"]:
            moved["version"] += 1
        return httpx.Response(200, json=dict(apiVersion=1, stack=moved, moved=control["moved"], replayed=control["replayed"], operationId=body["idempotencyKey"]))

    client = DreamLakeClient(dl_url="https://fixture.invalid", token="fixture", transport=httpx.MockTransport(serve))
    return client.infra, requests, control


def preview(infra, **kw):
    return infra.preview_move(STACK, destination_namespace="acme", destination_project="archive", **kw)


def test_preview_preserves_cross_project_path_and_pins_state(fixture):
    infra, requests, _ = fixture
    review = preview(infra)
    assert len(requests) == 1 and requests[0][1].endswith("/move/preview")
    assert review["request"]["destinationPath"] == review["request"]["expectedSourcePath"] == "infra/examples/example"
    assert review["request"]["expectedVersion"] == 4
    assert review["request"]["expectedDigest"] == "a" * 64
    assert "destinationPath" not in requests[0][2]


def test_explicit_path_and_name_then_exact_execution_and_replay(fixture):
    infra, requests, control = fixture
    review = preview(infra, destination_path="archive/examples/example", destination_name="example-recipe")
    original = copy.deepcopy(review)
    receipt = infra.execute_move(STACK, review, confirm=True)
    assert requests[-1][2] == original["request"]
    assert receipt["stack"]["id"] == STACK
    assert receipt["stack"]["version"] == 4
    assert receipt["stack"]["path"] == "archive/examples/example"
    assert receipt["stack"]["name"] == "example-recipe"
    control["replayed"] = True
    assert infra.execute_move(STACK, review, confirm=True)["replayed"]
    assert requests[-1][2] == requests[-2][2]
    assert review == original
    assert len(requests) == 3  # execution never re-previews


@pytest.mark.parametrize("same_project,path", [(True, "infra/examples/example"), (False, None)])
def test_legacy_no_path_review_still_supported(fixture, same_project, path):
    infra, _, control = fixture
    control["same_project"] = same_project
    control["stack"]["path"] = path
    review = preview(infra)
    assert "destinationPath" not in review["request"]
    assert infra.execute_move(STACK, review, confirm=True)["stack"]["path"] == path


@pytest.mark.parametrize("mutate", [
    lambda r: r["request"].update(expectedSourcePath="stale/path"),
    lambda r: r["request"].update(expectedVersion=True),
    lambda r: r["request"].update(expectedSourceProjectId="elsewhere"),
    lambda r: r["request"].update(unreviewedField=True),
    lambda r: r["request"].update(expectedDigest="not-a-digest"),
    lambda r: r["request"].update(destinationPath="../escape"),
    lambda r: r["request"].pop("destinationPath"),
    lambda r: r.update(apiVersion=True),
])
def test_malformed_or_stale_review_never_writes(fixture, mutate):
    infra, requests, _ = fixture
    review = preview(infra)
    mutate(review)
    with pytest.raises(ValueError):
        infra.execute_move(STACK, review, confirm=True)
    assert len(requests) == 1


def test_confirmation_and_blocker_guards(fixture):
    infra, requests, control = fixture
    review = preview(infra)
    with pytest.raises(ValueError, match="confirm"):
        infra.execute_move(STACK, review)
    control["blocked"] = True
    review = preview(infra)
    with pytest.raises(ValueError, match="blocked"):
        infra.execute_move(STACK, review, confirm=True)
    assert all(path.endswith("preview") for _, path, _ in requests)


def test_rejects_redirected_or_unpinned_preview(fixture):
    infra, requests, control = fixture
    control["preview_override"] = lambda r: r["request"].update(destinationPath="surprise/path")
    with pytest.raises(ValueError, match="destination"):
        preview(infra)
    def unpin(r):
        r["request"].pop("destinationPath")
        r["request"].pop("expectedSourcePath")
    control["preview_override"] = unpin
    with pytest.raises(ValueError, match="pin"):
        preview(infra)
    assert len(requests) == 2


@pytest.mark.parametrize("mode,expected", [("timeout", "unknown"), ("invalid_receipt", "unknown"), (503, "unknown"), (409, "rejected"), (403, "rejected"), (302, "rejected")])
def test_failed_execution_is_sanitized_and_never_retried(fixture, mode, expected):
    infra, requests, control = fixture
    review = preview(infra)
    control["status" if isinstance(mode, int) else mode] = mode if isinstance(mode, int) else True
    with pytest.raises(InfrastructureError) as caught:
        infra.execute_move(STACK, review, confirm=True)
    assert caught.value.outcome == expected
    assert caught.value.stack_id == STACK
    assert caught.value.operation_id == KEY
    assert "secret-token" not in str(caught.value)
    assert len(requests) == 2


def test_status_uses_immutable_uuid_and_invalid_inputs_never_request(fixture):
    infra, requests, _ = fixture
    assert infra.status(STACK)["id"] == STACK
    with pytest.raises(ValueError):
        infra.status("example")
    with pytest.raises(ValueError):
        preview(infra, destination_path="infra//example")
    assert len(requests) == 1


@pytest.mark.parametrize("code", ["stack_path_reconciliation_required", "stack_move_reconciliation_required"])
def test_reconciliation_409_keeps_unknown_outcome_and_trusted_identity(fixture, code):
    infra, requests, control = fixture
    review = preview(infra)
    control.update(status=409, error_code=code)
    with pytest.raises(InfrastructureError) as caught:
        infra.execute_move(STACK, review, confirm=True)
    assert caught.value.code == code
    assert caught.value.outcome == "unknown"
    assert caught.value.stack_id == STACK
    assert caught.value.operation_id == KEY
    assert len(requests) == 2


def test_same_project_noop_receipt_is_valid(fixture):
    infra, _, control = fixture
    control.update(same_project=True, moved=False)
    review = preview(infra)
    receipt = infra.execute_move(STACK, review, confirm=True)
    assert receipt["moved"] is False and receipt["replayed"] is False


def test_cross_project_false_move_receipt_requires_reconciliation(fixture):
    infra, _, control = fixture
    review = preview(infra)
    control["moved"] = False
    with pytest.raises(InfrastructureError) as caught:
        infra.execute_move(STACK, review, confirm=True)
    assert caught.value.outcome == "unknown"
