import base64
import json

import httpx
import pytest

from dreamlake import DreamLakeClient
from dreamlake.api.experiments import ExperimentRequestError
from dreamlake.experiments import DashRun, DashWriterError, dash_writer

DL = "https://dl.test"
DASH = "https://dash.test/api/managed"
CTRL = "/managed-experiments/ns/team/projects/research"
RUN_PATH = "/api/managed/projects/proj1/runs/run123"


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeServer:
    """DreamLake control plane + ml-dash managed API, in memory."""

    def __init__(self, clock, *, status="ready", session_error=None):
        self.clock, self.status, self.session_error = clock, status, session_error
        self.calls, self.sessions, self.metrics, self.tokens_seen = [], [], {}, []
        self.fail_queue = []  # (path-suffix, Response) consumed once each
        self.token_n = 0

    def __call__(self, request):
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        self.calls.append((request.method, request.url.host, path, body))
        for i, (suffix, response) in enumerate(self.fail_queue):
            if path.endswith(suffix):
                del self.fail_queue[i]
                return response
        if request.url.host == "dl.test":
            assert request.headers["authorization"] == "Bearer account-token"
            return self.control(request.method, path, body)
        auth = request.headers["authorization"]
        self.tokens_seen.append(auth)
        assert auth == f"Bearer dash-token-{self.token_n}"
        return self.dash(request, path, body)

    def control(self, method, path, body):
        if path == f"{CTRL}/status":
            return httpx.Response(200, json={"status": self.status, "canSetup": True, "revision": 0})
        if path == f"{CTRL}/setup" and method == "GET":
            return httpx.Response(200, json={"status": self.status, "revision": 0, "planHash": "a" * 64})
        if path == f"{CTRL}/setup" and method == "POST":
            self.status = "ready"
            return httpx.Response(200, json={"status": "ready", "revision": 1})
        if path == f"{CTRL}/session":
            if self.session_error:
                return httpx.Response(409, json={"error": {"code": self.session_error}})
            self.sessions.append(body)
            self.token_n += 1
            return httpx.Response(200, json={
                "token": f"dash-token-{self.token_n}", "expiresAt": int(self.clock()) + 60, "apiUrl": DASH,
                "namespaceId": "ns1", "projectId": "proj1", "instanceId": "inst", "workloadId": "wl-1", "generation": 1})
        raise AssertionError(f"unexpected control call {method} {path}")

    def dash(self, request, path, body):
        method = request.method
        if method == "PUT" and path == "/api/managed/projects/proj1/runs/trial_1":
            assert body is None
            return httpx.Response(200, json={"experiment": {"id": "run123", "name": "trial_1", "status": "RUNNING"}})
        if path == f"{RUN_PATH}/status":
            return httpx.Response(200, json={"id": "run123", "status": body["status"]})
        if path == f"{RUN_PATH}/parameters" and method == "POST":
            return httpx.Response(200, json={"data": body["data"], "version": 1})
        if path.startswith(f"{RUN_PATH}/metrics/") and method == "POST":
            name = path.rsplit("/", 1)[1]
            assert 1 <= len(body["dataPoints"]) <= 1000
            self.metrics.setdefault(name, []).extend(body["dataPoints"])
            return httpx.Response(200, json={"metricId": "m", "count": len(body["dataPoints"])})
        if path.startswith(f"{RUN_PATH}/metrics/") and method == "GET":
            name = path.rsplit("/", 1)[1]
            points = self.metrics.get(name, [])
            cols = ["step", "value", "timestamp"]
            return httpx.Response(200, json={
                "format": "ml-dash.metric-rows.v1", "metricId": "m", "visibility": "committed", "order": "storage",
                "blocks": [{"columns": [{"name": c, "type": "float64"} for c in cols],
                            "rows": [[p.get(c) for c in cols] for p in points]}],
                "returned": len(points), "hasMore": False, "nextCursor": None})
        if path == f"{RUN_PATH}/files" and method == "POST":
            return httpx.Response(200, json={"id": "f1", "name": body["filename"]})
        if path == f"{RUN_PATH}/files/f1/download":
            return httpx.Response(200, json={"filename": "w.bin", "dataBase64": base64.b64encode(b"weights").decode()})
        if path == f"{RUN_PATH}/logs" and method == "POST":
            return httpx.Response(200, json={"count": len(body["entries"])})
        raise AssertionError(f"unexpected dash call {method} {path}")

    def dash_calls(self, suffix=""):
        return [(m, p, b) for m, h, p, b in self.calls if h == "dash.test" and p.endswith(suffix)]


