import json
import httpx
import pytest
from dreamlake.api._client import DreamLakeClient
from dreamlake.api.memos import MemoPermalinkConflict, WriteOutcomeUnknown

ID = '6ae3f318608512b4ba34860c'
RECEIPT = dict(noteId=ID, namespaceSlug='team', slug='new-title',
               href='/team/notes/new-title', aliases=['old-title'])

def client(handler):
    return DreamLakeClient(dl_url='https://example.test', token='test', transport=httpx.MockTransport(handler))

@pytest.mark.parametrize('alias', ['memos', 'notes'])
def test_get_edit_and_history(alias):
    calls = []
    def handler(req):
        calls.append(req)
        assert req.url.path == f'/namespaces/team/notes/{ID}/permalink'
        return httpx.Response(200, json=RECEIPT)
    memo = getattr(client(handler), alias)(ID, namespace='team')
    assert memo.permalink() == RECEIPT
    assert memo.permalink(slug='new-title') == RECEIPT
    assert [json.loads(r.content) for r in calls] == [{}, {'slug': 'new-title'}]
    assert all(r.method == 'POST' for r in calls)


def test_conflict_is_permanent_and_never_retried():
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(409, json={'error': 'permalink_conflict'})
    with pytest.raises(MemoPermalinkConflict):
        client(handler).memos(ID, namespace='team').permalink(slug='taken')
    assert len(calls) == 1


@pytest.mark.parametrize('failure', ['timeout', 'wrong-id', 'bad-json'])
def test_uncertain_mutation_never_retries(failure):
    calls = []
    def handler(req):
        calls.append(req)
        if failure == 'timeout':
            raise httpx.ReadTimeout('lost acknowledgement')
        if failure == 'bad-json':
            return httpx.Response(200, text='invalid')
        return httpx.Response(200, json={**RECEIPT, 'noteId': 'wrong'})
    with pytest.raises(WriteOutcomeUnknown):
        client(handler).memos(ID, namespace='team').permalink(slug='new-title')
    assert len(calls) == 1
