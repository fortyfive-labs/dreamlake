"""Authentication contexts retain identity and constrain namespace defaults."""
import json
import stat

import httpx
import pytest
import yaml

from dreamlake.auth import Auth
from dreamlake.auth.profiles import bound_namespace
from dreamlake.config import Config


@pytest.fixture
def auth(tmp_path, monkeypatch):
    for key in ("DREAMLAKE_API_KEY", "DREAMLAKE_REMOTE", "DREAMLAKE_IDENTITY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("dreamlake.config.config", Config(tmp_path))
    def handle(request):
        if request.headers["Authorization"] == "Bearer invalid":
            return httpx.Response(401)
        if request.url.path == "/auth/me":
            personal = "other-person" if request.headers["Authorization"] == "Bearer other-token" else "personal"
            return httpx.Response(200, json={"id": "account", "namespace": {"slug": personal}})
        assert request.url.path == "/graphql"
        return httpx.Response(200, json={"data": {"myOrganizations": [
            {"slug": "fortyfive", "name": "Fortyfive", "myRole": "owner"},
            {"slug": "dyna-robotics", "name": "Dyna Robotics", "myRole": "member"}]}})
    return Auth(tmp_path, transport=httpx.MockTransport(handle))


def test_profile_namespace_roundtrip_shared_contract(auth):
    auth.login("secret-token", profile="work")
    auth.set_default_namespace("fortyfive")
    assert auth.token() == "secret-token"
    assert auth.status(verify=True)["namespace"] == "personal"
    assert auth.status()["defaultNamespace"] == "fortyfive"
    assert "secret-token" not in repr(auth.status()) + repr(auth.profiles())
    config = json.loads((auth.config_dir / "config.json").read_text())
    assert config["namespace"] == "personal"
    store = yaml.safe_load((auth.config_dir / "auth.yml").read_text())
    assert store["envs"]["work"]["defaultNamespace"] == "fortyfive"
    auth.login("second-secret", profile="dyna")
    auth.set_default_namespace("dyna-robotics")
    auth.switch("work")
    assert auth.status()["defaultNamespace"] == "fortyfive"
    assert auth.token() == "secret-token"
    auth.clear_default_namespace()
    assert auth.status()["defaultNamespace"] is None
    for name in ("auth.yml", "config.json", "tokens.json"):
        assert stat.S_IMODE((auth.config_dir / name).stat().st_mode) == 0o600


def test_rejected_membership_and_login_leave_files_unchanged(auth):
    auth.login("secret-token", profile="work")
    auth.set_default_namespace("fortyfive")
    before = {p.name: p.read_bytes() for p in auth.config_dir.iterdir()}
    with pytest.raises(ValueError, match="not available"):
        auth.set_default_namespace("unrelated")
    with pytest.raises(httpx.HTTPStatusError):
        auth.login("invalid", profile="work")
    assert before == {p.name: p.read_bytes() for p in auth.config_dir.iterdir()}


def test_graphql_error_does_not_change_default(auth):
    auth.login("secret-token")
    before = (auth.config_dir / "auth.yml").read_bytes()
    auth.transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"errors": [{"message": "no"}]}))
    with pytest.raises(RuntimeError, match="verify organization membership"):
        auth.set_default_namespace("fortyfive")
    assert (auth.config_dir / "auth.yml").read_bytes() == before


def test_refresh_binds_new_token_and_clears_different_identity(auth):
    auth.login("secret-token")
    auth.set_default_namespace("fortyfive")
    auth.refresh(token="new-token")
    assert bound_namespace("new-token", "https://api.dreamlake.ai", auth.config_dir) == "fortyfive"
    assert bound_namespace("secret-token", "https://api.dreamlake.ai", auth.config_dir) is None
    auth.refresh(token="other-token")
    assert auth.status()["defaultNamespace"] is None


@pytest.mark.parametrize("key,value", [("DREAMLAKE_API_KEY", "override"), ("DREAMLAKE_REMOTE", "https://other"), ("DREAMLAKE_IDENTITY", "machine")])
def test_overrides_refuse_all_persistent_actions(auth, monkeypatch, key, value):
    auth.login("secret-token")
    before = {p.name: p.read_bytes() for p in auth.config_dir.iterdir()}
    monkeypatch.setenv(key, value)
    for action in [lambda: auth.login("token"), lambda: auth.switch("prod"), auth.logout,
                   lambda: auth.refresh(token="new"), lambda: auth.set_default_namespace("fortyfive"),
                   auth.clear_default_namespace]:
        with pytest.raises(ValueError):
            action()
    assert before == {p.name: p.read_bytes() for p in auth.config_dir.iterdir()}