@pytest.fixture
def env(monkeypatch):
    clock = Clock()
    server = FakeServer(clock)
    monkeypatch.setattr(DashRun, "_clock", staticmethod(clock))
    sleeps = []
    monkeypatch.setattr(DashRun, "_sleep", staticmethod(sleeps.append))
    client = DreamLakeClient(dl_url=DL, token="account-token", transport=httpx.MockTransport(server))
    return clock, server, client, sleeps


def test_lifecycle_params_status_and_logs(env):
    clock, server, client, _ = env
    with dash_writer("team", "research", client=client, run_key="trial_1") as run:
        assert run.run_id == "run123" and run.project_id == "proj1"
        run.params.set(lr=3e-4, model={"depth": 4})
        run.log("started", level="warning")
    statuses = [b["status"] for _, _, b in server.dash_calls("/status")]
    assert statuses == ["RUNNING", "COMPLETED"]
    assert server.dash_calls("/parameters") == [("POST", f"{RUN_PATH}/parameters", {"data": {"lr": 3e-4, "model.depth": 4}})]
    (_, _, logs), = server.dash_calls("/logs")
    assert logs["entries"][0]["level"] == "warn" and logs["entries"][0]["message"] == "started"
    assert logs["entries"][0]["timestamp"].endswith("Z")
    # first session asks for a fresh workload credential with an idempotency key
    assert server.sessions[0]["mode"] == "write" and "idempotencyKey" in server.sessions[0]
    assert "dash-token" not in repr(run)


def test_failure_sets_failed_and_does_not_mask(env):
    _, server, client, _ = env
    with pytest.raises(ZeroDivisionError):
        with dash_writer("team", "research", client=client, run_key="trial_1"):
            1 / 0
    assert [b["status"] for _, _, b in server.dash_calls("/status")] == ["RUNNING", "FAILED"]


def test_token_refreshes_lazily_within_margin_and_reuses_workload(env):
    clock, server, client, _ = env
    with dash_writer("team", "research", client=client, run_key="trial_1", refresh_margin=15) as run:
        assert len(server.sessions) == 1
        clock.t += 44  # 16 s left: still valid
        run.log("a")
        assert len(server.sessions) == 1
        clock.t += 2   # 14 s left: refresh before the call
        run.log("b")
        assert len(server.sessions) == 2
        assert server.sessions[1] == {"mode": "write", "workloadId": "wl-1"}
    assert server.tokens_seen[-1] == "Bearer dash-token-2"


def test_401_reauths_once(env):
    _, server, client, _ = env
    with dash_writer("team", "research", client=client, run_key="trial_1") as run:
        server.fail_queue.append(("/logs", httpx.Response(401, json={"error": {"code": "MANAGED_CREDENTIAL_REQUIRED"}})))
        run.log("x")
        assert len(server.sessions) == 2


def test_metric_batching_and_flush(env):
    _, server, client, _ = env
    with dash_writer("team", "research", client=client, run_key="trial_1") as run:
        train = run.metrics("train")
        for step in range(1500):
            train.log(step=step, loss=1.0 / (step + 1), acc=step / 1500)
        posts = server.dash_calls("/metrics/train.loss")
        assert [len(b["dataPoints"]) for _, _, b in posts] == [1000]  # auto-flush at 1000
        assert posts[0][2]["dataPoints"][0] == {"step": 0, "value": 1.0, "timestamp": 1000.0}
    # exit flushes the remaining 500 of each field
    assert [len(b["dataPoints"]) for _, _, b in server.dash_calls("/metrics/train.loss")] == [1000, 500]
    assert len(server.metrics["train.acc"]) == 1500


def test_metric_read_back_flushes_first(env):
    _, server, client, _ = env
    with dash_writer("team", "research", client=client, run_key="trial_1") as run:
        run.metrics("eval").log(step=3, loss=0.5)
        page = run.metrics("eval").read("loss", limit=10)
        assert page == {"rows": [{"step": 3, "value": 0.5, "timestamp": 1000.0}], "nextCursor": None, "hasMore": False}
    (_, h, path, _), = [c for c in server.calls if c[0] == "GET" and "/metrics/" in c[2]]
    assert path == f"{RUN_PATH}/metrics/eval.loss"


def test_file_upload_json_base64_and_download(env, tmp_path):
    _, server, client, _ = env
    f = tmp_path / "w.bin"
    f.write_bytes(b"weights")
    with dash_writer("team", "research", client=client, run_key="trial_1") as run:
        assert run.files("ckpt/best").upload(f)["name"] == "ckpt__best__w.bin"
        assert run.files().download("f1") == b"weights"
    (_, _, body), = server.dash_calls("/files")
    assert body == {"filename": "ckpt__best__w.bin", "dataBase64": base64.b64encode(b"weights").decode(),
                    "contentType": "application/octet-stream"}


