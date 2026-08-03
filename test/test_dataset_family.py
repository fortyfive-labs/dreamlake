"""Tests for the generic dataset family: ``Dataset`` (custom schemas),
``Schema``, ``Track``, qualified ``namespace/name`` addressing, and the
schemaType dispatch on open.

House style (see test_db.py / test_remote_stub.py): a real-socket stub HTTP
server whose behavior is a per-test ``app(method, path, headers, body) ->
(status, payload)`` callable. No mock libraries.

Unlike test_db.py's fake-dreamdb tests, the platform-flow tests here run
against the REAL dreamdb engine: the stub broker hands out a
``file://<tmp>`` backendUrl, so the whole write/read path — schema compile,
fields mirror, append_many, iter_all_batches — is exercised end to end
without any network storage. Skipped when dreamdb is not installed.
"""

import json
import re
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from dreamlake import _platform, _session
from dreamlake._platform import PlatformError, split_qualified

_has_dreamdb = True
try:  # the platform-flow tests need the real engine
    import dreamdb  # noqa: F401
except ImportError:
    _has_dreamdb = False

needs_dreamdb = pytest.mark.skipif(not _has_dreamdb, reason="dreamdb not installed")


# ── Stub server (test_remote_stub.py pattern) ────────────────────────────────

class _StubServer(ThreadingHTTPServer):
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


NS = "testns"
TOKEN = "test-token"


@pytest.fixture(autouse=True)
def _clean_session(monkeypatch):
    _session._namespace_cache.clear()
    monkeypatch.setenv("DREAMLAKE_API_KEY", TOKEN)
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"):
        monkeypatch.setenv(var, "pre-test-stale")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "pre-test-stale")
    yield
    _session._namespace_cache.clear()


@pytest.fixture
def platform(monkeypatch, stub_server):
    host, port = stub_server.server_address[:2]
    url = f"http://{host}:{port}"
    monkeypatch.setenv("DREAMLAKE_REMOTE", url)
    return url


def _catalog_app(tmp_path, seen, catalog):
    """A living catalog over every namespace: CRUD + broker, with the broker
    handing out file:// backends inside tmp_path so the real dreamdb engine
    does the storage work."""

    def app(method, path, headers, body):
        parsed = urlparse(path)
        route = parsed.path
        query = parse_qs(parsed.query)
        payload = json.loads(body) if body else None
        seen.append({"method": method, "route": route, "query": query, "body": payload})

        if method == "GET" and route == "/auth/me":
            return 200, {"id": "u-1", "namespace": {"id": "n-1", "slug": NS}}

        m = re.match(r"^/namespaces/([^/]+)/datasets$", route)
        if m:
            ns = m.group(1)
            if method == "POST":
                key = (ns, payload["name"])
                if key in catalog:
                    return 409, {"error": "already_exists"}
                catalog[key] = {
                    "name": payload["name"],
                    "schemaType": payload["schemaType"],
                    "schemaJson": payload.get("schemaJson"),
                    "visibility": payload.get("visibility", "private"),
                }
                return 201, catalog[key]
            if method == "GET":
                rows = [r for (n, _), r in catalog.items() if n == ns]
                want = query.get("schemaType", [None])[0]
                if want:
                    rows = [r for r in rows if r["schemaType"] == want]
                return 200, {"datasets": rows}

        m = re.match(r"^/namespaces/([^/]+)/datasets/([^/]+)$", route)
        if m:
            key = (m.group(1), m.group(2))
            row = catalog.get(key)
            if row is None:
                return 404, {"error": "not found"}
            if method == "GET":
                return 200, row
            if method == "PATCH":
                row["visibility"] = payload["visibility"]
                return 200, row
            if method == "DELETE":
                del catalog[key]
                return 200, {"deleted": True}

        m = re.match(r"^/namespaces/([^/]+)/datasets/([^/]+)/upload-credentials$", route)
        if m and method == "POST":
            ns, name = m.group(1), m.group(2)
            if (ns, name) not in catalog:
                return 404, {"error": "not found"}
            return 200, {
                "credentials": {
                    "accessKeyId": "AKIA-STUB",
                    "secretAccessKey": "SECRET-STUB",
                    "sessionToken": "",
                    "expiration": "2099-01-01T00:00:00Z",
                },
                "region": "eu-west-1",
                "bucket": "stub",
                "prefix": f"datasets/{ns}/{name}",
                "backendUrl": f"file://{tmp_path}/{ns}__{name}",
                "refName": "main",
            }

        return 404, {"error": f"unhandled {method} {route}"}

    return app


