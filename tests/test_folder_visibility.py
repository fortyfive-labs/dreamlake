import json
import httpx
import pytest
from dreamlake.api._client import DreamLakeClient
from dreamlake.api.folder_visibility import VisibilityWriteUnconfirmed
ID = 'a' * 24
STAMP = '2026-11-15T10:00:00.000Z'

@pytest.mark.parametrize('kind', ['folders', 'bindrs'])
def test_read_and_one_guarded_visibility_write(kind):
    calls = []
    state = {'id': ID, 'visibility': 'private', 'updatedAt': STAMP, 'secret': 'omit'}
    def handle(req):
        calls.append(req)
        assert req.headers['Authorization'] == 'Bearer synthetic'
        expected = f'/nodes/{ID}/visibility' if kind == 'folders' else f'/bindrs/{ID}' + ('/summary' if req.method == 'GET' else '')
        assert req.url.path == expected
        if req.method == 'PATCH':
            payload = json.loads(req.content)
            assert payload == {'visibility': 'unlisted', 'expectedUpdatedAt': STAMP}
            state['visibility'] = payload['visibility']
        return httpx.Response(200, json=state if kind == 'folders' else {'bindr': state})
    api = getattr(DreamLakeClient(dl_url='https://api.invalid', token='synthetic', transport=httpx.MockTransport(handle)), kind)
    current = api.get_visibility(ID)
    assert 'secret' not in current
    assert api.set_visibility(ID, 'unlisted', expected_updated_at=current['updatedAt'])['visibility'] == 'unlisted'
    assert [r.method for r in calls] == ['GET', 'PATCH']

@pytest.mark.parametrize('kind', ['folders', 'bindrs'])
@pytest.mark.parametrize('failure', ['lost', 'malformed', 'mismatch', 'server'])
def test_unknown_write_never_retries(kind, failure):
    calls = []
    def handle(req):
        calls.append(req)
        if failure == 'lost': raise httpx.ReadTimeout('lost', request=req)
        row = {'id': ID, 'visibility': 'private', 'updatedAt': STAMP}
        return httpx.Response(500 if failure == 'server' else 200,
                              json={} if failure == 'malformed' else row if kind == 'folders' else {'bindr': row})
    api = getattr(DreamLakeClient(dl_url='https://api.invalid', token='synthetic', transport=httpx.MockTransport(handle)), kind)
    with pytest.raises(VisibilityWriteUnconfirmed): api.set_visibility(ID, 'unlisted', expected_updated_at=STAMP)
    assert len(calls) == 1

@pytest.mark.parametrize('kind', ['folders', 'bindrs'])
def test_invalid_visibility_or_version_never_sends(kind):
    def handle(req): pytest.fail('unexpected network request')
    api = getattr(DreamLakeClient(dl_url='https://api.invalid', token='synthetic', transport=httpx.MockTransport(handle)), kind)
    with pytest.raises(ValueError): api.set_visibility(ID, 'invalid', expected_updated_at=STAMP)
    with pytest.raises(ValueError): api.set_visibility(ID, 'unlisted', expected_updated_at='2026-11-15')


def test_bindr_create_visibility_confirmed_before_filing_more_nodes():
    calls = []
    def handle(req):
        calls.append(req)
        assert json.loads(req.content)['visibility'] == 'unlisted'
        return httpx.Response(201, json={'bindr': {'id': ID, 'visibility': 'private'}})
    client = DreamLakeClient(dl_url='https://api.invalid', token='synthetic', transport=httpx.MockTransport(handle))
    with pytest.raises(VisibilityWriteUnconfirmed): client.create_bindr('owner', 'project', 'test', ['one', 'two'], visibility='unlisted')
    assert len(calls) == 1


@pytest.mark.parametrize('exists', [False, True])
def test_top_level_bindr_preserves_requested_visibility(monkeypatch, exists):
    import dreamlake
    from unittest.mock import Mock
    client = Mock()
    monkeypatch.setattr(dreamlake, '_get_client', lambda: client)
    if exists:
        client.get_bindr.return_value = {'id': ID}
        client.bindrs.get_visibility.return_value = {'id': ID, 'updatedAt': STAMP}
        client.bindrs.set_visibility.return_value = {'id': ID, 'visibility': 'unlisted'}
    else:
        request = httpx.Request('GET', 'https://api.invalid/bindrs')
        client.get_bindr.side_effect = httpx.HTTPStatusError('missing', request=request, response=httpx.Response(404, request=request))
        client.create_bindr.return_value = {'id': ID, 'visibility': 'unlisted'}
    result = dreamlake.bindr('set', project='project@ns', visibility='unlisted')
    assert result['visibility'] == 'unlisted'
    if exists:
        client.bindrs.set_visibility.assert_called_once_with(ID, 'unlisted', expected_updated_at=STAMP)
        client.create_bindr.assert_not_called()
    else:
        assert client.create_bindr.call_args.kwargs['visibility'] == 'unlisted'
        client.bindrs.set_visibility.assert_not_called()
