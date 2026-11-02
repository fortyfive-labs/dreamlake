import json

import httpx
import pytest

from dreamlake.api._client import DreamLakeClient
from dreamlake.api.hosts import HostAuthorizationError, HostConfigurationError, HostError

HOST = 'a' * 24
REQUEST = dict(destination_namespace='fortyfive', request_id='stable', expected_revision=4, preview_hash='c' * 64)
COMMON = dict(apiVersion=1, hostId=HOST, sourceNamespaceId='source', destinationNamespaceId='destination', destinationNamespace='fortyfive', name='fortyfive/dev/mac', ownerUserId='caller', visibility='private', revision=4)
PREVIEW = dict(COMMON, previewHash='c' * 64, executable=True, blockers=[])
RECEIPT = dict(COMMON, revision=5, operationId='operation', requestId='stable', recordedAt='2026-11-02T00:00:00Z', replayed=False)


def fixture(handler):
    return DreamLakeClient(dl_url='https://fixture.test', token='synthetic', transport=httpx.MockTransport(handler))


def test_guarded_routes_and_identical_replay():
    calls = []
    def serve(req):
        calls.append(req)
        return httpx.Response(200, json=PREVIEW if req.url.path.endswith('/preview') else RECEIPT)
    client = fixture(serve)
    assert client.hosts.move_preview('a/b', HOST, 'fortyfive') == PREVIEW
    assert client.hosts.move('a/b', HOST, **REQUEST) == RECEIPT
    client.hosts.move('a/b', HOST, **REQUEST)
    assert calls[0].url.raw_path == f'/namespaces/a%2Fb/hosts/{HOST}/move/preview'.encode()
    assert calls[1].content == calls[2].content
    assert json.loads(calls[1].content) == dict(destinationNamespace='fortyfive', requestId='stable', expectedRevision=4, previewHash='c' * 64)


@pytest.mark.parametrize('change', [{'visibility': 'public'}, {'hostId': 'b' * 24}, {'revision': True}, {'blockers': ['run']}])
def test_invalid_previews_fail_closed(change):
    with pytest.raises(HostError):
        fixture(lambda _: httpx.Response(200, json=dict(PREVIEW, **change))).hosts.move_preview('geyang', HOST, 'fortyfive')


@pytest.mark.parametrize('change', [{'requestId': 'other'}, {'revision': 4}, {'visibility': 'public'}, {'recordedAt': 'invalid'}])
def test_invalid_receipts_are_not_completion(change):
    with pytest.raises(HostError):
        fixture(lambda _: httpx.Response(200, json=dict(RECEIPT, **change))).hosts.move('geyang', HOST, **REQUEST)


def test_denials_and_uncertain_writes_do_not_retry_or_expose_body():
    calls = []
    def refuse(req):
        calls.append(req)
        return httpx.Response(403, json={'private': 'secret'})
    with pytest.raises(HostAuthorizationError) as caught:
        fixture(refuse).hosts.move('geyang', HOST, **REQUEST)
    assert caught.value.request_id == 'stable'
    assert 'secret' not in str(caught.value)
    assert len(calls) == 1
    def lost(req):
        calls.append(req)
        raise httpx.ReadError('private transport detail')
    with pytest.raises(HostError) as uncertain:
        fixture(lost).hosts.move('geyang', HOST, **REQUEST)
    assert uncertain.value.request_id == 'stable'
    assert len(calls) == 2


def test_invalid_input_never_sends_request():
    client = fixture(lambda _: pytest.fail('unexpected request'))
    with pytest.raises(HostConfigurationError):
        client.hosts.move('geyang', HOST, **dict(REQUEST, expected_revision=True))
    with pytest.raises(HostConfigurationError):
        client.hosts.move('geyang', '../host', **REQUEST)
