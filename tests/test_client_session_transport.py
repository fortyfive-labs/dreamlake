"""All Python entry points select one identity and honor the API transport."""

import json
from unittest.mock import Mock

import httpx
import pytest

from dreamlake import Episode
from dreamlake.api._client import DreamLakeClient
from dreamlake.auth.exceptions import NotAuthenticatedError
from dreamlake.client import RemoteClient


@pytest.fixture(autouse=True)
def isolated_session(monkeypatch):
    monkeypatch.delenv("DREAMLAKE_API_KEY", raising=False)
    storage = Mock()
    storage.load.return_value = None
    monkeypatch.setattr("dreamlake.auth.token_storage.get_token_storage", lambda: storage)
    # Any request which misses an injected transport must fail before a socket opens.
    def forbid_network(*args, **kwargs):
        pytest.fail("Unexpected live HTTP request")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbid_network)
    return storage


@pytest.mark.parametrize("entry", ["api", "remote", "episode", "hybrid"])
@pytest.mark.parametrize("env", [None, "env-token"])
def test_entry_points_use_env_before_saved_login(entry, env, isolated_session, monkeypatch, tmp_path):
    isolated_session.load.return_value = "saved-token"
    if env:
        monkeypatch.setenv("DREAMLAKE_API_KEY", env)
    expected = env or "saved-token"
    if entry == "api":
        client = DreamLakeClient(dl_url="https://api.invalid")
        assert client._headers() == {"Authorization": f"Bearer {expected}"}
    else:
        if entry == "remote":
            client = RemoteClient("https://api.invalid")
        else:
            episode = Episode("ns/project/run", url="https://api.invalid",
                              root=str(tmp_path) if entry == "hybrid" else None)
            client = episode._client
        try:
            assert client.api_key == expected
            assert client._client.headers["Authorization"] == f"Bearer {expected}"
        finally:
            client.close()
    if env:
        isolated_session.load.assert_not_called()
    else:
        isolated_session.load.assert_called_once_with("dreamlake-token")


@pytest.mark.parametrize("entry", ["api", "remote"])
def test_explicit_credentials_win(entry, isolated_session, monkeypatch):
    monkeypatch.setenv("DREAMLAKE_API_KEY", "env-token")
    isolated_session.load.return_value = "saved-token"
    if entry == "api":
        client = DreamLakeClient(token="explicit")
        assert client._headers()["Authorization"] == "Bearer explicit"
    else:
        client = RemoteClient("https://api.invalid", api_key="explicit")
        try:
            assert client.api_key == "explicit"
        finally:
            client.close()
    isolated_session.load.assert_not_called()


def test_missing_login_preserves_anonymous_api_and_local_episode(tmp_path):
    assert DreamLakeClient()._headers() == {}
    Episode("project/run", root=str(tmp_path))
    with pytest.raises(NotAuthenticatedError, match="DREAMLAKE_API_KEY"):
        RemoteClient("https://api.invalid")
    with pytest.raises(NotAuthenticatedError, match="dreamlake login"):
        Episode("project/run", url="https://api.invalid", root=None)


CASES = [
    ("authorize_upload", ("ns", "project", [{"name": "a"}]), "POST", "/namespaces/ns/projects/project/upload-authorizations", {"files": [{"name": "a"}]}, 60),
    ("resume_upload", ("ns", "project", "key", "upload", 7), "POST", "/namespaces/ns/projects/project/upload-authorizations/resume", {"objectKey": "key", "uploadId": "upload", "size": 7}, 60),
    ("register_node", ({"kind": "folder"},), "POST", "/nodes", {"kind": "folder"}, 30),
    ("register_dl_asset", ("folder", {"kind": "folder"}), "POST", "/nodes", {"kind": "folder"}, 30),
    ("get_auth_me", (), "GET", "/auth/me", None, 10),
    ("lookup_node", ("ns", "project", "folder/a", "episode"), "GET", "/nodes/lookup", None, 15),
    ("get_node_download_url", ("node",), "GET", "/nodes/node/download", None, 30),
]


@pytest.mark.parametrize("method,args,verb,path,body,timeout", CASES)
def test_legacy_requests_use_configured_transport(method, args, verb, path, body, timeout):
    requests = []
    def handle(request):
        requests.append(request)
        assert request.method == verb
        assert request.url == httpx.URL("https://api.invalid/prefix" + path).copy_with(query=request.url.query or None)
        assert request.headers["Authorization"] == "Bearer explicit"
        assert (json.loads(request.content) if request.content else None) == body
        assert request.extensions["timeout"]["read"] == timeout
        return httpx.Response(200, json={"ok": True})
    client = DreamLakeClient(dl_url="https://api.invalid/prefix", token="explicit", transport=httpx.MockTransport(handle))
    assert getattr(client, method)(*args) == {"ok": True}
    assert len(requests) == 1
    if method == "lookup_node":
        assert dict(requests[0].url.params) == {"namespace": "ns", "project": "project", "path": "folder/a", "episode": "episode"}


@pytest.mark.parametrize("method,args,verb,path,body,timeout", CASES)
def test_legacy_requests_preserve_http_errors(method, args, verb, path, body, timeout):
    client = DreamLakeClient(dl_url="https://api.invalid", token="explicit",
                            transport=httpx.MockTransport(lambda request: httpx.Response(403, json={"error": "forbidden"})))
    with pytest.raises(httpx.HTTPStatusError) as error:
        getattr(client, method)(*args)
    assert error.value.response.status_code == 403


def test_download_does_not_follow_storage_redirect():
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://storage.invalid/file"})
    client = DreamLakeClient(dl_url="https://api.invalid", token="explicit", transport=httpx.MockTransport(handle))
    with pytest.raises(httpx.HTTPStatusError):
        client.get_node_download_url("node")
    assert len(requests) == 1
