"""Host client contract tests plus subprocess/HTTP/persistent-receipt fixture.

The HTTP fixture exercises the Python client protocol, not the real host server.
Real backend/CP/nymph acceptance is a separate coordinated integration test.
"""
import base64
import json
import os
import sqlite3
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from dreamlake import DreamLakeClient
from dreamlake.api.hosts import (
    HostAuthorizationError,
    HostConfigurationError,
    HostConflictError,
)

NAME = "fortyfive/bos14/bos14-ctrl"
KEY = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
GRANT = "synthetic-bootstrap-secret"


def test_configuration_and_conflicts(tmp_path):
    hosts = DreamLakeClient(token="test").hosts
    config = tmp_path / "host.json"
    config.write_text(json.dumps({"name": NAME, "ssh": {"host": "ctrl", "port": 2222}}))
    assert hosts.plan(config=config)["ssh"]["args"] == ["-p", "2222", "ctrl"]
    assert hosts.plan(config=config, ssh="-J ge@jump\n -i '/tmp/key with space' ctrl")["ssh"]["args"] == ["-J", "ge@jump", "-i", "/tmp/key with space", "ctrl"]
    assert hosts.plan("bos14-ctrl", prefix="fortyfive/bos14", ssh="ctrl")["name"] == NAME
    for kwargs in [{"name": NAME, "prefix": "other/bos14", "ssh": "ctrl"},
                   {"name": NAME, "ssh": "ctrl echo secret"},
                   {"name": NAME, "ssh": {"host": "ctrl", "password": "DO-NOT-ECHO"}},
                   {"name": NAME, "ssh": {"host": "ge@ctrl", "user": "other"}},
                   {"name": NAME, "ssh": "-o ProxyCommand=DO-NOT-ECHO ctrl"}]:
        with pytest.raises(HostConfigurationError) as error:
            hosts.plan(**kwargs)
        assert "DO-NOT-ECHO" not in str(error.value)
    config.write_text('{"password":"DO-NOT-ECHO"')
    with pytest.raises(HostConfigurationError) as error:
        hosts.plan(config=config)
    assert "DO-NOT-ECHO" not in str(error.value)


def test_dry_run_and_save_refusal_have_no_effects(monkeypatch):
    monkeypatch.setattr("dreamlake.api.hosts._remote", lambda *a: pytest.fail("remote effect"))
    hosts = DreamLakeClient(token="test", transport=httpx.MockTransport(lambda r: pytest.fail("network effect"))).hosts
    result = hosts.enroll(NAME, ssh="ctrl", save_credentials=True, dry_run=True)
    assert result["credentialSaveRequested"] and not result["credentialsSaved"]
    with pytest.raises(HostConfigurationError, match="not implemented"):
        hosts.enroll(NAME, ssh="ctrl", save_credentials=True)
    for bad in [float("nan"), -1, 601, True]:
        with pytest.raises(HostConfigurationError):
            hosts.enroll(NAME, ssh="ctrl", wait_seconds=bad)


def test_authorization_precedes_target_effects(monkeypatch):
    monkeypatch.setattr("dreamlake.api.hosts._remote", lambda *a: pytest.fail("remote effect"))
    hosts = DreamLakeClient(token="test", transport=httpx.MockTransport(lambda r: httpx.Response(403, text=GRANT))).hosts
    with pytest.raises(HostAuthorizationError) as error:
        hosts.enroll(NAME, ssh="ctrl", request_id="retry-me")
    assert error.value.status == 403 and error.value.request_id == "retry-me"
    assert GRANT not in str(error.value)