class _Catalog:
    """What the catalog fixture hands tests: the request log and the row
    store (so a test can fabricate server states the SDK cannot produce)."""

    def __init__(self, seen, store):
        self.seen = seen
        self.store = store

    def __iter__(self):
        return iter(self.seen)


@pytest.fixture
def catalog(stub_server, platform, tmp_path):
    seen, store = [], {}
    stub_server.app = _catalog_app(tmp_path, seen, store)
    return _Catalog(seen, store)


# ── split_qualified — pure ───────────────────────────────────────────────────

class TestSplitQualified:
    def test_bare_name_is_own_namespace(self):
        assert split_qualified("clips") == (None, "clips")

    def test_qualified(self):
        assert split_qualified("acme/clips") == ("acme", "clips")

    def test_at_prefix_tolerated(self):
        assert split_qualified("@acme/clips") == ("acme", "clips")

    @pytest.mark.parametrize("bad", ["a/b/c", "/clips", "acme/", "/", "", "  ", "@/x"])
    def test_invalid(self, bad):
        with pytest.raises(PlatformError, match="dataset name"):
            split_qualified(bad)


# ── Schema — pure declaration + wire format ─────────────────────────────────

class TestSchema:
    def test_round_trip(self):
        from dreamlake.dataset import Schema

        sch = Schema()
        sch.add_video("cam", mime="h264")
        sch.add_image("meta", mime="json")
        sch.add_embedding("clip", dim=512, lsh_bits=14)
        sch.add_scalar_float("temp")
        sch.add_scalar_timestamp("recorded_at")
        fields = sch.to_fields()
        assert Schema.from_fields(fields).to_fields() == fields
        assert {f["name"]: f["type"] for f in fields} == {
            "cam": "video", "meta": "image", "clip": "embedding",
            "temp": "scalar_float", "recorded_at": "scalar_timestamp",
        }

    def test_chainable_like_dreamdb(self):
        from dreamlake.dataset import Schema

        sch = Schema().add_scalar_int("a").add_scalar_bool("b")
        assert [f["name"] for f in sch.to_fields()] == ["a", "b"]

    def test_required_true_rejected(self):
        from dreamlake.dataset import Schema, SchemaError

        with pytest.raises(SchemaError, match="required"):
            Schema().add_scalar_float("x", required=True)

    def test_audio_rejected_with_reason(self):
        from dreamlake.dataset import Schema, SchemaError

        with pytest.raises(SchemaError, match="append_many cannot ingest"):
            Schema().add_audio("mic")

    def test_reserved_and_bad_names(self):
        from dreamlake.dataset import Schema, SchemaError

        with pytest.raises(SchemaError, match="reserved"):
            Schema().add_scalar_float("anchor")
        # dreamdb's own reserved keys start with "_" — already outside the
        # name grammar, so the regex rejects them before the reserved check
        for bad in ("_anchor", "_time_anchors", "Bad-Name"):
            with pytest.raises(SchemaError, match="field name"):
                Schema().add_scalar_float(bad)

    def test_duplicates_and_missing_params(self):
        from dreamlake.dataset import Schema, SchemaError

        sch = Schema().add_scalar_float("x")
        with pytest.raises(SchemaError, match="duplicate"):
            sch.add_scalar_int("x")
        with pytest.raises(SchemaError, match="dim"):
            Schema().add_embedding("v", dim=0)
        with pytest.raises(SchemaError, match="mime"):
            Schema.from_fields([{"name": "v", "type": "video"}])
        with pytest.raises(SchemaError, match="unknown type"):
            Schema.from_fields([{"name": "x", "type": "float"}])


