import httpx
import pytest
from dreamlake.api._client import DreamLakeClient as Client


def test_explicit_note_identity_is_scoped_to_api_and_note_operations(monkeypatch):
    monkeypatch.setenv('DREAMLAKE_AGENT_ID', 'session-1')
    monkeypatch.setenv('DREAMLAKE_AGENT_NAME', 'Codex')
    c = Client(dl_url='https://api.example.com', token='token')
    for path in ['/namespaces/ns/notes/id/body', '/namespaces/ns/notes/id/diff', '/namespaces/ns/notes/id/sections/header']:
        request = httpx.Request('GET', 'https://api.example.com' + path)
        c._note_agent_headers(request)
        assert request.headers['X-DreamLake-Agent-Id'] == 'session-1'
    for url in ['https://other.example.com/namespaces/ns/notes/id/body', 'https://api.example.com/me', 'https://api.example.com/namespaces/ns/notes/id/agent-activity']:
        request = httpx.Request('GET', url)
        c._note_agent_headers(request)
        assert 'X-DreamLake-Agent-Id' not in request.headers


def test_human_and_invalid_identity(monkeypatch):
    monkeypatch.delenv('DREAMLAKE_AGENT_ID', raising=False)
    c = Client(dl_url='https://api.example.com', token='token')
    request = httpx.Request('GET', 'https://api.example.com/namespaces/ns/notes/id/body')
    c._note_agent_headers(request)
    assert 'X-DreamLake-Agent-Id' not in request.headers
    monkeypatch.setenv('DREAMLAKE_AGENT_ID', 'bad\n')
    with pytest.raises(ValueError):
        c._note_agent_headers(request)


def test_http_client_sends_attribution_without_mutation(monkeypatch):
    monkeypatch.setenv('DREAMLAKE_AGENT_ID', 'fixture-session')
    requests = []
    def handler(req):
        requests.append(req)
        return httpx.Response(200, json={})
    c = Client(dl_url='https://api.example.com', token='token', transport=httpx.MockTransport(handler))
    with c.http() as http:
        http.get('/namespaces/ns/notes/id/body', headers={'If-Match': 'original'})
    assert requests[0].headers['X-DreamLake-Agent-Id'] == 'fixture-session'
    assert requests[0].headers['If-Match'] == 'original'
    assert requests[0].method == 'GET'