def test_pending_and_redacted_conflict(monkeypatch):
    monkeypatch.setattr("dreamlake.api.hosts._remote", lambda args, p: {"unixUser": "ge", "publicKey": KEY} if p["action"] == "probe" else {"started": True})
    def handler(req):
        if req.url.path.endswith("/hosts"):
            assert req.url.params["prefix"] == "fortyfive/bos14"
        if req.method == "POST":
            return httpx.Response(200, json={"host": {"id": "h1", "name": NAME},
                "enrollment": {"id": "e1", "machineId": "m1"}, "operationId": "a" * 24,
                "bootstrap": {"namespace": "distinct-controlplane", "controlPlaneUrl": "https://cp.example", "token": GRANT, "expiresAt": "2099-01-01T00:00:00.000Z"}})
        return httpx.Response(200, json={"hosts": [], "enrollments": [{"id": "e1", "state": "pending"}]})
    hosts = DreamLakeClient(token="test", transport=httpx.MockTransport(handler)).hosts
    result = hosts.enroll(NAME, ssh="ctrl", wait_seconds=0)
    assert result["status"] == "pending" and not result["enrolled"]
    assert GRANT not in repr(result)
    conflict = DreamLakeClient(token="test", transport=httpx.MockTransport(lambda r: httpx.Response(409, text=GRANT))).hosts
    with pytest.raises(HostConflictError) as error:
        conflict.enroll(NAME, ssh="ctrl")
    assert GRANT not in str(error.value)


@contextmanager
def api_server(db_path, online=True):
    db = sqlite3.connect(db_path, check_same_thread=False)
    db.execute("create table if not exists receipts (request_id text primary key, payload text)")
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def reply(self, status, value):
            data = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def do_GET(self):
            if self.headers.get("Authorization") != "Bearer test-user-token":
                self.reply(403, {"error": GRANT}); return
            if not self.path.startswith("/namespaces/fortyfive/hosts"):
                self.reply(403, {"error": "wrong namespace"}); return
            host = {"id": "h1", "name": NAME}
            if self.path.startswith("/namespaces/fortyfive/hosts?"):
                self.reply(200, {"hosts": [host]})
            else:
                self.reply(200, {"host": host, "enrollments": [{"id": "e1", "machineId": "m1", "state": "online" if online else "pending", "statusVerified": True}]})
        def do_POST(self):
            if self.headers.get("Authorization") != "Bearer test-user-token":
                self.reply(403, {"error": GRANT}); return
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert "token" not in body and "ssh" not in body
            key, payload = body["requestId"], json.dumps(body, sort_keys=True)
            previous = db.execute("select payload from receipts where request_id=?", [key]).fetchone()
            if previous and previous[0] != payload:
                self.reply(409, {"error": GRANT}); return
            db.execute("insert or ignore into receipts values (?,?)", [key, payload]); db.commit()
            self.reply(200, {"host": {"id": "h1", "name": body["name"]},
                "enrollment": {"id": "e1", "machineId": "m1"}, "operationId": "a" * 24,
                "bootstrap": {"namespace": "distinct-controlplane", "controlPlaneUrl": "https://cp.example", "token": GRANT, "expiresAt": "2099-01-01T00:00:00.000Z"}})
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", db
    finally:
        server.shutdown(); server.server_close(); thread.join(); db.close()


