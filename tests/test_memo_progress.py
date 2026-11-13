import httpx
import pytest
from dreamlake import DreamLakeClient
from dreamlake.api.sessions import SessionError

ID = 'a' * 24

def test_progress_preserves_access_coverage_pagination_and_unknown_state():
    result = {'schemaVersion': 1, 'task': {'id': ID}, 'sessions': [], 'hasMore': True, 'nextCursor': 'b' * 24, 'coverage': 'accessible-linked-sessions-only', 'acceptance': 'pending-human-review'}
    def handle(request):
        assert request.method == 'GET'
        assert request.url.path == f'/namespaces/team/notes/{ID}/progress'
        assert request.url.params == httpx.QueryParams({'pageSize': '2', 'cursor': 'c' * 24})
        assert request.headers['authorization'] == 'Bearer caller'
        return httpx.Response(200, json=result)
    client = DreamLakeClient(dl_url='https://api.invalid', token='caller', transport=httpx.MockTransport(handle))
    assert client.sessions.memo_progress('team', ID, page_size=2, cursor='c' * 24) == result

@pytest.mark.parametrize('kwargs', [{'page_size': 0}, {'page_size': 51}, {'cursor': 'native-uuid'}])
def test_invalid_progress_query_never_dispatches(kwargs):
    def handle(_):
        pytest.fail('invalid input dispatched')
    client = DreamLakeClient(token='caller', transport=httpx.MockTransport(handle))
    with pytest.raises(ValueError):
        client.sessions.memo_progress('team', ID, **kwargs)

@pytest.mark.parametrize('status,body', [(200, {'schemaVersion': 2}), (200, {'schemaVersion': 1, 'sessions': [], 'task': None}), (403, {'private': 'secret'}), (404, {})])
def test_no_fallback_or_acceptance_on_unavailable_progress(status, body):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(status, json=body)
    client = DreamLakeClient(token='caller', transport=httpx.MockTransport(handle))
    with pytest.raises(SessionError) as error:
        client.sessions.memo_progress('team', ID)
    assert 'secret' not in str(error.value)
    assert len(calls) == 1