def test_file_over_8_mib_refused_locally(env, tmp_path):
    _, server, client, _ = env
    f = tmp_path / "big.bin"
    f.write_bytes(b"\0" * (8 * 1024 * 1024 + 1))
    with dash_writer("team", "research", client=client, run_key="trial_1") as run:
        with pytest.raises(ValueError, match="8 MiB"):
            run.files().upload(f)
    assert server.dash_calls("/files") == []


def test_conflict_and_unavailable_retry_bounded(env):
    _, server, client, sleeps = env
    with dash_writer("team", "research", client=client, run_key="trial_1") as run:
        server.fail_queue += [("/parameters", httpx.Response(409, json={"error": {"code": "OPERATION_CONFLICT"}})),
                              ("/parameters", httpx.Response(503, json={"error": {"code": "MANAGED_UNAVAILABLE"}}))]
        run.params.set(a=1)
        assert len(server.dash_calls("/parameters")) == 3 and len(sleeps) == 2
        # appends are not retried on 503
        server.fail_queue.append(("/logs", httpx.Response(503, json={"error": {"code": "MANAGED_UNAVAILABLE"}})))
        with pytest.raises(DashWriterError) as caught:
            run.log("x")
        assert caught.value.status == 503 and len(server.dash_calls("/logs")) == 1
        # bounded: 6 conflicts -> 5 retries then error
        server.fail_queue += [("/status", httpx.Response(409, json={"error": {"code": "OPERATION_CONFLICT"}}))] * 6
        with pytest.raises(DashWriterError) as caught:
            run.set_status("RUNNING")
        assert caught.value.code == "OPERATION_CONFLICT"


def test_failed_metric_flush_keeps_points(env):
    _, server, client, _ = env
    with dash_writer("team", "research", client=client, run_key="trial_1") as run:
        run.metrics("m").log(v=1)
        server.fail_queue.append(("/metrics/m.v", httpx.Response(400, json={"error": {"code": "MANAGED_INVALID_METRICS"}})))
        with pytest.raises(DashWriterError):
            run.flush()
        run.flush()
    assert server.metrics["m.v"][0]["value"] == 1


def test_legacy_read_only_is_explained(env):
    clock, _, client, _ = env
    client._transport = httpx.MockTransport(FakeServer(clock, session_error="MANAGED_LEGACY_READ_ONLY"))
    with pytest.raises(DashWriterError) as caught:
        dash_writer("team", "research", client=client).open()
    err = caught.value
    assert isinstance(err, ExperimentRequestError)
    assert err.status == 409 and err.code == "MANAGED_LEGACY_READ_ONLY"
    assert "DASH_MANAGED_WRITES_ENABLED" in str(err)


def test_setup_required_unless_auto_setup(env):
    clock, _, client, _ = env
    server = FakeServer(clock, status="not-enabled")
    client._transport = httpx.MockTransport(server)
    with pytest.raises(DashWriterError) as caught:
        dash_writer("team", "research", client=client, run_key="trial_1").open()
    assert caught.value.code == "MANAGED_SETUP_REQUIRED"
    with dash_writer("team", "research", client=client, run_key="trial_1", auto_setup=True):
        pass
    (setup,) = [b for m, h, p, b in server.calls if m == "POST" and p == f"{CTRL}/setup"]
    assert setup["expectedRevision"] == 0 and setup["planHash"] == "a" * 64 and setup["idempotencyKey"]


def test_non_https_api_url_refused(env):
    clock, _, client, _ = env

    def handle(request):
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json={"status": "ready"})
        return httpx.Response(200, json={"token": "t", "expiresAt": 2000, "apiUrl": "http://evil.test/api/managed",
                                         "projectId": "proj1"})

    client._transport = httpx.MockTransport(handle)
    with pytest.raises(DashWriterError, match="non-HTTPS"):
        dash_writer("team", "research", client=client).open()


def test_validation_before_network():
    client = DreamLakeClient(dl_url=DL, token="t", transport=httpx.MockTransport(lambda r: pytest.fail("network")))
    with pytest.raises(ValueError):
        dash_writer("team", "research", client=client, run_key="bad key")
    with pytest.raises(ValueError):
        dash_writer("team", "research", client=client, refresh_margin=90)
    run = dash_writer("team", "research", client=client)
    with pytest.raises(RuntimeError):
        run.params.set(a=1)
    with pytest.raises(ValueError):
        run.set_status("DONE")
