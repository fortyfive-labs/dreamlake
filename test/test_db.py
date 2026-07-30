"""
Tests for the storage layering:

- ``dreamlake.db`` — the platform-FREE engine layer: dreamdb re-exported,
  plus ``create``/``open`` against an explicit backend. Must never touch
  the network.
- ``dreamlake._platform`` — the internal platform plumbing presets use:
  catalog registration, credential brokering, lease attachment.

House style (see test_remote_stub.py): a real-socket stub HTTP server whose
behavior is a per-test ``app(method, path, headers, body) -> (status,
payload)`` callable. No mock libraries.

The dreamdb half is injected through ``db._dreamdb`` — the single,
documented import seam, which ``_platform`` also resolves at call time —
because the platform stub brokers an s3:// backend URL that the real native
package would actually try to reach. The direct-backend path
(``backend="file://..."``) IS exercised against the real dreamdb package.
"""

import json
import os
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

import dreamlake.db as db
from dreamlake import _platform, _session
from dreamlake.auth.exceptions import NotAuthenticatedError


# ── Stub server (test_remote_stub.py pattern) ────────────────────────────────

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
        status, payload = self.server.app(self.command, self.path, headers, body)
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


# ── Deterministic auth + env hygiene ─────────────────────────────────────────

TOKEN = "test-token"
NS = "testns"


@pytest.fixture(autouse=True)
def _clean_session(monkeypatch):
    """Deterministic auth for every test: token comes from the env (never the
    developer's real keyring), the namespace cache starts empty, and any
    AWS_* vars the code under test writes are restored afterwards."""
    _session._namespace_cache.clear()
    monkeypatch.setenv("DREAMLAKE_API_KEY", TOKEN)
    # Touch the AWS vars through monkeypatch so its teardown restores the
    # developer's originals even though _platform writes os.environ directly.
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"):
        monkeypatch.setenv(var, "pre-test-stale")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "pre-test-stale")
    yield
    _session._namespace_cache.clear()


@pytest.fixture
def platform(monkeypatch, stub_url):
    """Point the platform resolution at the stub server."""
    monkeypatch.setenv("DREAMLAKE_REMOTE", stub_url)
    return stub_url


# ── Fake dreamdb behind the documented seam ─────────────────────────────────

@pytest.fixture
def fake_dreamdb(monkeypatch):
    """Inject a fake dreamdb through db._dreamdb — _platform resolves the
    same seam at call time, so one patch covers both layers."""
    calls = {"create": [], "open": []}

    class FakeDataset:
        def __init__(self, ref, schema, backend):
            self.ref, self.schema, self.backend = ref, schema, backend
            self.meta = {}

        def set_meta(self, key, value):
            self.meta[key] = value

        @classmethod
        def create(cls, ref, schema, backend):
            calls["create"].append({"ref": ref, "schema": schema, "backend": backend})
            return cls(ref, schema, backend)

        @classmethod
        def open(cls, ref, schema=None, backend=""):
            calls["open"].append({"ref": ref, "schema": schema, "backend": backend})
            return cls(ref, schema, backend)

    fake = types.SimpleNamespace(Dataset=FakeDataset)
    monkeypatch.setattr(db, "_dreamdb", lambda: fake)
    return calls


# ── Stub app for the platform flow ───────────────────────────────────────────

BACKEND_URL = f"s3://dreamlake-datasets/{NS}/robo-set"


def _broker_payload(session_token=""):
    return {
        "credentials": {
            "accessKeyId": "AKIA-STUB",
            "secretAccessKey": "SECRET-STUB",
            "sessionToken": session_token,
            "expiration": "2026-07-31T00:00:00Z",
        },
        "region": "eu-west-1",
        "bucket": "dreamlake-datasets",
        "prefix": f"{NS}/robo-set",
        "backendUrl": BACKEND_URL,
        "refName": "main",
    }


