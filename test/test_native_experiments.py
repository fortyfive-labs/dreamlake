import json

import httpx
import pytest

from dreamlake import DreamLakeClient
from dreamlake.api.experiments import ExperimentRequestError

RUN = "a" * 24


def test_native_create_log_archive_and_project_scope():
    seen = []

    def handle(request):
        seen.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        assert request.headers["authorization"] == "Bearer account-token"
        return httpx.Response(200, json={"experiment": {"id": RUN, "archivedAt": None}, "count": 1})

    experiments = DreamLakeClient(token="account-token", transport=httpx.MockTransport(handle)).experiments
    assert experiments.create_run("team", "research", "trial_1")["id"] == RUN
    assert experiments.log("team", "research", RUN,
                           [{"timestamp": "2026-10-31T00:00:00Z", "level": "info", "message": "started"}])["count"] == 1
    assert experiments.archive("team", "research", RUN, False)["id"] == RUN
    assert experiments.delete_run("team", "research", RUN)["id"] == RUN
    assert isinstance(experiments.list_trash("team", "research"), dict)
    assert experiments.restore_deleted("team", "research", RUN)["id"] == RUN
    assert seen == [
        ("PUT", "/native-experiments/ns/team/projects/research/runs/trial_1", None),
        ("POST", f"/native-experiments/ns/team/projects/research/runs/{RUN}/logs",
         {"entries": [{"timestamp": "2026-10-31T00:00:00Z", "level": "info", "message": "started"}]}),
        ("PATCH", f"/native-experiments/ns/team/projects/research/runs/{RUN}", {"archived": False}),
        ("DELETE", f"/native-experiments/ns/team/projects/research/runs/{RUN}", None),
        ("GET", "/native-experiments/ns/team/projects/research/trash", None),
        ("POST", f"/native-experiments/ns/team/projects/research/runs/{RUN}/restore", None),
    ]


def test_uncertain_write_is_not_retried_and_error_body_is_redacted():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(503, text="DO-NOT-ECHO")

    experiments = DreamLakeClient(token="account-token", transport=httpx.MockTransport(handle)).experiments
    with pytest.raises(ExperimentRequestError, match="HTTP 503") as caught:
        experiments.create_run("team", "research", "trial_1")
    assert caught.value.status == 503
    assert "DO-NOT-ECHO" not in str(caught.value)
    assert len(calls) == 1


def test_invalid_run_identity_never_reaches_network():
    experiments = DreamLakeClient(token="account-token", transport=httpx.MockTransport(
        lambda request: pytest.fail("unexpected network request"))).experiments
    with pytest.raises(ValueError):
        experiments.get_run("team", "research", "dash-snowflake-id")


BASE = "/native-experiments/ns/team/projects/research"


def _exp(handle):
    exp = DreamLakeClient(token="t", transport=httpx.MockTransport(handle)).experiments
    exp._sleep = lambda s: None
    return exp


def test_read_metric_pagination_params_and_validation():
    seen = []

    def handle(request):
        seen.append((request.url.path, dict(request.url.params)))
        start = int(request.url.params["cursor"])
        rows = [{"v": i} for i in range(start, min(start + int(request.url.params["limit"]), 5))]
        nxt = str(start + len(rows)) if len(rows) == int(request.url.params["limit"]) else None
        return httpx.Response(200, json={"rows": rows, "nextCursor": nxt})

    exp = _exp(handle)
    first = exp.read_metric("team", "research", RUN, "loss.val", limit=3)
    assert [r["v"] for r in first["rows"]] == [0, 1, 2] and first["nextCursor"] == "3"
    second = exp.read_metric("team", "research", RUN, "loss.val", start_index=3, limit=3)
    assert [r["v"] for r in second["rows"]] == [3, 4] and second["nextCursor"] is None
    assert seen[0] == (f"{BASE}/runs/{RUN}/metrics/loss.val", {"limit": "3", "cursor": "0"})
    assert seen[1][1] == {"limit": "3", "cursor": "3"}
    for kw in ({"start_index": -1}, {"limit": 0}, {"limit": 1001}):
        with pytest.raises(ValueError):
            exp.read_metric("team", "research", RUN, "loss", **kw)
    with pytest.raises(ValueError):
        exp.read_metric("team", "research", RUN, "a/b")


