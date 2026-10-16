"""`dl.upload()` and `TextTrack.flush()` after the move off BSS.

The SDK used to run a BSS handshake and then register twice — once in BSS,
once in dreamlake-server, with the node pointing at `metadata.bssId`. BSS is
being retired, so the bytes go straight to S3 and the node records
`metadata.s3Key`.

What is worth testing is the part that fails *quietly*:

  - **the signed headers.** Every header the server lists is part of the
    signature. The old code sent its own `Content-Type` on each part, which
    BSS's signature tolerated and S3's does not — it answers 403.
  - **the part plan.** A gap sends the wrong bytes under a signature minted for
    another part, an overlap sends one range twice. Each PUT succeeds, Complete
    succeeds, and the object holds bytes nobody checked.
  - **Complete's body.** A 2xx is where it starts, not where it ends: S3 holds
    the connection open while it assembles the parts, so the body may be an
    `<Error>` under a 200.
  - **the hash.** It is the only client-controlled part of the object key, so a
    truncated one escapes the project's prefix. The old code sent 16 of the 64
    characters.
"""

from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
import pytest

from dreamlake.api._transfer import (
    TransferError,
    completion_body,
    parse_multipart,
    run_multipart,
    sha256_file,
)


# ── the part plan ────────────────────────────────────────────────────


def _plan(size: int, part_size: int, numbers: list[int], base: str = "http://x") -> dict:
    return {
        "kind": "s3-multipart",
        "uploadId": "u-1",
        "partSize": part_size,
        "parts": [{"partNumber": n, "put": {"url": f"{base}/part/{n}", "headers": {}}} for n in numbers],
        "complete": {
            "url": f"{base}/complete",
            "headers": {},
            "method": "POST",
            "bodyFormat": "s3-complete-multipart-xml",
        },
        "abort": {"url": f"{base}/abort", "headers": {}, "method": "DELETE"},
    }


def test_a_full_plan_is_accepted():
    plan = parse_multipart(_plan(10, 4, [1, 2, 3]), 10, "u")
    assert [n for n, _ in plan.parts] == [1, 2, 3]


def test_a_short_plan_is_refused():
    with pytest.raises(TransferError, match="do not cover the file"):
        parse_multipart(_plan(10, 4, [1, 2]), 10, "u")


def test_a_duplicated_part_is_refused():
    with pytest.raises(TransferError, match="already stored|do not cover"):
        parse_multipart(_plan(10, 4, [1, 2, 2]), 10, "u")


def test_a_part_beyond_the_file_is_refused():
    with pytest.raises(TransferError, match="outside the 3"):
        parse_multipart(_plan(10, 4, [1, 2, 9]), 10, "u")


def test_a_resume_subset_is_accepted_when_the_stored_parts_complete_it():
    plan = parse_multipart(_plan(10, 4, [2]), 10, "r", completed=[(1, "e1"), (3, "e3")])
    assert [n for n, _ in plan.parts] == [2]
    assert plan.completed == [(1, "e1"), (3, "e3")]


def test_a_gap_between_stored_and_signed_parts_is_refused():
    # Part 3 is in neither list: nobody sends that range and the object is
    # simply missing its tail.
    with pytest.raises(TransferError, match="do not cover the file"):
        parse_multipart(_plan(10, 4, [2]), 10, "r", completed=[(1, "e1")])


def test_a_part_both_stored_and_signed_is_refused():
    with pytest.raises(TransferError, match="already stored"):
        parse_multipart(_plan(10, 4, [2, 3]), 10, "r", completed=[(1, "e1"), (2, "e2")])


def test_a_completion_body_the_contract_did_not_promise_is_refused():
    bad = _plan(10, 4, [1, 2, 3])
    bad["complete"]["bodyFormat"] = "application/json"
    with pytest.raises(TransferError, match="bodyFormat"):
        parse_multipart(bad, 10, "u")


# ── the completion body ──────────────────────────────────────────────


def test_the_completion_body_is_ordered_by_part_number():
    xml = completion_body([(3, "c"), (1, "a"), (2, "b")])
    assert xml.index("<PartNumber>1<") < xml.index("<PartNumber>2<") < xml.index("<PartNumber>3<")


def test_the_completion_body_escapes_markup_in_an_etag():
    assert "&lt;" in completion_body([(1, "<evil>")])


# ── the hash ─────────────────────────────────────────────────────────


def test_the_hash_is_the_full_sha256(tmp_path: Path):
    # The authorize endpoint refuses anything but 64 lowercase hex. The BSS
    # flow sent the first 16 characters.
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello world")
    assert sha256_file(f) == hashlib.sha256(b"hello world").hexdigest()
    assert len(sha256_file(f)) == 64