def test_default_resolution_bound_to_credentials_remote_and_explicit_namespace(auth):
    from dreamlake import _session
    from dreamlake.api._client import DreamLakeClient
    from dreamlake.client import RemoteClient
    auth.login("secret-token")
    auth.set_default_namespace("fortyfive")
    assert _session.get_token() == "secret-token"
    assert _session.get_namespace() == "fortyfive"
    assert _session.get_namespace(namespace="explicit") == "explicit"
    assert bound_namespace("another-token", "https://api.dreamlake.ai", auth.config_dir) is None
    assert bound_namespace("secret-token", "https://another", auth.config_dir) is None
    api = DreamLakeClient(token="secret-token", transport=auth.transport)
    assert api.resolve_namespace() == "fortyfive"
    assert api.resolve_namespace("explicit") == "explicit"
    with_personal = DreamLakeClient(token="other-token", transport=auth.transport)
    assert with_personal.resolve_namespace() == "other-person"
    remote = RemoteClient("https://api.dreamlake.ai", api_key="secret-token")
    try:
        assert remote.resolve_namespace() == "fortyfive"
    finally:
        remote.close()


def test_logout_inactive_preserves_current_and_active_clears_default(auth, monkeypatch):
    from unittest.mock import Mock
    storage = Mock()
    storage.load.return_value = None
    monkeypatch.setattr("dreamlake.auth.token_storage.get_token_storage", lambda *args: storage)
    auth.login("secret-token", profile="a")
    auth.set_default_namespace("fortyfive")
    auth.login("second-token", profile="b", activate=False)
    auth.logout("b")
    assert auth.token() == "secret-token"
    auth.logout()
    assert auth.status()["authenticated"] is False
    assert "default_namespace" not in json.loads((auth.config_dir / "config.json").read_text())
    storage.delete.assert_not_called()  # An isolated store must not alter the OS keyring.


def test_same_token_profiles_keep_independent_defaults(auth):
    auth.login("same-token", profile="fortyfive-work")
    auth.set_default_namespace("fortyfive")
    auth.login("same-token", profile="dyna-work")
    auth.set_default_namespace("dyna-robotics")
    auth.clear_default_namespace(profile="fortyfive-work")
    assert auth.status()["defaultNamespace"] == "dyna-robotics"
    auth.set_default_namespace("fortyfive", profile="fortyfive-work")
    assert auth.status()["defaultNamespace"] == "dyna-robotics"
    auth.logout("fortyfive-work")
    assert auth.status()["defaultNamespace"] == "dyna-robotics"
    assert auth.token() == "same-token"


def test_login_explicit_server_replaces_profile_server(auth):
    auth.login("same-token", profile="work", server="https://prod.invalid")
    auth.set_default_namespace("fortyfive")
    auth.login("same-token", profile="work", server="https://staging.invalid", environment="staging")
    assert auth.status()["server"] == "https://staging.invalid"
    assert auth.status()["defaultNamespace"] is None
    assert auth.profiles()[0]["environment"] == "staging"


def test_explicit_builtin_environment_selects_server_and_auth(auth):
    auth.login("secret-token", profile="work", environment="prod")
    auth.set_default_namespace("fortyfive")
    auth.login("staging-token", profile="work", environment="staging")
    saved = yaml.safe_load((auth.config_dir / "auth.yml").read_text())["envs"]["work"]
    assert saved["server"] == "https://staging-api.dreamlake.ai"
    assert saved["auth"] == "https://staging-auth.vuer.ai"
    assert saved["bss"] == "https://bs-0b713023d6574ac2936eb1e9cbe9cfaa.ecs.us-east-1.on.aws"
    assert json.loads((auth.config_dir / "config.json").read_text())["bss_url"] == saved["bss"]
    assert "defaultNamespace" not in saved
    auth.login("replacement-token", profile="work")
    assert auth.status()["server"] == "https://staging-api.dreamlake.ai"
    with pytest.raises(ValueError, match="Unknown environment"):
        auth.login("secret-token", environment="typo")


def test_refresh_uses_saved_deployment_auth_provider(auth, monkeypatch):
    from unittest.mock import Mock
    auth.login("secret-token", environment="staging")
    flow = Mock()
    flow.exchange_token.return_value = "replacement-token"
    factory = Mock(return_value=flow)
    monkeypatch.setattr("dreamlake.auth.device_flow.DeviceFlowClient", factory)
    callback = Mock()
    auth.refresh(on_device_code=callback)
    assert factory.call_args.args[1] == "https://staging-api.dreamlake.ai"
    assert factory.call_args.kwargs["auth_url"] == "https://staging-auth.vuer.ai"
    callback.assert_called_once_with(flow.start_device_flow.return_value)
    assert auth.token() == "replacement-token"
