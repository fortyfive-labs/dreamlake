import hashlib
import json
import threading
from contextlib import contextmanager

import pytest
from websockets.sync.server import serve
import dreamlake as dl
from dreamlake.api._client import DreamLakeClient
from dreamlake.api.memos import MemoSnapshot

ID = '6ae3f318608512b4ba34860c'
BASE = MemoSnapshot(ID, 'a', 'sha256:' + hashlib.sha256(b'a').hexdigest(), 'rtc:0')

@contextmanager
def stream_fixture(*, fail_at=-1):
    calls, connections, headers = [], [], []
    def handler(socket):
        connections.append(socket)
        headers.append(socket.request.headers)
        for raw in socket:
            message = json.loads(raw)
            calls.append(message)
            if message['type'] == 'open':
                socket.send(json.dumps(dict(type='ready')))
            elif message['type'] == 'end':
                socket.send(json.dumps(dict(type='closed')))
                return
            else:
                n = message['sequence']
                if n == fail_at:
                    socket.close()
                    return
                revision = f'rtc:{n+1}'
                receipt = dict(note=ID, revision=revision, hash=BASE.hash, baseRevision='rtc:0', mode='merge',
                               outcome='accepted', commandId=None, replayed=False, continuation=dict(revision=revision, cursor=8))
                socket.send(json.dumps(dict(type='receipt', sequence=n, receipt=receipt)))
    with serve(handler, '127.0.0.1', 0) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = DreamLakeClient(dl_url=f'http://127.0.0.1:{server.socket.getsockname()[1]}', token='test')
        try:
            yield client.memos(ID, namespace='fortyfive'), calls, connections, headers
        finally:
            server.shutdown()
            thread.join(timeout=2)


def test_native_stream_keeps_one_connection_and_one_cursor_with_ack_backpressure(monkeypatch):
    monkeypatch.setenv('DREAMLAKE_AGENT_ID', 'codex:stream-test')
    monkeypatch.setenv('DREAMLAKE_AGENT_NAME', 'Stream test')
    with stream_fixture() as (memo, calls, connections, headers):
        iterator = memo.stream_insert(chunks=['x', 'y'], at=BASE, cursor=1, intent='Add the example.')
        assert next(iterator).revision == 'rtc:1'
        assert [c['type'] for c in calls] == ['open', 'insert']
        assert next(iterator).continuation['cursor'] == 8
        with pytest.raises(StopIteration): next(iterator)
        assert len(connections) == 1
        assert calls[0] == dict(type='open', baseRevision='rtc:0', cursor=1, mode='merge', intent='Add the example.')
        assert calls[2] == dict(type='insert', sequence=1, text='y')
        assert headers[0]['Authorization'] == 'Bearer test'
        assert headers[0]['X-DreamLake-Agent-Id'] == 'codex:stream-test'


def test_native_stream_lost_ack_preserves_pending_and_never_reconnects():
    with stream_fixture(fail_at=1) as (memo, calls, connections, _):
        iterator = memo.stream_insert(chunks=['x', 'y', 'z'], at=BASE, cursor=1)
        first = next(iterator)
        with pytest.raises(dl.MemoStreamInterrupted) as error: next(iterator)
        assert error.value.last_receipt is first
        assert error.value.pending['sequence'] == 1
        assert error.value.pending['text'] == 'y'
        assert len(connections) == 1
        assert len([c for c in calls if c['type'] == 'insert']) == 2


def test_unicode_chunks_exact_and_close():
    with stream_fixture() as (memo, calls, _, _):
        assert len(list(memo.stream_insert(chunks=['', '😀' * 2049], at=BASE, cursor=1, mode='exact'))) == 2
        assert calls[0]['mode'] == 'exact'
        assert [len(c['text']) for c in calls if c['type'] == 'insert'] == [2048, 1]
    with stream_fixture() as (memo, calls, _, _):
        iterator = memo.stream_insert(chunks=['x', 'y'], at=BASE, cursor=1)
        next(iterator)
        iterator.close()
        assert len([c for c in calls if c['type'] == 'insert']) == 1


@pytest.mark.parametrize('stream_id', ['', False, 0, 'short', 'bad id here'])
def test_invalid_stream_identity_is_refused_without_connecting(stream_id):
    with stream_fixture() as (memo, calls, connections, _):
        with pytest.raises(ValueError, match='stream_id'):
            list(memo.stream_insert(chunks=['x'], at=BASE, cursor=1, stream_id=stream_id))
        assert not calls and not connections