def test_subprocess_client_real_http_persistent_receipt_and_ssh(tmp_path):
    # Actual child Python consumer and fake SSH executable validate stdin/argv boundary.
    ssh = tmp_path / "ssh"
    ssh.write_text(f'''#!{sys.executable}
import json,sys
p=json.load(sys.stdin)
assert 'BatchMode=yes' in sys.argv
assert {GRANT!r} not in repr(sys.argv)
if p['action']=='probe': print(json.dumps({{'unixUser':'ge','publicKey':{KEY!r}}}))
else:
 assert p['token']=={GRANT!r}
 print(json.dumps({{'started':True}}))
''')
    ssh.chmod(0o700)
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"], "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    code = '''import json,sys
from dreamlake import DreamLakeClient
c=DreamLakeClient(dl_url=sys.argv[1],token='test-user-token')
a=c.hosts.enroll('fortyfive/bos14/bos14-ctrl',ssh='ctrl',request_id='stable-retry',wait_seconds=0)
b=c.hosts.enroll('fortyfive/bos14/bos14-ctrl',ssh='ctrl',request_id='stable-retry',wait_seconds=0)
assert a['host']==b['host'] and a['enrolled']
assert c.hosts.status('fortyfive/bos14/*')['hosts'][0]['id']=='h1'
assert c.hosts.status('fortyfive/bos14/bos14-ctrl')['host']['id']=='h1'
from dreamlake.api.hosts import HostAuthorizationError, HostConflictError
try:
 c.hosts.enroll('fortyfive/bos14/bos14-ctrl',ssh='ctrl',request_id='stable-retry',lakeshore_id='different')
 raise AssertionError('conflicting replay accepted')
except HostConflictError as error:
 assert error.status==409 and 'synthetic-bootstrap-secret' not in str(error)
try:
 c.hosts.enroll('other/bos14/ctrl',ssh='ctrl',request_id='unauthorized')
 raise AssertionError('namespace denied too late')
except HostAuthorizationError as error:
 assert error.status==403
print(json.dumps(b))
'''
    with api_server(tmp_path / "receipts.db") as (url, db):
        result = subprocess.run([sys.executable, "-c", code, url], env=env, capture_output=True, text=True, timeout=20, check=False)
        assert result.returncode == 0, result.stderr
        assert db.execute("select count(*) from receipts").fetchone()[0] == 1
        assert GRANT not in result.stdout + result.stderr
        assert json.loads(result.stdout)["status"] == "online"
    # A new server session retains the same receipt instead of duplicating enrollment.
    with api_server(tmp_path / "receipts.db") as (url, db):
        result = subprocess.run([sys.executable, "-c", code, url], env=env, capture_output=True, text=True, timeout=20, check=False)
        assert result.returncode == 0, result.stderr
        assert db.execute("select count(*) from receipts").fetchone()[0] == 1


def test_status_pagination():
    seen = []
    def handler(req):
        seen.append(str(req.url))
        if not req.url.path.endswith("/h1"):
            assert req.url.params["prefix"] == "fortyfive/bos14"
        if req.url.path.endswith("/h1"):
            return httpx.Response(200, json={"host": {"id": "h1", "name": NAME}})
        page = int(req.url.params["page"])
        return httpx.Response(200, json={"hosts": [{"id": "h1", "name": NAME}] if page == 2 else [], "totalPages": 2, "page": page})
    hosts = DreamLakeClient(token="test", transport=httpx.MockTransport(handler)).hosts
    assert hosts.status(NAME)["host"]["id"] == "h1"
    assert len(seen) == 3
    assert hosts.status("fortyfive/bos14/*", page=2)["page"] == 2
    with pytest.raises(HostConfigurationError):
        hosts.status(NAME, page_size=101)


def _receipt(**bootstrap_overrides):
    return {"host": {"id": "h1", "name": NAME},
            "enrollment": {"id": "e1", "machineId": "m1"}, "operationId": "a" * 24,
            "bootstrap": {"namespace": "different-cp", "controlPlaneUrl": "https://cp.example",
                          "token": GRANT, "expiresAt": "2099-01-01T00:00:00.000Z", **bootstrap_overrides}}


@pytest.mark.parametrize("verified,machine,expected", [(False, "m1", False), (True, "other", False), (True, "m1", True)])
def test_online_requires_verified_matching_identity_and_returns_only_public_fields(monkeypatch, verified, machine, expected):
    monkeypatch.setattr("dreamlake.api.hosts._remote", lambda args, p: {"unixUser": "ge", "publicKey": KEY} if p["action"] == "probe" else {"started": True})
    def handler(req):
        if req.method == "POST":
            return httpx.Response(200, json=_receipt())
        return httpx.Response(200, json={"hosts": [], "enrollments": [{"id": "e1", "machineId": machine,
            "state": "online", "statusVerified": verified, "privateData": GRANT}]})
    result = DreamLakeClient(token="test", transport=httpx.MockTransport(handler)).hosts.enroll(NAME, ssh="ctrl", wait_seconds=0)
    assert result["enrolled"] is expected
    assert result["enrollment"]["machineId"] == "m1"
    assert GRANT not in repr(result)
    assert "privateData" not in result["enrollment"]


