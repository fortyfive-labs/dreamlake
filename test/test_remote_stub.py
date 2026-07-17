"""
Tests against a stub HTTP server that mimics dreamlake-server.

These cover behavior that needs exact control over server responses
(raw wire shapes, error statuses) without a live server:

* Hybrid _flush_track durability — a remote 500 must not lose the
  batch: local is written first, the remote failure only warns.
* Remote-only _flush_track failure — the batch goes back into the
  buffer so a later flush can re-send it.
* RemoteClient.read_track_data normalizes the server's raw
  {trackName, points, startIndex, count} response to the documented
  {data, startIndex, endIndex, total, hasMore} shape.
* RemoteClient.api_key is a live property: assigning it rebuilds the
  auth header and invalidates the cached default namespace.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import httpx
import pytest

from dreamlake import Episode
from dreamlake.client import RemoteClient


class _StubServer(ThreadingHTTPServer):
    """HTTP server whose behavior is a per-test ``app`` callable.

    ``app(method, path, headers, body) -> (status, payload_dict)`` with
    lower-cased header names; ``path`` still includes the query string.
    """

    daemon_threads = True
    app = None


class _StubHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _dispatch(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        headers = {key.lower(): value for key, value in self.headers.items()}
        status, payload = self.server.app(
            self.command,
            self.path,
            headers,
            body,
        )
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = _dispatch
    do_POST = _dispatch
    do_PATCH = _dispatch
    do_DELETE = _dispatch


@pytest.fixture
def stub_server():
    server = _StubServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def stub_url(stub_server):
    host, port = stub_server.server_address[:2]
    return f"http://{host}:{port}"


def _episode_upsert_response():
    return 200, {
        "id": "ep-1",
        "name": "stub-episode",
        "status": "running",
    }


class TestHybridFlushDurability:
    """A remote failure during flush must never lose the batch."""

    def test_remote_500_keeps_local_copy(
        self,
        stub_server,
        stub_url,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.setenv("DREAMLAKE_API_KEY", "test-token")
        seen_routes = []

        def app(method, path, headers, body):
            route = urlparse(path).path
            seen_routes.append((method, route))
            if method == "POST" and route == "/namespaces/ns/projects/ws/episodes":
                return _episode_upsert_response()
            if method == "POST" and route.endswith("/append-batch"):
                return 500, {"error": "boom"}
            if method == "GET" and route.endswith("/data"):
                return 200, {
                    "trackName": "metric",
                    "points": [],
                    "startIndex": 0,
                    "count": 0,
                }
            return 404, {"error": f"unhandled {method} {route}"}

        stub_server.app = app

        with Episode(
            prefix="ns/ws/stub-episode",
            url=stub_url,
            root=str(tmp_path),
        ) as episode:
            for step in range(3):
                episode.track("metric").append(value=step * 0.5, step=step)

            with pytest.warns(RuntimeWarning, match="persisted locally"):
                episode.track("metric").flush()

            result = episode.track("metric").read(start_index=0, limit=10)

        # The remote POST was attempted (and 500ed) ...
        assert any(route.endswith("/append-batch") for _method, route in seen_routes)

        # ... but the batch survived in local storage.
        assert result["total"] == 3
        values = [point["data"]["value"] for point in result["data"]]
        assert values == [0.0, 0.5, 1.0]

    def test_remote_only_failure_keeps_batch_resendable(
        self,
        stub_server,
        stub_url,
        monkeypatch,
    ):
        monkeypatch.setenv("DREAMLAKE_API_KEY", "test-token")
        state = {"fail": True}

        def app(method, path, headers, body):
            route = urlparse(path).path
            if method == "POST" and route == "/namespaces/ns/projects/ws/episodes":
                return _episode_upsert_response()
            if method == "POST" and route.endswith("/append-batch"):
                if state["fail"]:
                    return 500, {"error": "boom"}
                payload = json.loads(body)
                count = len(payload["dataPoints"])
                return 200, {
                    "trackId": "t-1",
                    "startIndex": 0,
                    "endIndex": count - 1,
                    "count": count,
                }
            return 404, {"error": f"unhandled {method} {route}"}

        stub_server.app = app

        episode = Episode(
            prefix="ns/ws/stub-episode",
            url=stub_url,
            root=None,
        ).open()

        for step in range(3):
            episode.track("metric").append(value=step, step=step)

        with pytest.raises(httpx.HTTPStatusError):
            episode.track("metric").flush()

        # The batch went back into the buffer — the next flush re-sends it.
        state["fail"] = False
        result = episode.track("metric").flush()
        assert result["count"] == 3

        episode.close()


class TestReadTrackDataNormalization:
    """read_track_data must return the documented (local-storage) shape."""

    POINTS = [
        {"index": "0", "data": {"value": 0.5, "step": 0}},
        {"index": "1", "data": {"value": 0.4, "step": 1}},
        {"index": "2", "data": {"value": 0.3, "step": 2}},
    ]

    def _app(self, method, path, headers, body):
        route = urlparse(path).path
        if method == "GET" and route == "/episodes/ep-1/tracks/metric/data":
            # The server's exact raw schema — not the documented one.
            return 200, {
                "trackName": "metric",
                "points": self.POINTS,
                "startIndex": 0,
                "count": len(self.POINTS),
            }
        return 404, {"error": f"unhandled {method} {route}"}

    def test_full_page_normalized(self, stub_server, stub_url):
        stub_server.app = self._app
        client = RemoteClient(base_url=stub_url, api_key="test-token")

        result = client.read_track_data(
            episode_id="ep-1",
            track_name="metric",
            start_index=0,
            limit=3,
        )

        assert result == {
            "data": self.POINTS,
            "startIndex": 0,
            "endIndex": 2,
            "total": 3,
            "hasMore": True,  # full page (count == limit) → more may exist
        }

    def test_partial_page_has_more_false(self, stub_server, stub_url):
        stub_server.app = self._app
        client = RemoteClient(base_url=stub_url, api_key="test-token")

        result = client.read_track_data(
            episode_id="ep-1",
            track_name="metric",
            start_index=0,
            limit=10,
        )

        assert result["data"] == self.POINTS
        assert result["total"] == 3
        assert result["hasMore"] is False

    def test_empty_track(self, stub_server, stub_url):
        def app(method, path, headers, body):
            return 200, {
                "trackName": "metric",
                "points": [],
                "startIndex": 5,
                "count": 0,
            }

        stub_server.app = app
        client = RemoteClient(base_url=stub_url, api_key="test-token")

        result = client.read_track_data(
            episode_id="ep-1",
            track_name="metric",
            start_index=5,
            limit=10,
        )

        assert result == {
            "data": [],
            "startIndex": 5,
            "endIndex": 4,
            "total": 0,
            "hasMore": False,
        }


class TestApiKeyProperty:
    """Assigning client.api_key must take effect on later requests."""

    def test_setter_rebuilds_header_and_invalidates_namespace(
        self,
        stub_server,
        stub_url,
    ):
        def app(method, path, headers, body):
            route = urlparse(path).path
            if method == "GET" and route == "/auth/me":
                token = headers.get("authorization", "").removeprefix("Bearer ")
                return 200, {
                    "id": "u-1",
                    "namespace": {"id": "n-1", "slug": f"ns-{token}"},
                }
            return 404, {"error": f"unhandled {method} {route}"}

        stub_server.app = app

        client = RemoteClient(base_url=stub_url, api_key="alpha")
        assert client.api_key == "alpha"
        assert client.resolve_namespace() == "ns-alpha"

        client.api_key = "beta"
        assert client.api_key == "beta"
        # New header is used AND the cached namespace was invalidated.
        assert client.resolve_namespace() == "ns-beta"