def test_the_hash_is_streamed_not_read_whole(tmp_path: Path):
    # Reading the file whole is what put a large video out of reach. A chunk
    # size below the file size proves the loop runs more than once.
    f = tmp_path / "b.bin"
    f.write_bytes(b"x" * 5000)
    assert sha256_file(f, chunk=64) == hashlib.sha256(b"x" * 5000).hexdigest()


# ── against a real server ────────────────────────────────────────────


class _Recorder(BaseHTTPRequestHandler):
    hits: list = []
    fail_on: str | None = None
    complete_body: bytes = b"<CompleteMultipartUploadResult/>"

    def _read(self) -> bytes:
        length = int(self.headers.get("content-length") or 0)
        return self.rfile.read(length) if length else b""

    def _headers(self) -> dict:
        """Lower-cased, because HTTP header names are case-insensitive.

        `dict(self.headers)` keeps whatever case the client sent, so a
        lower-case lookup against it silently never matches — which is how the
        "no Content-Type of our own" assertion here passed while a mutation
        that added one went undetected.
        """
        return {k.lower(): v for k, v in self.headers.items()}

    def do_PUT(self):  # noqa: N802
        body = self._read()
        type(self).hits.append(("PUT", self.path, body, self._headers()))
        if type(self).fail_on and type(self).fail_on in self.path:
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("etag", f'"etag-{self.path.rsplit("/", 1)[-1]}"')
        self.end_headers()

    def do_POST(self):  # noqa: N802
        body = self._read()
        type(self).hits.append(("POST", self.path, body, self._headers()))
        if type(self).fail_on and type(self).fail_on in self.path:
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(type(self).complete_body)

    def do_DELETE(self):  # noqa: N802
        type(self).hits.append(("DELETE", self.path, b"", self._headers()))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_args):
        pass


@pytest.fixture
def server():
    _Recorder.hits = []
    _Recorder.fail_on = None
    _Recorder.complete_body = b"<CompleteMultipartUploadResult/>"
    httpd = HTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", _Recorder
    httpd.shutdown()


def test_parts_go_up_with_the_signed_headers_verbatim(server, tmp_path: Path):
    # Adding a Content-Type of our own is what the BSS flow did, and S3 answers
    # 403 to it. Only Content-Length is added, because a body requires it.
    base, rec = server
    f = tmp_path / "big.bin"
    f.write_bytes(b"0123456789")
    raw = _plan(10, 4, [1, 2, 3], base)
    raw["parts"][0]["put"]["headers"] = {"x-amz-meta-a": "1"}

    run_multipart(httpx.Client(), parse_multipart(raw, 10, "u"), f, 10)

    first = next(h for h in rec.hits if h[1] == "/part/1")
    assert first[3].get("x-amz-meta-a") == "1"
    assert first[3].get("content-type") is None, "sent a Content-Type the signature did not cover"


def test_each_part_carries_its_own_byte_range(server, tmp_path: Path):
    base, rec = server
    f = tmp_path / "big.bin"
    f.write_bytes(b"0123456789")

    run_multipart(httpx.Client(), parse_multipart(_plan(10, 4, [1, 2, 3], base), 10, "u"), f, 10)

    bodies = {h[1]: h[2] for h in rec.hits if h[0] == "PUT"}
    assert bodies["/part/1"] == b"0123"
    assert bodies["/part/2"] == b"4567"
    assert bodies["/part/3"] == b"89"


def test_completion_names_the_stored_parts_as_well(server, tmp_path: Path):
    base, rec = server
    f = tmp_path / "big.bin"
    f.write_bytes(b"0123456789")
    plan = parse_multipart(_plan(10, 4, [2], base), 10, "r", completed=[(1, "stored-1"), (3, "stored-3")])

    run_multipart(httpx.Client(), plan, f, 10)

    assert [h[1] for h in rec.hits if h[0] == "PUT"] == ["/part/2"], "re-sent a stored part"
    xml = next(h[2] for h in rec.hits if h[1] == "/complete").decode()
    assert "<PartNumber>1</PartNumber>" in xml
    assert "<PartNumber>3</PartNumber>" in xml
    assert "stored-1" in xml and "stored-3" in xml