def _make_app(seen, session_token="", dataset_exists=True):
    """Handle the whole platform surface: /auth/me, catalog CRUD, broker."""

    def app(method, path, headers, body):
        parsed = urlparse(path)
        route = parsed.path
        seen.append({
            "method": method,
            "route": route,
            "query": parse_qs(parsed.query),
            "auth": headers.get("authorization"),
            "body": json.loads(body) if body else None,
        })
        if method == "GET" and route == "/auth/me":
            return 200, {"id": "u-1", "namespace": {"id": "n-1", "slug": NS}}
        if method == "POST" and route == f"/namespaces/{NS}/datasets":
            return 201, {"name": body and json.loads(body).get("name")}
        if method == "GET" and route == f"/namespaces/{NS}/datasets":
            return 200, {"datasets": [{"name": "robo-set", "schemaType": "robot"}]}
        if method == "GET" and route == f"/namespaces/{NS}/datasets/robo-set":
            if dataset_exists:
                return 200, {"name": "robo-set", "schemaType": "robot"}
            return 404, {"error": "dataset not found"}
        if method == "POST" and route == f"/namespaces/{NS}/datasets/robo-set/upload-credentials":
            return 200, _broker_payload(session_token)
        if method == "DELETE" and route in (
            f"/namespaces/{NS}/datasets/robo-set",
            f"/namespaces/{NS}/datasets/robo-set/purge",
        ):
            return 200, {"deleted": True}
        return 404, {"error": f"unhandled {method} {route}"}

    return app


# ── _platform.create_dataset — platform flow end to end ─────────────────────

class TestCreatePlatformFlow:
    def test_full_flow(self, stub_server, platform, fake_dreamdb):
        seen = []
        stub_server.app = _make_app(seen, session_token="")
        schema = object()

        ds = _platform.create_dataset(
            "robo-set", schema,
            schema_type="robot",
            schema_json='{"fields": []}',
            visibility="private",
        )

        # 1) catalog registration — exact body + Bearer token
        create_req = next(
            r for r in seen
            if r["method"] == "POST" and r["route"] == f"/namespaces/{NS}/datasets"
        )
        assert create_req["body"] == {
            "name": "robo-set",
            "schemaType": "robot",
            "schemaJson": '{"fields": []}',
            "visibility": "private",
        }
        assert create_req["auth"] == f"Bearer {TOKEN}"

        # 2) credential broker call — duration passed through
        broker_req = next(r for r in seen if r["route"].endswith("/upload-credentials"))
        assert broker_req["method"] == "POST"
        assert broker_req["body"] == {"durationSeconds": 43200}
        assert broker_req["auth"] == f"Bearer {TOKEN}"

        # 3) lease exported as env vars; empty sessionToken must POP the var
        #    (an empty AWS_SESSION_TOKEN breaks SigV4 on the MinIO static path)
        assert os.environ["AWS_ACCESS_KEY_ID"] == "AKIA-STUB"
        assert os.environ["AWS_SECRET_ACCESS_KEY"] == "SECRET-STUB"
        assert os.environ["AWS_REGION"] == "eu-west-1"
        assert "AWS_SESSION_TOKEN" not in os.environ

        # 4) dreamdb create against the brokered backend, ref from the response
        assert fake_dreamdb["create"] == [
            {"ref": "main", "schema": schema, "backend": BACKEND_URL}
        ]
        assert fake_dreamdb["open"] == []

        # 5) the space is self-describing, and the lease rides on the handle
        assert ds.meta == {"dreamdb.schema_type": "robot"}
        assert ds.dreamlake_lease == {
            "backend_url": BACKEND_URL,
            "expiration": "2026-07-31T00:00:00Z",
            "namespace": NS,
            "name": "robo-set",
            "prefix": f"{NS}/robo-set",
        }

    def test_nonempty_session_token_is_set(self, stub_server, platform, fake_dreamdb):
        stub_server.app = _make_app([], session_token="STS-TOKEN")
        _platform.create_dataset("robo-set", object(), schema_type="robot")
        assert os.environ["AWS_SESSION_TOKEN"] == "STS-TOKEN"

    def test_conflict_409_says_open_instead(self, stub_server, platform, fake_dreamdb):
        def app(method, path, headers, body):
            route = urlparse(path).path
            if route == "/auth/me":
                return 200, {"namespace": {"slug": NS}}
            if method == "POST" and route == f"/namespaces/{NS}/datasets":
                return 409, {"error": "already exists"}
            return 404, {"error": "unhandled"}

        stub_server.app = app
        with pytest.raises(_platform.DatasetExistsError, match="open it instead"):
            _platform.create_dataset("robo-set", object(), schema_type="robot")
        assert fake_dreamdb["create"] == []

    def test_server_500_carries_body_text(self, stub_server, platform, fake_dreamdb):
        def app(method, path, headers, body):
            route = urlparse(path).path
            if route == "/auth/me":
                return 200, {"namespace": {"slug": NS}}
            return 500, {"error": "kaboom-xyz"}

        stub_server.app = app
        with pytest.raises(_platform.PlatformError, match="kaboom-xyz") as exc:
            _platform.create_dataset("robo-set", object(), schema_type="robot")
        assert "500" in str(exc.value)

    def test_broker_500_is_platform_error(self, stub_server, platform, fake_dreamdb):
        def app(method, path, headers, body):
            route = urlparse(path).path
            if route == "/auth/me":
                return 200, {"namespace": {"slug": NS}}
            if method == "POST" and route == f"/namespaces/{NS}/datasets":
                return 201, {"name": "robo-set"}
            if route.endswith("/upload-credentials"):
                return 500, {"error": "broker down"}
            return 404, {"error": "unhandled"}

        stub_server.app = app
        with pytest.raises(_platform.PlatformError, match="broker down"):
            _platform.create_dataset("robo-set", object(), schema_type="robot")
        assert fake_dreamdb["create"] == []

    def test_not_authenticated(self, monkeypatch, fake_dreamdb):
        monkeypatch.delenv("DREAMLAKE_API_KEY", raising=False)

        class NoToken:
            def load(self, key):
                return None

        monkeypatch.setattr(
            "dreamlake.auth.token_storage.get_token_storage",
            lambda config_dir=None: NoToken(),
        )
        with pytest.raises(NotAuthenticatedError, match="dreamlake login"):
            _platform.create_dataset("robo-set", object(), schema_type="robot")


