import json
from contextlib import closing

import httpx
import pytest

from dreamlake.api._client import DreamLakeClient
from dreamlake.api.agent import AgentError


class Stream(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    def __iter__(self):
        yield from self.chunks

    def close(self):
        self.closed = True


def client(handler):
    return DreamLakeClient(dl_url="https://api.invalid/prefix", token="test", transport=httpx.MockTransport(handler))


def test_split_utf8_crlf_multiline_and_continuation():
    requests = []
    raw = ': heartbeat\r\ndata: {"type":"delta",\r\ndata: "text":"机器人"}\r\n\r\ndata:{"type":"done"}\n\n'.encode()
    stream = Stream([raw[i:i+1] for i in range(len(raw))])

    def handle(request):
        requests.append(request)
        assert str(request.url) == "https://api.invalid/prefix/agent/chat"
        assert request.headers["Authorization"] == "Bearer test"
        assert request.headers["Accept"] == "text/event-stream"
        body = json.loads(request.content)
        assert body == {"message": "inspect", "sessionId": "a" * 24, "actingNamespace": "team", "page": {"pathname": "/team/scenes/example", "layouts": []}}
        return httpx.Response(200, headers={"content-type": "text/event-stream; charset=utf-8"}, stream=stream)

    events = list(client(handle).agent.chat("inspect", session_id="a" * 24, acting_namespace="team", pathname="/team/scenes/example"))
    assert events == [{"type": "delta", "text": "机器人"}, {"type": "done"}]
    assert stream.closed and len(requests) == 1


@pytest.mark.parametrize("status", [302, 401, 403, 429, 503])
def test_http_error_never_retries_redirects_or_exposes_body(status):
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(status, text="private credential", headers={"location": "https://other.invalid"})
    with pytest.raises(AgentError) as error:
        list(client(handle).agent.chat("hello"))
    assert error.value.status == status
    assert "private" not in str(error.value)
    assert len(requests) == 1


@pytest.mark.parametrize("data,code", [
    (b'data: {"type":"delta","text":"partial"}\n\n', "agent_stream_interrupted"),
    (b'data: {"type":"done"}', "agent_stream_interrupted"),
    (b'data: invalid\n\n', "agent_invalid_event"),
    (b'data: []\n\n', "agent_invalid_event"),
])
def test_partial_or_malformed_is_not_success(data, code):
    stream = Stream([data])
    with pytest.raises(AgentError, match=code):
        list(client(lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)).agent.chat("hello"))
    assert stream.closed


def test_early_close_releases_stream_and_preserves_uncertain_receipt():
    stream = Stream([b'data: {"type":"context","sessionId":"aaaaaaaaaaaaaaaaaaaaaaaa"}\n\n'])
    def handle(request):
        if request.method == "POST":
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)
        assert request.url.params == httpx.QueryParams({"cursor": "next", "limit": "2"})
        return httpx.Response(200, json={"toolCalls": [{"status": "uncertain"}], "nextCursor": "more"})
    api = client(handle).agent
    with closing(api.chat("hello")) as events:
        session_id = next(events)["sessionId"]
    assert stream.closed
    assert api.tool_calls(session_id, cursor="next", limit=2) == {"toolCalls": [{"status": "uncertain"}], "nextCursor": "more"}


def test_server_error_event_is_preserved_and_terminal():
    data = b'data: {"type":"error","error":"agent_busy"}\n\ndata: {"type":"done"}\n\n'
    assert list(client(lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=data)).agent.chat("hello")) == [{"type": "error", "error": "agent_busy"}]


@pytest.mark.parametrize("kwargs", [{"session_id": "native-uuid"}, {"acting_namespace": "../escape"}, {"capabilities": {"layoutActions": 1}}, {"capabilities": {"notesWrite": True}}, {"timeout": 0}])
def test_invalid_inputs_do_not_dispatch(kwargs):
    def handle(_):
        pytest.fail("invalid input dispatched")
    with pytest.raises(ValueError):
        list(client(handle).agent.chat("hello", **kwargs))
