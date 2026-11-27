"""Acceptance against the installed recorder, with no umbrella SDK available."""
import importlib.util
import json
import sys

import httpx
import pytest

from dreamlake_dash import Experiment, NativeConnection, OperationMode, experiment, dash_writer
from dreamlake_dash.dash import DashWriterError
from dreamlake_dash.managed import _login_provider


def test_standalone_import_and_local_round_trip(tmp_path, monkeypatch):
    assert importlib.util.find_spec("dreamlake") is None
    assert not any(name == "dreamlake" or name.startswith("dreamlake.") for name in sys.modules)
    monkeypatch.setenv("DREAMLAKE_EXPERIMENT_BUFFER_ENABLED", "false")
    with Experiment(prefix="team/project/trial", root=tmp_path) as run:
        assert run.mode is OperationMode.LOCAL
        run.params.set(seed=0)
        run.metrics("train").log(step=0, loss=0)
        run.tracks("state").append(x=1, _ts=1)
        run.logs.info("Explicit log")
        run.files("reports").save_json({"passed": True}, to="summary.json")
        run.flush()
    with Experiment(prefix="team/project/trial", root=tmp_path) as run:
        assert run.params.get()["seed"] == 0
        assert run.metrics("train").read()["data"][0]["data"]["loss"] == 0
        assert run.tracks("state").read()["entries"][0]["x"] == 1
        assert json.loads(run.files("reports/summary.json").read_text()) == {"passed": True}


def test_decorator_retains_return_value(tmp_path):
    @experiment(prefix="team/project/decorated", root=tmp_path)
    def trial(experiment):
        experiment.params.set(seed=3)
        return 3
    assert trial() == 3


def test_native_credentials_remain_project_scoped_and_transport_borrowed():
    seen = []
    root = "/native-experiments/ns/team/projects/project"
    def handle(request):
        seen.append((request.url.path, request.headers.get("authorization")))
        if request.url.path.endswith("/session"):
            return httpx.Response(200, json={"token": "session-token", "expiresAt": 9999999999,
                "apiUrl": "https://example.test" + root, "workloadId": "writer", "generation": 1})
        return httpx.Response(200, json={"metrics": []})
    class OwnedTransport(httpx.MockTransport):
        closed = 0
        def close(self):
            self.closed += 1
    transport = OwnedTransport(handle)
    connection = NativeConnection("https://example.test", "team", "project", lambda: "user-token", transport=transport)
    client = connection.client()
    assert client.list_metrics("0123456789abcdef01234567") == []
    client.close()
    assert transport.closed == 0
    transport.close()
    assert transport.closed == 1
    assert seen[0] == (root + "/session", "Bearer user-token")
    assert seen[1][1] == "Bearer session-token"
    assert all(path.startswith(root + "/") for path, _ in seen)


def test_login_environment_switch_never_reuses_other_server_token(tmp_path, monkeypatch):
    monkeypatch.delenv("DREAMLAKE_API_KEY", raising=False)
    monkeypatch.delenv("DREAMLAKE_REMOTE", raising=False)
    (tmp_path / "config.json").write_text(json.dumps({"remote_url": "https://first.test"}))
    (tmp_path / "tokens.json").write_text(json.dumps({"dreamlake-token": "first-token"}))
    _, provider = _login_provider(config_dir=tmp_path)
    assert provider() == "first-token"
    (tmp_path / "config.json").write_text(json.dumps({"remote_url": "https://second.test"}))
    with pytest.raises(ValueError, match="No DreamLake login"):
        provider()


def test_managed_default_does_not_import_umbrella_or_leak_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("DREAMLAKE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("DREAMLAKE_API_KEY", raising=False)
    run = dash_writer("team", "project")
    with pytest.raises(DashWriterError) as error:
        run.ensure_setup()
    assert error.value.status == 401
    assert "dreamlake" not in sys.modules


def test_managed_default_reads_selected_login_and_preserves_route(tmp_path, monkeypatch):
    monkeypatch.setenv("DREAMLAKE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("DREAMLAKE_API_KEY", raising=False)
    monkeypatch.delenv("DREAMLAKE_REMOTE", raising=False)
    (tmp_path / "config.json").write_text(json.dumps({"remote_url": "https://managed.test"}))
    (tmp_path / "tokens.json").write_text(json.dumps({"dreamlake-token": "private-token"}))
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"status": "ready"})
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(**kw, transport=httpx.MockTransport(handle)))
    run = dash_writer("team", "project")
    assert run.ensure_setup() == {"status": "ready"}
    assert str(seen[0].url) == "https://managed.test/managed-experiments/ns/team/projects/project/status"
    assert seen[0].headers["authorization"] == "Bearer private-token"
    assert "private-token" not in repr(run)
    assert "dreamlake" not in sys.modules
