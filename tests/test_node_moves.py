import json
import httpx
import pytest
from dreamlake.api._client import DreamLakeClient

ID = '507f1f77bcf86cd799439011'
TO = '507f1f77bcf86cd799439012'

@pytest.mark.parametrize('method', ['move_file', 'move_folder'])
def test_move_preserves_expected_path_and_uses_one_mutation(method):
    calls = []
    def handle(req):
        calls.append(req)
        if req.method == 'GET':
            return httpx.Response(200, json={'path': ',p,'})
        assert req.url.path == '/nodes/move'
        assert json.loads(req.content) == {'parentId': TO, 'nodes': [{'id': ID, 'expectedPath': ',p,'}]}
        return httpx.Response(200, json={'nodes': [{'id': ID, 'path': ',p,target,', 'name': 'file'}]})
    client = DreamLakeClient(dl_url='https://example.test', token='test', transport=httpx.MockTransport(handle))
    assert getattr(client, method)(ID, TO)['id'] == ID
    assert [r.method for r in calls] == ['GET', 'POST']


def test_move_failure_not_retried():
    calls = []
    def handle(req):
        calls.append(req)
        return httpx.Response(200, json={'path': ',p,'}) if req.method == 'GET' else httpx.Response(409, json={'error': 'collision'})
    client = DreamLakeClient(dl_url='https://example.test', token='test', transport=httpx.MockTransport(handle))
    with pytest.raises(httpx.HTTPStatusError):
        client.move_nodes([ID], TO)
    assert len(calls) == 2
    with pytest.raises(ValueError):
        client.move_nodes([ID, ID], TO)
