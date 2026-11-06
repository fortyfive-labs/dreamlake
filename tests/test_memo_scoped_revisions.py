"""Room-bound public references remain opaque through supported client operations."""
import hashlib
import json
import httpx
import pytest
from dreamlake.api._client import DreamLakeClient
from dreamlake.api.memos import Memo, MemoSnapshot

ID = '6ae3f318608512b4ba34860c'
REF = 'memo-rtc:v1:' + ID + ':' + 'a' * 64 + ':' + 'b' * 64
NEXT = 'memo-rtc:v1:' + ID + ':' + 'a' * 64 + ':' + 'c' * 64
HASH = 'sha256:' + hashlib.sha256(b'hello').hexdigest()


def test_facade_read_and_append_preserve_scoped_baselines_and_receipts():
    calls = []
    def handler(req):
        calls.append(req)
        if req.method == 'GET':
            assert req.url.params['at'] == REF
            return httpx.Response(200, json=dict(note=ID, content='hello', hash=HASH, revision=REF))
        data = json.loads(req.content)
        assert data['baseRevision'] == REF
        return httpx.Response(200, json=dict(note=ID, hash=HASH, revision=NEXT,
                                             baseRevision=REF, mode=data['mode']))
    client = DreamLakeClient(dl_url='https://example.test', token='test', transport=httpx.MockTransport(handler))
    handle = client.memos(ID, namespace='workspace')
    read = handle.read(at=REF)
    assert read.revision == REF
    receipt = handle.append(text='!', at=read)
    assert receipt.revision == NEXT and receipt.base_revision == REF
    assert len(calls) == 2


@pytest.mark.parametrize('reference', [REF, 'rtc:' + 'd' * 64])
def test_command_reconciliation_accepts_complete_scoped_and_historical_receipts(reference):
    command = 'memo-command-1234'
    def handler(req):
        return httpx.Response(200, json=dict(commandId=command, state='accepted', outcome='accepted',
            receipt=dict(note=ID, hash=HASH, revision=reference, baseRevision=reference,
                         mode='exact', commandId=command, outcome='accepted', replayed=True)))
    client = DreamLakeClient(dl_url='https://example.test', token='test', transport=httpx.MockTransport(handler))
    status = Memo(ID, namespace='workspace', client=client).command_status(command)
    assert status.receipt.revision == reference


@pytest.mark.parametrize('reference', [REF[:-1], REF + ':extra', REF + '\n', REF.replace('v1:', 'v2:')])
def test_malformed_scoped_reference_is_refused_before_io(reference):
    def never(req):
        raise AssertionError('unexpected request')
    client = DreamLakeClient(dl_url='https://example.test', token='test', transport=httpx.MockTransport(never))
    with pytest.raises(ValueError):
        client.memos(ID, namespace='workspace').read(at=reference)