# ── anchors — pure helpers ───────────────────────────────────────────────────

class TestAnchors:
    def test_sequence_anchors(self):
        from dreamlake.dataset import sequence_anchors

        assert sequence_anchors(3) == [0, 1, 2]
        assert sequence_anchors(2, start=10, step=5) == [10, 15]

    def test_to_anchor_ns_datetime(self):
        from dreamlake.dataset._fields import to_anchor_ns

        t = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert to_anchor_ns(t) == int(t.timestamp()) * 10**9
        with pytest.raises(Exception, match="naive"):
            to_anchor_ns(datetime(2026, 1, 1))
        with pytest.raises(Exception, match="bool"):
            to_anchor_ns(True)
        with pytest.raises(Exception, match=">= 0"):
            to_anchor_ns(-1)


# ── Track value validation — pure (no engine, no platform) ──────────────────

class TestTrackEncoding:
    def _t(self, kind, **kw):
        from dreamlake.dataset import Track

        return Track(None, "t", kind, **kw)

    def test_scalar_strictness(self):
        from dreamlake.dataset import DatasetError

        assert self._t("scalar_float")._encode_value(1) == 1.0
        with pytest.raises(DatasetError, match="scalar_float"):
            self._t("scalar_float")._encode_value(True)
        with pytest.raises(DatasetError, match="scalar_int"):
            self._t("scalar_int")._encode_value(1.5)
        with pytest.raises(DatasetError, match="None is not a value"):
            self._t("scalar_int")._encode_value(None)

    def test_json_and_video_gates(self):
        from dreamlake.dataset import DatasetError

        # compact wire form: no separator whitespace, UTF-8 not \uXXXX
        assert self._t("image", mime="json")._encode_value({"a": 1, "b": "中"}) \
            == '{"a":1,"b":"中"}'.encode()
        with pytest.raises(DatasetError, match="ingest"):
            self._t("video", mime="h264")._encode_value(b"...")

    def test_embedding_dim(self):
        from dreamlake.dataset import DatasetError

        assert self._t("embedding", dim=2)._encode_value([1, 2]) == [1.0, 2.0]
        with pytest.raises(DatasetError, match="dim=2"):
            self._t("embedding", dim=2)._encode_value([1.0])


# ── Platform flow against the REAL engine on file:// backends ───────────────

