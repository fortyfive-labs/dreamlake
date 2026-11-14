import json
import httpx
import pytest
from dreamlake.api._client import DreamLakeClient
from dreamlake.api.projects import ProjectAccessError

ID = '507f1f77bcf86cd799439011'

def test_update_binds_namespace_stable_id_and_revision():
    calls = []
    def serve(request):
        calls.append(request)
        return httpx.Response(200, json={'id': ID, 'name': 'New', 'slug': 'new', 'slugMode': 'auto', 'slugRevision': 4})
    api = DreamLakeClient(token='synthetic', transport=httpx.MockTransport(serve)).projects
    result = api.update('acme', 'old', name='New')
    assert result['slug'] == 'new'
    assert calls[1].url.path == f'/namespaces/acme/projects/{ID}'
    assert json.loads(calls[1].content) == {'name': 'New', 'expectedProjectId': ID, 'expectedSlugRevision': 4}
    api.update('acme', ID, slug='stable')
    assert json.loads(calls[-1].content)['slug'] == 'stable'
    api.update('acme', ID, slug_mode='auto')
    assert json.loads(calls[-1].content)['slugMode'] == 'auto'

def test_refuses_unsupported_or_wrong_identity_without_writes():
    calls = []
    def serve(request):
        calls.append(request)
        return httpx.Response(200, json={'id': ID})
    api = DreamLakeClient(token='synthetic', transport=httpx.MockTransport(serve)).projects
    with pytest.raises(ProjectAccessError, match='UNSUPPORTED'):
        api.update('acme', 'old', name='New')
    assert [r.method for r in calls] == ['GET']
    with pytest.raises(ValueError):
        api.update('acme', ID, slug='manual', slug_mode='auto')
    assert len(calls) == 1

def test_conflict_is_not_retried():
    calls = []
    def serve(request):
        calls.append(request)
        if request.method == 'PATCH':
            return httpx.Response(409, json={'error': 'PROJECT_SLUG_CHANGED'})
        return httpx.Response(200, json={'id': ID, 'slugMode': 'auto', 'slugRevision': 2})
    api = DreamLakeClient(token='synthetic', transport=httpx.MockTransport(serve)).projects
    with pytest.raises(ProjectAccessError) as caught:
        api.update('acme', ID, name='New', expected_slug_revision=1)
    assert caught.value.status == 409
    assert len(calls) == 2