def test_an_error_body_under_a_200_is_a_failure(server, tmp_path: Path):
    # S3 writes the status line before it knows the outcome.
    base, rec = server
    rec.complete_body = b"<Error><Code>InternalError</Code></Error>"
    f = tmp_path / "big.bin"
    f.write_bytes(b"0123456789")

    with pytest.raises(TransferError, match="refused to complete"):
        run_multipart(httpx.Client(), parse_multipart(_plan(10, 4, [1, 2, 3], base), 10, "u"), f, 10)


def test_a_failed_upload_is_aborted(server, tmp_path: Path):
    # The parts already sent are billed and belong to no object.
    base, rec = server
    rec.fail_on = "/part/2"
    f = tmp_path / "big.bin"
    f.write_bytes(b"0123456789")

    with pytest.raises(TransferError):
        run_multipart(httpx.Client(), parse_multipart(_plan(10, 4, [1, 2, 3], base), 10, "u"), f, 10)

    assert any(h[0] == "DELETE" for h in rec.hits), "a failed multipart was left behind"


def test_abort_can_be_declined_for_a_resumable_upload(server, tmp_path: Path):
    # Aborting destroys exactly the parts a resume would skip.
    base, rec = server
    rec.fail_on = "/part/2"
    f = tmp_path / "big.bin"
    f.write_bytes(b"0123456789")

    with pytest.raises(TransferError):
        run_multipart(
            httpx.Client(),
            parse_multipart(_plan(10, 4, [1, 2, 3], base), 10, "u"),
            f,
            10,
            abort_on_failure=False,
        )

    assert not any(h[0] == "DELETE" for h in rec.hits)


def test_a_part_with_no_etag_is_an_error(server, tmp_path: Path):
    # Without it the part cannot be named at completion. The usual cause is a
    # bucket whose CORS does not expose the header.
    class NoEtag(_Recorder):
        def do_PUT(self):  # noqa: N802
            self._read()
            self.send_response(200)
            self.end_headers()

    httpd = HTTPServer(("127.0.0.1", 0), NoEtag)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        f = tmp_path / "big.bin"
        f.write_bytes(b"0123456789")
        with pytest.raises(TransferError, match="no ETag"):
            run_multipart(httpx.Client(), parse_multipart(_plan(10, 4, [1, 2, 3], base), 10, "u"), f, 10)
    finally:
        httpd.shutdown()


# ── upload() end to end ──────────────────────────────────────────────


def test_upload_authorizes_with_a_full_hash_and_registers_s3_key(server, tmp_path: Path, monkeypatch):
    base, rec = server
    content = b"hello world" * 10
    f = tmp_path / "clip.mp4"
    f.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()

    import dreamlake
    from dreamlake.api import _client as client_mod

    registered: dict = {}

    class FakeClient:
        def get_auth_me(self):
            return {"namespace": {"slug": "ns"}}

        def authorize_upload(self, namespace, project, files):
            registered["authorize"] = (namespace, project, files)
            return {
                "objects": [
                    {
                        "objectKey": f"files/proj-id/{digest}",
                        "size": len(content),
                        "transfer": {
                            "kind": "single-put",
                            "put": {"url": f"{base}/one", "headers": {}},
                        },
                    }
                ]
            }

        def register_node(self, body):
            registered["node"] = body
            return {"id": "node-1"}

    monkeypatch.setattr(dreamlake, "_get_client", lambda: FakeClient())

    dreamlake.upload(str(f), project="proj@ns")

    sent = registered["authorize"][2][0]
    assert sent["hash"] == digest
    assert len(sent["hash"]) == 64

    meta = registered["node"]["metadata"]
    assert meta["s3Key"] == f"files/proj-id/{digest}"
    assert "bssId" not in meta, "still registering a BSS id"
    assert registered["node"]["kind"] == "video"

    put = next(h for h in rec.hits if h[1] == "/one")
    assert put[2] == content


def test_upload_refuses_when_the_server_authorized_nothing(tmp_path: Path, monkeypatch):
    # An empty `objects` would read as "nothing to upload", and the node would
    # be registered for bytes that were never sent.
    f = tmp_path / "a.txt"
    f.write_bytes(b"x")

    import dreamlake

    calls: list = []

    class FakeClient:
        def get_auth_me(self):
            return {"namespace": {"slug": "ns"}}

        def authorize_upload(self, *_a, **_k):
            return {"objects": []}

        def register_node(self, body):
            calls.append(body)
            return {"id": "n"}

    monkeypatch.setattr(dreamlake, "_get_client", lambda: FakeClient())

    with pytest.raises(RuntimeError, match="authorized no upload"):
        dreamlake.upload(str(f), project="proj@ns")
    assert calls == [], "registered a node for an upload that never happened"