# ── _platform.open_dataset ───────────────────────────────────────────────────

class TestOpenPlatformFlow:
    def test_full_flow(self, stub_server, platform, fake_dreamdb):
        seen = []
        stub_server.app = _make_app(seen, session_token="STS")
        schema = object()

        ds = _platform.open_dataset("robo-set", schema=schema, duration_seconds=600)

        lookup = next(
            r for r in seen
            if r["method"] == "GET" and r["route"] == f"/namespaces/{NS}/datasets/robo-set"
        )
        assert lookup["auth"] == f"Bearer {TOKEN}"

        broker_req = next(r for r in seen if r["route"].endswith("/upload-credentials"))
        assert broker_req["body"] == {"durationSeconds": 600}

        assert fake_dreamdb["open"] == [
            {"ref": "main", "schema": schema, "backend": BACKEND_URL}
        ]
        assert fake_dreamdb["create"] == []
        assert ds.dreamlake_lease["name"] == "robo-set"
        assert ds.dreamlake_lease["backend_url"] == BACKEND_URL
        assert os.environ["AWS_SESSION_TOKEN"] == "STS"

    def test_missing_404_says_create_first(self, stub_server, platform, fake_dreamdb):
        stub_server.app = _make_app([], dataset_exists=False)
        with pytest.raises(_platform.DatasetNotFoundError, match="create it first"):
            _platform.open_dataset("robo-set")
        assert fake_dreamdb["open"] == []


# ── _platform.list_datasets / delete_dataset — request shapes ────────────────

