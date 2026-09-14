import base64
import json

import httpx
import pytest

from dreamlake import DreamLakeClient
from dreamlake.api.runs import (
    RunAuthorizationError,
    RunConfigurationError,
    RunConflictError,
    RunWaitTimeout,
)


def test_submit_explicit_manifest_and_argv(tmp_path):
    (tmp_path / "train.py").write_bytes(b"print('hello')\n")
    seen = []
    argv = ["train.py", "--json", "", "a b", "--uvx", "$(never-evaluate)"]
    def handler(req):
        body = json.loads(req.content)
        seen.append(body)
        assert req.headers["Authorization"] == "Bearer synthetic"
        assert body["execution"] == {"kind": "uv-run", "argv": argv}
        assert base64.b64decode(body["source"]["files"][0]["contentBase64"]) == b"print('hello')\n"
        return httpx.Response(202, json={"run": {"id": "run1", "status": "queued"}})
    client = DreamLakeClient(token="synthetic", transport=httpx.MockTransport(handler))
    result = client.runs.submit("ge/lab/box", kind="uv-run", argv=argv,
                                include=["train.py"], cwd=tmp_path, request_id="same")
    assert result["id"] == "run1"
    assert len(seen) == 1  # Submission does not poll or retry implicitly.


def test_logs_cursor_cancel_and_wait():
    methods = []
    def handler(req):
        methods.append(req.method)
        if req.url.path.endswith("/logs"):
            assert req.url.params["cursor"] == "opaque/+="
            return httpx.Response(200, json={"entries": [{"stream": "stdout", "dataBase64": "8J8="}], "nextCursor": "next", "done": False})
        return httpx.Response(200, json={"run": {"id": "run1", "status": "cancel_requested"}})
    runs = DreamLakeClient(token="synthetic", transport=httpx.MockTransport(handler)).runs
    page = runs.logs("ge", "run1", cursor="opaque/+=", limit=2)
    assert base64.b64decode(page["entries"][0]["dataBase64"]) == b"\xf0\x9f"
    assert runs.cancel("ge", "run1")["status"] == "cancel_requested"
    with pytest.raises(RunWaitTimeout) as error:
        runs.wait("ge", "run1", timeout_seconds=0)
    assert error.value.run_id == "run1"
    assert methods == ["GET", "POST", "GET"]  # Wait timeout does not send cancel.


@pytest.mark.parametrize("status,kind", [(403, RunAuthorizationError), (409, RunConflictError)])
def test_error_redaction_and_explicit_request_identity(status, kind, tmp_path):
    client = DreamLakeClient(token="synthetic", transport=httpx.MockTransport(lambda r: httpx.Response(status, text="DO-NOT-ECHO")))
    with pytest.raises(kind) as error:
        client.runs.submit("ge/lab/box", kind="uvx", argv=["tool"], cwd=tmp_path, request_id="retry-me")
    assert error.value.status == status and error.value.request_id == "retry-me"
    assert "DO-NOT-ECHO" not in str(error.value)


def test_invalid_inputs_never_contact_api(tmp_path):
    runs = DreamLakeClient(token="synthetic", transport=httpx.MockTransport(lambda r: pytest.fail("network effect"))).runs
    for args in [{"kind": "uv", "argv": ["script"]}, {"kind": [], "argv": ["x"]},
                 {"kind": "uvx", "argv": "tool"}, {"kind": "uvx", "argv": ["\0"]},
                 {"kind": "uvx", "argv": ["tool"], "timeout_seconds": True}]:
        with pytest.raises(RunConfigurationError):
            runs.submit("ge/lab/box", cwd=tmp_path, **args)
    with pytest.raises(RunConfigurationError):
        runs.logs("ge", "run1", limit=0)


def test_wait_returns_failed_terminal_record():
    runs = DreamLakeClient(token="synthetic", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"run": {"id": "run1", "status": "failed", "exitCode": 7}}))).runs
    assert runs.wait("ge", "run1", timeout_seconds=0)["exitCode"] == 7


def test_provider_placement_resources_are_explicit(tmp_path):
    seen = []
    def handler(req):
        seen.append(json.loads(req.content))
        return httpx.Response(202, json={"run": {"id": "run1", "status": "queued"}})
    runs = DreamLakeClient(token="synthetic", transport=httpx.MockTransport(handler)).runs
    placement = {"providerId": "A" * 24, "associationId": "b" * 24, "associationRevision": 1}
    runs.submit("ge/lab/box", kind="uv-run", argv=["train.py"], cwd=tmp_path,
                placement=placement, resources={"cpus": 4, "gpus": 1}, request_id="placed")
    assert seen[0]["placement"] == {**placement, "providerId": "a" * 24}
    assert seen[0]["resources"] == {"cpus": 4, "memoryMib": 512, "gpus": 1}
    for extra in [{"placement": {}}, {"resources": {"cpus": 2}},
                  {"placement": placement, "resources": {"cpus": True}},
                  {"placement": placement, "resources": {"gpus": -1}}]:
        with pytest.raises(RunConfigurationError):
            runs.submit("ge/lab/box", kind="uv-run", argv=["train.py"], cwd=tmp_path, **extra)
    assert len(seen) == 1