def test_upload_list_get_download_file(tmp_path):
    import hashlib
    f = tmp_path / "out.txt"
    f.write_bytes(b"hello")
    seen = []

    def handle(request):
        seen.append((request.method, request.url.path, dict(request.url.params)))
        if request.method == "POST":
            body = request.content
            assert request.headers["content-type"].startswith("multipart/form-data")
            assert body.index(b'name="sizeBytes"') < body.index(b'name="file"')
            assert b'filename="renamed.txt"' in body and hashlib.sha256(b"hello").hexdigest().encode() in body
            assert b'name="tags"' in body and b"a,b" in body
            return httpx.Response(200, json={"id": "f1", "filename": "renamed.txt"})
        if request.url.path.endswith("/download"):
            return httpx.Response(200, content=b"hello")
        if request.url.path.endswith("/files/f1"):
            return httpx.Response(200, json={"file": {"id": "f1"}})
        return httpx.Response(200, json={"files": [{"id": "f1"}], "hasMore": False})

    exp = _exp(handle)
    assert exp.upload_file("team", "research", RUN, f, name="renamed.txt", tags=["a", "b"])["id"] == "f1"
    assert exp.list_files("team", "research", RUN, prefix="/x", tags=["a"])["files"][0]["id"] == "f1"
    assert exp.get_file("team", "research", RUN, "f1")["id"] == "f1"
    dest = tmp_path / "dl.txt"
    assert exp.download_file("team", "research", RUN, "f1", dest) == b"hello" and dest.read_bytes() == b"hello"
    assert seen[1] == ("GET", f"{BASE}/runs/{RUN}/files", {"prefix": "/x", "tags": "a"})
    assert seen[3][1] == f"{BASE}/runs/{RUN}/files/f1/download"
    with pytest.raises(ValueError):
        exp.upload_file("team", "research", RUN, f, name="a/b")


def test_error_carries_status_code_and_body():
    def handle(request):
        return httpx.Response(409, json={"error": {"code": "NATIVE_RUN_ACTIVE"}})

    with pytest.raises(ExperimentRequestError) as caught:
        _exp(handle).delete_run("team", "research", RUN)
    e = caught.value
    assert (e.status, e.code, e.body) == (409, "NATIVE_RUN_ACTIVE", {"error": {"code": "NATIVE_RUN_ACTIVE"}})
    assert "NATIVE_RUN_ACTIVE" in str(e)

    with pytest.raises(ExperimentRequestError) as caught:
        _exp(lambda r: httpx.Response(502, text="gateway")).get_run("team", "research", RUN)
    assert (caught.value.status, caught.value.code, caught.value.body) == (502, None, "gateway")


def test_conflict_retry_is_opt_in_bounded_and_code_specific():
    conflict = httpx.Response(409, json={"error": {"code": "OPERATION_CONFLICT"}})
    calls = []

    def flaky(request):
        calls.append(1)
        return conflict if len(calls) < 3 else httpx.Response(200, json={"ok": True})

    exp = _exp(flaky)
    assert exp.append_metric("team", "research", RUN, "loss", [{"v": 1}], retry_conflicts=True) == {"ok": True}
    assert len(calls) == 3

    calls.clear()
    with pytest.raises(ExperimentRequestError) as caught:  # default: no retry
        _exp(lambda r: (calls.append(1), conflict)[1]).append_metric("team", "research", RUN, "loss", [{"v": 1}])
    assert caught.value.code == "OPERATION_CONFLICT" and len(calls) == 1

    calls.clear()
    with pytest.raises(ExperimentRequestError):  # bounded
        _exp(lambda r: (calls.append(1), conflict)[1]).append_metric(
            "team", "research", RUN, "loss", [{"v": 1}], retry_conflicts=True)
    assert len(calls) == 6

    calls.clear()  # other 409 codes are never retried
    with pytest.raises(ExperimentRequestError):
        _exp(lambda r: (calls.append(1), httpx.Response(409, json={"error": {"code": "NATIVE_RUN_ACTIVE"}}))[1]
             ).set_status("team", "research", RUN, "COMPLETED", retry_conflicts=True)
    assert len(calls) == 1