@needs_dreamdb
class TestCustomDatasetFlow:
    def test_create_writes_catalog_and_mirror(self, catalog):
        from dreamlake.dataset import Dataset, Schema

        sch = Schema().add_scalar_float("temp").add_image("meta", mime="json")
        ds = Dataset.create("sensors", schema=sch, schema_type="acme.sensors/v1")

        create_req = next(r for r in catalog
                          if r["method"] == "POST" and r["route"] == f"/namespaces/{NS}/datasets")
        assert create_req["body"]["schemaType"] == "acme.sensors/v1"
        assert json.loads(create_req["body"]["schemaJson"])["fields"][0]["name"] == "temp"
        assert ds.namespace == NS and ds.name == "sensors"
        assert ds.schema_type == "acme.sensors/v1"
        assert [t.name for t in ds.tracks()] == ["temp", "meta"]

    def test_registered_schema_type_steers_to_preset_create(self, catalog):
        from dreamlake.dataset import Dataset, DatasetError, VideoAnnotationDataset

        with pytest.raises(DatasetError, match="VideoAnnotationDataset.create"):
            Dataset.create("x", schema_type=VideoAnnotationDataset.SCHEMA_TYPE)

    def test_rows_and_track_round_trip(self, catalog):
        from dreamlake.dataset import Dataset, DatasetError, Schema

        sch = Schema().add_scalar_float("temp").add_scalar_string("label")
        ds = Dataset.create("rt", schema=sch)
        assert ds.schema_type == "custom/v1"

        ds.append_rows([
            {"anchor": 0, "temp": 21.5, "label": "ok"},
            {"anchor": 5, "temp": 21.7},                 # sparse row
        ])
        got = ds.rows()
        assert got == [
            {"anchor": 0, "temp": 21.5, "label": "ok"},
            {"anchor": 5, "temp": 21.7},
        ]
        # the formal round-trip contract: rows() feeds append_rows verbatim
        ds2 = Dataset.create("rt2", schema=sch)
        ds2.append_rows(got)
        assert ds2.rows() == got

        # column-wise pair: append_range/read and append/get
        t = ds.track("temp")
        t.append_range([(10, 22.0), (11, 22.1)])
        assert t.read(start=10) == [(10, 22.0), (11, 22.1)]
        t.append(12, 22.2)
        assert t.get(12) == 22.2
        assert t.get(999) is None
        # windowed rows
        assert [r["anchor"] for r in ds.rows(start=5, end=11)] == [5, 10]
        # anchors(): count + span primitive
        assert ds.anchors() == [0, 5, 10, 11, 12]
        assert ds.anchors(start=10, end=12) == [10, 11]

    def test_empty_declared_track_reads_empty(self, catalog):
        from dreamlake.dataset import Dataset

        ds = Dataset.create("empty")
        t = ds.add_track("ghost", "scalar_float")
        assert t.read() == []
        assert t.get(0) is None
        assert ds.rows() == []

    def test_add_track_evolution_and_gates(self, catalog):
        from dreamlake.dataset import Dataset, DatasetError

        ds = Dataset.create("evo")               # empty schema, tracks as you go
        ds.add_track("temp", "scalar_float")
        ds.add_track("temp", "scalar_float")     # idempotent
        with pytest.raises(DatasetError, match="cannot change"):
            ds.add_track("temp", "scalar_int")
        with pytest.raises(DatasetError, match="created"):
            ds.add_track("vec", "embedding")
        with pytest.raises(DatasetError, match="unknown track kind"):
            ds.add_track("x", "float")
        with pytest.raises(DatasetError, match="reserved"):
            ds.add_track("anchor", "scalar_float")
        with pytest.raises(DatasetError, match="declare it first"):
            ds.track("nope")
        with pytest.raises(DatasetError, match="declare it first"):
            ds.append_rows([{"anchor": 0, "nope": 1.0}])

        ds.add_track("cam", "video", mime="h264")
        with pytest.raises(DatasetError, match="ingest"):
            ds.track("cam").append(0, b"bytes")
        with pytest.raises(DatasetError, match="no row values"):
            ds.rows(tracks=["cam"])

        ds.track("temp").append(0, 1.0)
        assert [t.name for t in ds.tracks()] == ["temp", "cam"]
        assert ds.rows() == [{"anchor": 0, "temp": 1.0}]   # video excluded by default

    def test_write_once_duplicate_in_batch(self, catalog):
        from dreamlake.dataset import Dataset, DatasetError

        ds = Dataset.create("dups")
        t = ds.add_track("v", "scalar_float")
        with pytest.raises(DatasetError, match="write-once"):
            t.append_range([(1, 1.0), (1, 2.0)])
        with pytest.raises(DatasetError, match="write-once"):
            ds.append_rows([{"anchor": 1, "v": 1.0}, {"anchor": 1, "v": 2.0}])

    def test_open_dispatch(self, catalog):
        from dreamlake.dataset import Dataset, Schema

        Dataset.create("plain", schema=Schema().add_scalar_int("n"))
        ds = Dataset.open("plain")
        assert type(ds) is Dataset
        assert ds.schema_type == "custom/v1"

        Dataset.create("branded", schema_type="acme.unknown/v9")
        assert type(Dataset.open("branded")) is Dataset   # unknown → generic, never refuses

    def test_open_dispatch_reaches_preset(self, catalog):
        from dreamlake.dataset import Dataset, DatasetError

        # A custom space whose CATALOG row claims the preset type: dispatch
        # must select VideoAnnotationDataset, whose strict open then refuses
        # the mismatched space stamp — proof the preset class handled it.
        from dreamlake.dataset import VideoAnnotationDataset

        Dataset.create("imposter")
        catalog.store[(NS, "imposter")]["schemaType"] = VideoAnnotationDataset.SCHEMA_TYPE
        with pytest.raises(DatasetError, match="dreamlake.db"):
            Dataset.open("imposter")

    def test_ensure_open_or_create(self, catalog):
        from dreamlake.dataset import Dataset, DatasetError, Schema

        sch = Schema().add_scalar_float("temp")
        ds1 = Dataset.ensure("e1", schema=sch)               # missing → created
        assert any(r["method"] == "POST" and r["route"].endswith("/datasets") for r in catalog)
        ds2 = Dataset.ensure("e1", schema=sch)               # existing → opened + verified
        assert ds2.tracks()[0].name == "temp"
        with pytest.raises(DatasetError, match="different dataset"):
            Dataset.ensure("e1", schema_type="other/v1")
        with pytest.raises(DatasetError, match="never widens"):
            Dataset.ensure("e1", schema=Schema().add_scalar_float("other"))

    def test_ensure_on_preset_class(self, catalog):
        from dreamlake.dataset import Dataset, DatasetError, Schema, VideoAnnotationDataset

        # preset ensure rejects generic schema args — the preset owns its schema
        with pytest.raises(DatasetError, match="owns its schema"):
            VideoAnnotationDataset.ensure("robo", schema=Schema())
        # bare Dataset.ensure on an existing dataset of ANOTHER type opens it
        # (dispatch semantics, like open) instead of claiming a mismatch
        Dataset.create("mixed", schema_type="acme.x/v1")
        assert Dataset.ensure("mixed").schema_type == "acme.x/v1"

    def test_qualified_names_hit_the_named_namespace(self, catalog):
        from dreamlake.dataset import Dataset

        Dataset.create("org2/shared")
        assert any(r["route"] == "/namespaces/org2/datasets" for r in catalog)
        # bare names still resolve the login's own namespace via /auth/me
        Dataset.create("mine")
        assert any(r["route"] == f"/namespaces/{NS}/datasets" for r in catalog)

        ds = Dataset.open("@org2/shared")
        assert ds.namespace == "org2" and ds.name == "shared"
        Dataset.delete("org2/shared")
        assert any(r["method"] == "DELETE" and r["route"] == "/namespaces/org2/datasets/shared"
                   for r in catalog.seen)

    def test_list_and_visibility(self, catalog):
        from dreamlake.dataset import Dataset

        Dataset.create("l1", schema_type="a/v1")
        Dataset.create("l2", schema_type="b/v1")
        infos = {i.name: i for i in Dataset.list()}
        assert infos["l1"].schema_type == "a/v1" and infos["l1"].namespace == NS
        only = Dataset.list(schema_type="b/v1")
        assert [i.name for i in only] == ["l2"]

        ds = Dataset.open("l1")
        assert ds.visibility == "private"
        ds.set_visibility("public")
        assert ds.visibility == "public"
        assert Dataset.open("l1").visibility == "public"

    def test_expired_lease_says_reload(self, catalog):
        from dreamlake.dataset import Dataset, DatasetError

        ds = Dataset.create("stale")
        ds.add_track("v", "scalar_float")
        ds._ds.dreamlake_lease["expiration"] = "2000-01-01T00:00:00Z"
        with pytest.raises(DatasetError, match="reload"):
            ds.track("v").append(0, 1.0)
        ds.reload()                                  # re-brokers via HTTP token auth
        ds.track("v").append(0, 1.0)
        assert ds.track("v").get(0) == 1.0

    def test_reload_sees_out_of_band_add_track(self, catalog):
        from dreamlake.dataset import Dataset, DatasetError

        ds1 = Dataset.create("shared-mirror")
        ds2 = Dataset.open("shared-mirror")
        ds1.add_track("late", "scalar_float")
        with pytest.raises(DatasetError, match="declare it first"):
            ds2.track("late")
        ds2.reload()
        assert ds2.track("late").kind == "scalar_float"
