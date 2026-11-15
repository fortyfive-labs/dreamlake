import httpx
import pytest
from dreamlake.api._client import DreamLakeClient
from dreamlake.api.sources import SourceError


def test_source_lifecycle_and_editors_keep_namespace_keys_and_terminal_delete_contract():
    requests = []
    def serve(request):
        requests.append(request)
        if request.method == "DELETE" and "/editors/" in request.url.path:
            return httpx.Response(204)
        if request.method == "DELETE":
            return httpx.Response(202, json={"operationId": "cleanup", "state": "queued"})
        return httpx.Response(200, json={"id": "source", "lifecycle": "disabled"})
    c = DreamLakeClient(token="synthetic", transport=httpx.MockTransport(serve))
    s = c.sources
    s.detail("a/b", "source")
    s.disable("a/b", "source", idempotency_key="same")
    s.disable("a/b", "source", idempotency_key="same")
    s.enable("a/b", "source", idempotency_key="enable")
    s.editors("a/b", "source")
    s.add_editor("a/b", "source", "USER", "user", idempotency_key="editor")
    assert s.remove_editor("a/b", "source", "USER", "user", idempotency_key="remove") is None
    assert s.delete("a/b", "source", idempotency_key="delete") == {"operationId": "cleanup", "state": "queued"}
    assert requests[1].headers["idempotency-key"] == requests[2].headers["idempotency-key"] == "same"
    assert requests[1].content == requests[2].content == b"{}"
    assert requests[1].url.raw_path == b"/namespaces/a%2Fb/sources/source:disable"
    assert requests[6].url.raw_path.endswith(b"/editors/USER/user")


def test_source_denial_not_retried_or_revealed_and_missing_key_rejected():
    calls = []
    def serve(request):
        calls.append(request)
        return httpx.Response(403, json={"error": {"code": "SOURCE_FORBIDDEN", "message": "private detail"}})
    s = DreamLakeClient(token="synthetic", transport=httpx.MockTransport(serve)).sources
    with pytest.raises(SourceError, match="SOURCE_FORBIDDEN") as error:
        s.disable("owner", "source", idempotency_key="stable")
    assert "private detail" not in str(error.value)
    with pytest.raises(ValueError):
        s.enable("owner", "source", idempotency_key="")
    assert len(calls) == 1

@pytest.mark.parametrize('visibility', ['private', 'unlisted', 'public'])
def test_source_visibility_cas_payload_and_acknowledgment(visibility):
    import json
    calls = []
    def serve(request):
        calls.append(request)
        assert request.url.path == '/namespaces/team/sources/source/metadata'
        assert request.method == 'PATCH'
        assert json.loads(request.content) == {'visibility': visibility, 'expectedSettingsRevision': 7}
        return httpx.Response(200, json={'id': 'source', 'visibility': visibility, 'settingsRevision': 8})
    sources = DreamLakeClient(token='synthetic', transport=httpx.MockTransport(serve)).sources
    result = sources.set_visibility('team', 'source', visibility, expected_settings_revision=7, idempotency_key='same')
    assert result['visibility'] == visibility
    assert len(calls) == 1
    with pytest.raises(ValueError):
        sources.set_visibility('team', 'source', 'invalid', expected_settings_revision=7, idempotency_key='same')
    assert len(calls) == 1


def test_source_visibility_unknown_ack_never_retries():
    calls = []
    def serve(request):
        calls.append(request)
        return httpx.Response(200, json={'id': 'different', 'visibility': 'unlisted'})
    sources = DreamLakeClient(token='synthetic', transport=httpx.MockTransport(serve)).sources
    with pytest.raises(SourceError, match='SOURCE_VISIBILITY_RECONCILE_REQUIRED'):
        sources.set_visibility('team', 'source', 'unlisted', expected_settings_revision=7, idempotency_key='same')
    assert len(calls) == 1
