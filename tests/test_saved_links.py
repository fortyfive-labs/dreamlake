import json
import httpx
import pytest
from dreamlake.api._client import DreamLakeClient
from dreamlake.api.saved_links import new_link_id

RID = 'a' * 24
LID = 'b' * 24

def client(handler):
    return DreamLakeClient(dl_url='https://api.invalid', token='explicit', transport=httpx.MockTransport(handler))

def test_saved_link_crud_guards_and_pagination():
    requests = []
    def handle(request):
        requests.append(request)
        assert request.headers['Authorization'] == 'Bearer explicit'
        return httpx.Response(204) if request.method == 'DELETE' else httpx.Response(200, json={'ok': True})
    links = client(handle).links('@ge', 'source', RID)
    links.list(limit=2, cursor=LID)
    links.get(LID)
    links.create('https://dreamlake.ai/ge/projects/example', title='Project', link_id=LID)
    links.update(LID, expected_version=3, title='Edited')
    links.remove(LID, expected_version=4)
    assert [r.method for r in requests] == ['GET', 'GET', 'POST', 'PATCH', 'DELETE']
    assert requests[0].url.path == f'/namespaces/ge/resources/source/{RID}/links'
    assert dict(requests[0].url.params) == {'limit': '2', 'cursor': LID}
    assert json.loads(requests[2].content)['id'] == LID
    assert json.loads(requests[3].content) == {'title': 'Edited', 'expectedVersion': 3}
    assert requests[4].url.params['expectedVersion'] == '4'

@pytest.mark.parametrize('status', [307, 403, 409, 503])
def test_does_not_retry_mutations_or_follow_redirects(status):
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(status, headers={'Location': 'https://different.invalid'}, json={'error': 'refused'})
    with pytest.raises(httpx.HTTPStatusError):
        client(handle).links('ge', 'project', RID).create('https://example.com/', link_id=LID)
    assert len(seen) == 1

@pytest.mark.parametrize('version', [0, -1, True, 1.2, 2**53])
def test_invalid_versions_never_send(version):
    def handle(request):
        pytest.fail('Must not send invalid input')
    with pytest.raises(ValueError):
        client(handle).links('ge', 'project', RID).remove(LID, expected_version=version)

def test_recovery_ids_and_explicit_resource_identity():
    assert len(new_link_id()) == 24
    assert new_link_id() != new_link_id()
    def handle(request):
        pytest.fail('Must not send invalid input')
    for namespace, kind, rid in [('../other', 'project', RID), ('ge', 'unknown', RID), ('ge', 'project', 'name')]:
        with pytest.raises(ValueError):
            client(handle).links(namespace, kind, rid)
