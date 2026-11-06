import json

import httpx
import pytest

from dreamlake import DreamLakeClient
from dreamlake.api.artifacts import ArtifactError
from dreamlake.cli.commands.artifact import cmd_push


def api(handler):
    return DreamLakeClient(dl_url="https://fixture.test", token="synthetic",
                           transport=httpx.MockTransport(handler)).artifacts


def test_visibility_updates_only_metadata_and_preserves_sharing_and_version():
    calls = []
    state = {"artifactId": "report", "visibility": "private", "viewerCanManage": True,
             "latestVersion": 7, "shareToken": "unchanged"}

    def serve(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer synthetic"
        if request.method == "POST":
            body = json.loads(request.content)
            assert list(body) == ["visibility"]
            state.update(body)
        return httpx.Response(200, json=state)

    artifacts = api(serve)
    for visibility in ["unlisted", "public", "private"]:
        row = artifacts.set_visibility("team", "report", visibility)
        assert row["visibility"] == visibility
        assert row["latestVersion"] == 7 and row["shareToken"] == "unchanged"
    assert [r.method for r in calls] == ["GET", "POST"] * 3
    artifacts.list("team")
    assert calls[-1].url.path == "/namespaces/team/artifacts"


@pytest.mark.parametrize("status,body", [(403, {}), (404, {}), (200, {"viewerCanManage": False}),
                                       (200, {}), (200, {"viewerCanManage": "true"})])
def test_missing_deleted_or_unauthorized_artifacts_never_upsert(status, body):
    calls = []

    def serve(request):
        calls.append(request)
        return httpx.Response(status, json=body)

    with pytest.raises(ArtifactError):
        api(serve).set_visibility("team", "report", "unlisted")
    assert [r.method for r in calls] == ["GET"]


def test_invalid_visibility_or_path_fails_without_network():
    def unexpected(request):
        raise AssertionError("unexpected request")

    with pytest.raises(ValueError):
        api(unexpected).set_visibility("team", "report", "team")
    with pytest.raises(ValueError):
        api(unexpected).set_visibility("team", "../report", "unlisted")


def test_uncertain_write_is_not_retried():
    calls = []

    def serve(request):
        calls.append(request.method)
        if request.method == "POST":
            raise httpx.ReadTimeout("fixture")
        return httpx.Response(200, json={"viewerCanManage": True})

    with pytest.raises(httpx.ReadTimeout):
        api(serve).set_visibility("team", "report", "unlisted")
    assert calls == ["GET", "POST"]


def test_python_push_parser_accepts_unlisted_and_rejects_unknown_visibility(tmp_path, capsys):
    # A missing file stops before storage/auth, after argument validation.
    assert cmd_push([str(tmp_path / "missing.html"), "--visibility", "unlisted"]) == 1
    assert "file not found" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cmd_push([str(tmp_path / "missing.html"), "--visibility", "team"])
