"""note.files — the client half.

The server owns the rules and tests them. What is tested here is what only this
layer can get wrong: that bytes stay bytes across the whole round trip, that a
text operation on a binary is refused before it reaches the network, and that a
download cannot quietly replace a local file.

That last one matters more than it looks. Everything else here is recoverable
from the trash; overwriting something on the caller's disk is not.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from dreamlake.api._files import (
    FileExists,
    NoteFile,
    NoteFiles,
    NotPreviewable,
    NotTextFile,
)
from dreamlake.api.notes import Note

# Every byte value, including NUL and sequences that are not valid UTF-8. If
# anything on the path decodes, a round trip of this comes back different.
BLOB = bytes(range(256))


class FakeHttp:
    def __init__(self, server: "FakeServer") -> None:
        self._s = server

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _go(self, method, url, **kw):
        self._s.calls.append((method, url, kw))
        return self._s.handle(method, url, kw)

    def get(self, url, params=None, **kw):
        return self._go("GET", url, params=params, **kw)

    def post(self, url, json=None, headers=None, **kw):
        return self._go("POST", url, json=json, headers=headers, **kw)

    def put(self, url, params=None, content=None, headers=None, **kw):
        return self._go("PUT", url, params=params, content=content, headers=headers, **kw)

    def patch(self, url, json=None, headers=None, **kw):
        return self._go("PATCH", url, json=json, headers=headers, **kw)

    def delete(self, url, params=None, headers=None, **kw):
        return self._go("DELETE", url, params=params, headers=headers, **kw)


class FakeServer:
    """Enough of the file API to exercise the client, storing bytes as bytes."""

    remote = "https://example.test"

    preview: dict | None = None
    preview_status: int = 200

    def __init__(self) -> None:
        self.calls: list = []
        self.blobs: dict[str, bytes] = {}
        self.rows: dict[str, dict] = {}
        self._n = 0

    def http(self):
        return FakeHttp(self)

    # -- helpers ---------------------------------------------------------
    def _row(self, path, content_type, data):
        self._n += 1
        fid = f"6511111111111111111111{self._n:02d}"
        row = {
            "id": fid, "path": path, "contentType": content_type,
            "sizeBytes": len(data), "etag": f'"{"a" * 63}{self._n}"',
            "createdBy": "u", "createdAt": "2026-01-01T00:00:00.000Z",
            "updatedAt": "2026-01-01T00:00:00.000Z", "trashed": False,
            "previewKind": "html" if content_type == "text/html" else None,
            "text": content_type.startswith("text/") or content_type == "application/json",
        }
        self.rows[fid] = row
        self.blobs[fid] = data
        return row

    def _resp(self, url, status, body=None, raw=None):
        req = httpx.Request("GET", url)
        if raw is not None:
            return httpx.Response(status, content=raw, request=req)
        return httpx.Response(status, json=body if body is not None else {}, request=req)

    # -- routing ---------------------------------------------------------
    def handle(self, method, url, kw):
        if url.endswith("/files") and method == "GET":
            rows = [r for r in self.rows.values() if not r["trashed"]]
            return self._resp(url, 200, {"files": rows, "total": len(rows)})

        if url.endswith("/files") and method == "POST":
            body = kw["json"]
            return self._resp(
                url, 201,
                self._row(body["path"], body.get("contentType", "text/plain"),
                          body["text"].encode()),
            )

        if url.endswith("/files/content") and method == "PUT":
            params = kw["params"]
            return self._resp(
                url, 201,
                self._row(params["path"],
                          params.get("contentType", "application/octet-stream"),
                          kw["content"]),
            )

        if url.endswith("/content") and method == "GET":
            fid = url.split("/files/")[1].split("/")[0]
            return self._resp(url, 200, raw=self.blobs[fid])

        if url.endswith("/text") and method == "GET":
            fid = url.split("/files/")[1].split("/")[0]
            row = self.rows[fid]
            if not row["text"]:
                return self._resp(url, 400, {"error": "not_text", "message": "not text"})
            return self._resp(url, 200, {"text": self.blobs[fid].decode(), "etag": row["etag"]})

        if method == "PATCH":
            fid = url.split("/files/")[1]
            self.rows[fid]["path"] = kw["json"]["path"]
            return self._resp(url, 200, self.rows[fid])

        if url.endswith("/restore"):
            fid = url.split("/files/")[1].split("/")[0]
            self.rows[fid]["trashed"] = False
            return self._resp(url, 200, self.rows[fid])

        if url.endswith("/preview") and method == "GET":
            return self._resp(url, self.preview_status, self.preview or {})
        if url.endswith("/preview") and method == "DELETE":
            return self._resp(url, 200, {"id": "f", "shared": False})

        if method == "DELETE":
            fid = url.split("/files/")[1]
            self.rows[fid]["trashed"] = True
            return self._resp(url, 200, {"id": fid, "purged": False})

        if method == "GET":
            fid = url.split("/files/")[1]
            return self._resp(url, 200, self.rows[fid])

        raise AssertionError(f"unhandled {method} {url}")


def note_with(server: FakeServer) -> Note:
    return Note("651111111111111111111111", namespace="ns", client=server)


# ── bytes stay bytes ─────────────────────────────────────────────────────────

def test_upload_and_download_round_trip_every_byte(tmp_path: Path):
    src = tmp_path / "blob.bin"
    src.write_bytes(BLOB)

    note = note_with(FakeServer())
    f = note.files.upload(src, path="assets/blob.bin")
    assert f.size_bytes == 256

    out = f.download(tmp_path / "back.bin")
    assert out.read_bytes() == BLOB


def test_upload_sends_the_body_without_encoding_it():
    s = FakeServer()
    note = note_with(s)
    note.files.create("a.bin", data=BLOB)
    method, url, kw = s.calls[-1]
    assert method == "PUT"
    # `content=`, not `json=` or `data=`: the other two would encode it.
    assert kw["content"] == BLOB


def test_download_into_a_directory_uses_the_file_name(tmp_path: Path):
    note = note_with(FakeServer())
    f = note.files.create("assets/diagram.png", data=BLOB)
    out = f.download(tmp_path)
    assert out.name == "diagram.png"
    assert out.read_bytes() == BLOB


def test_download_creates_missing_parent_directories(tmp_path: Path):
    note = note_with(FakeServer())
    f = note.files.create("a.bin", data=b"x")
    out = f.download(tmp_path / "deep" / "deeper" / "a.bin")
    assert out.read_bytes() == b"x"


def test_download_refuses_to_replace_a_local_file_unless_told(tmp_path: Path):
    # The one operation here that destroys something DreamLake cannot restore.
    dest = tmp_path / "precious.txt"
    dest.write_text("do not lose me")

    note = note_with(FakeServer())
    f = note.files.create("a.txt", text="new")

    with pytest.raises(FileExists):
        f.download(dest)
    assert dest.read_text() == "do not lose me"

    f.download(dest, overwrite=True)
    assert dest.read_text() == "new"


# ── text operations refuse binaries ──────────────────────────────────────────

def test_reading_a_binary_as_text_is_refused_before_any_request():
    # Locally, from the metadata the server already sent. A round trip to be
    # told no is a round trip the caller did not need.
    s = FakeServer()
    note = note_with(s)
    f = note.files.create("a.bin", data=BLOB)
    before = len(s.calls)

    with pytest.raises(NotTextFile, match="corrupts them"):
        f.text()
    assert len(s.calls) == before


def test_a_server_side_text_refusal_is_the_same_error_type():
    # The client's own check and the server's must not be two different
    # failures for one mistake.
    s = FakeServer()
    note = note_with(s)
    f = note.files.create("a.bin", data=BLOB)
    with pytest.raises(NotTextFile):
        note.files.read_text(f.id)


def test_text_files_read_back_as_text():
    note = note_with(FakeServer())
    f = note.files.create("a.md", text="# hi\n", content_type="text/markdown")
    assert f.is_text is True
    assert f.text() == "# hi\n"


# ── the collection ───────────────────────────────────────────────────────────

def test_create_requires_exactly_one_of_text_or_data():
    note = note_with(FakeServer())
    with pytest.raises(ValueError, match="exactly one"):
        note.files.create("a.txt")
    with pytest.raises(ValueError, match="exactly one"):
        note.files.create("a.txt", text="x", data=b"x")


def test_overwrite_is_off_by_default_and_reaches_the_server():
    # "create" that silently replaces is how a caller loses a file it did not
    # know was there, so the default has to travel.
    s = FakeServer()
    note_with(s).files.create("a.txt", text="x")
    assert s.calls[-1][2]["json"]["overwrite"] is False

    note_with(s).files.create("a.txt", text="x", overwrite=True)
    assert s.calls[-1][2]["json"]["overwrite"] is True


def test_glob_and_trashed_reach_the_server_only_when_asked():
    s = FakeServer()
    note_with(s).files.list()
    assert "glob" not in s.calls[-1][2]["params"]
    assert "trashed" not in s.calls[-1][2]["params"]

    note_with(s).files.list("assets/*.png", trashed=True)
    params = s.calls[-1][2]["params"]
    assert params["glob"] == "assets/*.png"
    assert params["trashed"] == "true"


def test_a_file_keeps_its_id_across_a_move():
    # The id is what a reference held elsewhere resolves through.
    note = note_with(FakeServer())
    f = note.files.create("from.txt", text="x")
    moved = f.move("to.txt")
    assert moved.id == f.id
    assert moved.path == "to.txt"


def test_if_match_is_sent_as_a_header_when_given():
    s = FakeServer()
    note = note_with(s)
    f = note.files.create("a.txt", text="x")
    f.move("b.txt", if_match=f.etag)
    assert s.calls[-1][2]["headers"]["If-Match"] == f.etag


def test_no_precondition_header_when_none_was_asked_for():
    # An If-Match the caller did not write would make every unconditional
    # write start failing the moment anything else touched the file.
    s = FakeServer()
    note = note_with(s)
    note.files.create("a.txt", text="x").move("b.txt")
    assert not s.calls[-1][2]["headers"]


def test_trash_then_restore_round_trip():
    note = note_with(FakeServer())
    f = note.files.create("a.txt", text="x")
    assert f.trash().trashed is True
    assert f.restore().trashed is False


def test_purge_asks_for_it_explicitly():
    s = FakeServer()
    note = note_with(s)
    note.files.create("a.txt", text="x").purge()
    assert s.calls[-1][2]["params"]["purge"] == "true"

    note.files.create("b.txt", text="x").trash()
    assert s.calls[-2][2]["params"]["purge"] == "false"


def test_find_returns_none_rather_than_raising_for_a_missing_path():
    note = note_with(FakeServer())
    note.files.create("there.txt", text="x")
    assert note.files.find("there.txt") is not None
    assert note.files.find("nowhere.txt") is None


def test_a_detached_file_says_so_instead_of_failing_obscurely():
    orphan = NoteFile(
        id="x", path="a", content_type="text/plain", size_bytes=0, etag="",
        created_by="", created_at="", updated_at="", trashed=False,
        preview_kind=None, is_text=True,
    )
    with pytest.raises(RuntimeError, match="detached"):
        orphan.bytes()


def test_the_files_url_is_built_from_the_note_s_own_base():
    # Not a second construction of it: escaping a namespace slug twice, two
    # different ways, is how one of them addresses a different namespace.
    note = Note("651111111111111111111111", namespace="with space", client=FakeServer())
    assert note.files._base() == f"{note._base}/files"


def test_preview_kind_travels_so_a_caller_knows_what_can_be_rendered():
    note = note_with(FakeServer())
    page = note.files.create("page.html", text="<b>hi</b>", content_type="text/html")
    assert page.preview_kind == "html"
    assert note.files.create("a.bin", data=BLOB).preview_kind is None


# ── Preview links ────────────────────────────────────────────────────────────

def test_preview_url_defaults_to_the_session_link():
    # The default grants nothing: it opens a page that reads with the caller's
    # own login, so the note's permissions still decide.
    s = FakeServer()
    s.preview = {"url": "https://dreamlake.ai/preview/note-file?ns=me&note=n&file=f",
                 "kind": "session", "previewKind": "html"}
    note = note_with(s)
    f = note.files.create("a.html", text="<p>x</p>", content_type="text/html")
    assert "preview/note-file" in f.preview_url()
    assert s.calls[-1][2]["params"] == {}


def test_preview_url_share_asks_for_the_shared_kind():
    s = FakeServer()
    s.preview = {"url": "https://dreamlake.ai/preview/note-file?t=tok",
                 "kind": "shared", "previewKind": "html"}
    note = note_with(s)
    f = note.files.create("a.html", text="<p>x</p>", content_type="text/html")
    url = f.preview_url(share=True)
    assert s.calls[-1][2]["params"] == {"share": "true"}
    assert "t=tok" in url


def test_a_shared_link_carries_no_expiry():
    # The correction this replaced: a link that expires on its own may be dead
    # before the person you sent it to opens it.
    s = FakeServer()
    s.preview = {"url": "https://dreamlake.ai/preview/note-file?t=tok", "kind": "shared"}
    note = note_with(s)
    f = note.files.create("a.html", text="<p>x</p>", content_type="text/html")
    from urllib.parse import urlparse, parse_qs

    q = parse_qs(urlparse(f.preview_url(share=True)).query)
    assert set(q) == {"t"}


def test_a_file_with_no_rendered_form_says_so():
    # Not a link to a page that would only offer a download — "why is this
    # blank" is a worse answer than "there is nothing to look at".
    s = FakeServer()
    s.preview_status = 400
    s.preview = {"error": "not_previewable", "message": "a.zip is application/zip"}
    note = note_with(s)
    f = note.files.create("a.zip", data=b"PK")
    with pytest.raises(NotPreviewable, match="application/zip"):
        f.preview_url()


def test_unshare_withdraws_the_link():
    s = FakeServer()
    s.preview = {"url": "x", "kind": "shared"}
    note = note_with(s)
    f = note.files.create("a.html", text="<p>x</p>", content_type="text/html")
    f.unshare()
    method, url, _ = s.calls[-1]
    assert method == "DELETE"
    assert url.endswith("/preview")