class TestListDelete:
    def test_list(self, stub_server, platform):
        seen = []
        stub_server.app = _make_app(seen)

        result = _platform.list_datasets()

        assert result == [{"name": "robo-set", "schemaType": "robot"}]
        req = next(
            r for r in seen
            if r["method"] == "GET" and r["route"] == f"/namespaces/{NS}/datasets"
        )
        assert req["auth"] == f"Bearer {TOKEN}"
        assert req["query"] == {}

    def test_list_filters_by_schema_type(self, stub_server, platform):
        seen = []
        stub_server.app = _make_app(seen)

        _platform.list_datasets(schema_type="robot")

        req = next(
            r for r in seen
            if r["method"] == "GET" and r["route"] == f"/namespaces/{NS}/datasets"
        )
        assert req["query"] == {"schemaType": ["robot"]}

    def test_delete(self, stub_server, platform):
        seen = []
        stub_server.app = _make_app(seen)

        _platform.delete_dataset("robo-set")

        req = next(r for r in seen if r["method"] == "DELETE")
        assert req["route"] == f"/namespaces/{NS}/datasets/robo-set"

    def test_delete_purge(self, stub_server, platform):
        seen = []
        stub_server.app = _make_app(seen)

        _platform.delete_dataset("robo-set", purge=True)

        req = next(r for r in seen if r["method"] == "DELETE")
        assert req["route"] == f"/namespaces/{NS}/datasets/robo-set/purge"

    def test_delete_missing_404(self, stub_server, platform):
        def app(method, path, headers, body):
            route = urlparse(path).path
            if route == "/auth/me":
                return 200, {"namespace": {"slug": NS}}
            return 404, {"error": "no such dataset"}

        stub_server.app = app
        with pytest.raises(_platform.DatasetNotFoundError, match="not found"):
            _platform.delete_dataset("ghost")


# ── Re-export: dreamlake.db IS dreamdb ───────────────────────────────────────

class TestReExport:
    def test_forwards_to_dreamdb(self):
        import dreamdb

        assert db.Schema is dreamdb.Schema
        assert db.Dataset is dreamdb.Dataset
        assert (
            db.train_and_publish_ivf_centroids
            is dreamdb.train_and_publish_ivf_centroids
        )

    def test_missing_attribute_is_attribute_error(self):
        with pytest.raises(AttributeError):
            db.no_such_thing_anywhere
        # Dunder probes from import machinery must never turn into
        # ImportError, even if dreamdb were missing.
        with pytest.raises(AttributeError):
            db.__wibble__

    def test_lazy_export_on_dreamlake(self):
        import dreamlake

        assert dreamlake.db is db


# ── dreamlake.db — the platform-free engine layer ────────────────────────────

class TestEngineLayer:
    def test_backend_is_required(self, fake_dreamdb):
        with pytest.raises(TypeError):
            db.create(object())  # no backend= → engine refuses, no fallback
        with pytest.raises(TypeError):
            db.open()

    def test_create_and_reopen_file_backend(self, tmp_path, stub_server, platform):
        """file:// backend round-trip with the REAL dreamdb package; the stub
        records every request so we can prove zero platform traffic."""
        import dreamdb

        seen = []
        stub_server.app = _make_app(seen)

        backend = f"file://{tmp_path}/space"
        schema = dreamdb.Schema()
        schema.add_scalar_string("caption")

        ds = db.create(schema, backend=backend, schema_type="robot", title="named-set")
        assert isinstance(ds, dreamdb.Dataset)

        reopened = db.open(backend=backend)
        meta = reopened.meta()
        assert meta["dreamdb.schema_type"] == "robot"
        assert meta["dreamdb.title"] == "named-set"

        assert seen == []  # the engine layer must never touch the platform

    def test_open_uses_main_ref(self, fake_dreamdb):
        schema = object()
        db.open("file:///tmp/whatever", schema=schema)
        assert fake_dreamdb["open"] == [
            {"ref": "main", "schema": schema, "backend": "file:///tmp/whatever"}
        ]