@pytest.mark.parametrize("bad", [{"expiresAt": "2000-01-01T00:00:00.000Z"}, {"expiresAt": "invalid"},
    {"expiresAt": "2099-01-01T00:00:00"}, {"controlPlaneUrl": "https://user:secret@cp.example"},
    {"controlPlaneUrl": "file:///tmp/target"}, {"namespace": "invalid/name"}, {"token": "short"}])
def test_invalid_grant_never_reaches_remote_configuration(monkeypatch, bad):
    from dreamlake.api.hosts import HostError
    actions = []
    def remote(args, payload):
        actions.append(payload["action"])
        return {"unixUser": "ge", "publicKey": KEY}
    monkeypatch.setattr("dreamlake.api.hosts._remote", remote)
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=_receipt(**bad) if req.method == "POST" else {"hosts": []}))
    with pytest.raises(HostError, match="invalid or expired") as error:
        DreamLakeClient(token="test", transport=transport).hosts.enroll(NAME, ssh="ctrl", request_id="retry-id")
    assert error.value.request_id == "retry-id" and error.value.operation_id == "a" * 24
    assert actions == ["probe"]
    assert GRANT not in str(error.value)


def test_error_recovery_fields_are_allowlisted_and_redirects_refused():
    from dreamlake.api.hosts import HostError
    for body, expected in [({"error": GRANT, "operationId": "b" * 24, "retryAt": "2099-01-01T00:00:00.000Z"}, True),
                           ({"operationId": GRANT, "retryAt": GRANT}, False),
                           ({"retryAt": "2099-99-01T00:00:00.000Z"}, False)]:
        hosts = DreamLakeClient(token="test", transport=httpx.MockTransport(lambda r, body=body: httpx.Response(409, json=body))).hosts
        with pytest.raises(HostConflictError) as error:
            hosts.enroll(NAME, ssh="ctrl", request_id="retry-id")
        assert error.value.request_id == "retry-id"
        assert error.value.operation_id == ("b" * 24 if expected else None)
        assert error.value.retry_at == ("2099-01-01T00:00:00.000Z" if expected else None)
        assert GRANT not in str(error.value)
    calls = []
    def redirect(req):
        calls.append(str(req.url))
        return httpx.Response(307, headers={"location": "https://other.example/secret"})
    with pytest.raises(HostError) as error:
        DreamLakeClient(token="test", transport=httpx.MockTransport(redirect)).hosts.enroll(NAME, ssh="ctrl")
    assert error.value.status == 307
    assert len(calls) == 1


def test_contract_validation_precedes_all_effects(monkeypatch):
    monkeypatch.setattr("dreamlake.api.hosts._remote", lambda *a: pytest.fail("remote effect"))
    hosts = DreamLakeClient(token="test", transport=httpx.MockTransport(lambda r: pytest.fail("network effect"))).hosts
    for name in ["team.with.dot/group/host", "t" * 65 + "/group/host"]:
        with pytest.raises(HostConfigurationError):
            hosts.enroll(name, ssh="ctrl")
    for request in ["with space", "x" * 129, "", 5]:
        with pytest.raises(HostConfigurationError):
            hosts.enroll(NAME, ssh="ctrl", request_id=request)
    with pytest.raises(HostConfigurationError):
        hosts.status("fortyfive/bos14/*", page=100001)


def test_bootstrap_prerequisite_failure_preserves_request_id(monkeypatch):
    from dreamlake.api.hosts import HostError
    def remote(*args):
        raise HostError("SSH target requires user lingering")
    monkeypatch.setattr("dreamlake.api.hosts._remote", remote)
    hosts = DreamLakeClient(token="test", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"hosts": []}))).hosts
    with pytest.raises(HostError, match="user lingering") as error:
        hosts.enroll(NAME, ssh="ctrl", request_id="retry-id")
    assert error.value.request_id == "retry-id"
