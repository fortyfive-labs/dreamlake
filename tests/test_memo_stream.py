import hashlib
import json
import httpx
import pytest
import dreamlake as dl
from dreamlake.api._client import DreamLakeClient
from dreamlake.api.memos import MemoSnapshot

ID = '6ae3f318608512b4ba34860c'
def snapshot(text, revision):
    return MemoSnapshot(ID, text, 'sha256:' + hashlib.sha256(text.encode()).hexdigest(), revision)

def stream_fixture(*, missing=False, fail=False):
    calls = []
    def handler(req):
        if req.method == 'GET':
            return httpx.Response(200, json=dict(note=ID, content='Human: ax', hash=snapshot('Human: ax', 'rtc:1').hash, revision='rtc:1'))
        body = json.loads(req.content)
        calls.append(body)
        if fail and len(calls) == 2:
            raise httpx.ReadError('lost ACK', request=req)
        n = len(calls)
        data = dict(note=ID, revision=f'rtc:{n}', hash=snapshot('Human: ax', f'rtc:{n}').hash,
                    baseRevision=body['baseRevision'], mode=body['mode'], outcome='accepted',
                    commandId=body['commandId'], replayed=False)
        if not missing:
            data['continuation'] = dict(revision=f'rtc:{n}', cursor=8)
        return httpx.Response(200, json=data)
    client = DreamLakeClient(dl_url='https://example.test', token='test', transport=httpx.MockTransport(handler))
    return client.memos(ID, namespace='fortyfive'), calls

def test_stream_uses_ack_cursor_and_revision_with_backpressure():
    note, calls = stream_fixture()
    stream = note.stream_insert(chunks=['x','y'], at=snapshot('a','rtc:0'), cursor=1, stream_id='stream:test')
    first = next(stream)
    assert len(calls) == 1 and first.continuation['cursor'] == 8
    second = next(stream)
    assert second.revision == 'rtc:2'
    assert calls[1]['baseRevision'] == 'rtc:1'
    assert calls[1]['patch'] == '@@ chars 8:8 @@\n~ {+y+}\n'
    assert [c['commandId'] for c in calls] == ['stream:test:0','stream:test:1']
    assert all(c['mode'] == 'merge' for c in calls)
    with pytest.raises(StopIteration): next(stream)

def test_missing_continuation_yields_ack_then_stops_without_resend():
    note, calls = stream_fixture(missing=True)
    stream = note.stream_insert(chunks=['x','y'], at=snapshot('a','rtc:0'), cursor=1)
    first = next(stream)
    with pytest.raises(dl.MemoStreamInterrupted) as error: next(stream)
    assert error.value.last_receipt is first
    assert error.value.pending is None and len(calls) == 1

def test_uncertain_chunk_retains_its_command_and_last_ack():
    note, calls = stream_fixture(fail=True)
    stream = note.stream_insert(chunks=['x','y'], at=snapshot('a','rtc:0'), cursor=1, stream_id='stream:test')
    first = next(stream)
    with pytest.raises(dl.MemoStreamInterrupted) as error: next(stream)
    assert error.value.last_receipt is first
    assert error.value.pending['command_id'] == 'stream:test:1'
    assert len(calls) == 2

def test_close_prevents_future_writes_and_empty_stream_does_nothing():
    note, calls = stream_fixture()
    assert list(note.stream_insert(chunks=[''], at=snapshot('a','rtc:0'), cursor=1)) == []
    stream = note.stream_insert(chunks=['x','y'], at=snapshot('a','rtc:0'), cursor=1, mode='exact')
    next(stream); stream.close()
    assert len(calls) == 1 and calls[0]['mode'] == 'exact'


@pytest.mark.parametrize("stream_id", ["", False, 0, "short", "bad id here"])
def test_invalid_explicit_stream_identity_is_not_silently_replaced(stream_id):
    note, calls = stream_fixture()
    with pytest.raises(ValueError, match="stream_id"):
        list(note.stream_insert(chunks=['x'], at=snapshot('a', 'rtc:0'), cursor=1, stream_id=stream_id))
    assert calls == []
