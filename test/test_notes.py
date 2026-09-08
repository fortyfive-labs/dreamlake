"""
Tests for dreamlake.api.notes.

No server: a MockTransport answers the requests, so these run anywhere and
assert on what the client actually puts on the wire.

The behaviour that matters here is not "does a GET return text". It is that a
caller who reads, thinks, and writes back cannot silently destroy someone
else's work — which means the ETag has to be carried without being asked for,
the server's refusals have to arrive as errors a caller can act on differently
(retry vs. re-read vs. give up), and `force` has to be the only way past them.
"""

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dreamlake.api._client import DreamLakeClient  # noqa: E402
from dreamlake.api.notes import (  # noqa: E402
    Note,
    NoteBusy,
    NoteChanged,
    NoteNotFound,
    NoteReadOnly,
    PatchFailed,
    list_notes,
    note,
    search_notes,
    shared_with_me,
)

NOTE_ID = "507f1f77bcf86cd799439011"
BODY = "# Title\nlede\n\n## Install\nbrew install x\n\n## Usage\nrun it\n"
ETAG = '"' + "a" * 64 + '"'
ETAG2 = '"' + "b" * 64 + '"'


class Server:
    """A stand-in dreamlake-server that records what it was asked."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.body = BODY
        self.etag = ETAG
        self.status: dict[str, int] = {}

    def client(self) -> DreamLakeClient:
        return DreamLakeClient(
            dl_url="https://api.test",
            token="tok",
            transport=httpx.MockTransport(self.handle),
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        forced = self.status.get(f"{request.method} {path}") or self.status.get(request.method)
        if forced:
            return httpx.Response(forced, json=_err_for(forced, self.etag))

        if path.endswith("/notes") and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "notes": [
                        {"id": NOTE_ID, "name": "Design Doc", "slug": "design-doc", "updatedAt": "2026-01-01"},
                        {"id": "b" * 24, "name": "Other", "slug": "other", "updatedAt": "2026-01-02"},
                    ],
                    "total": 2,
                },
            )
        if path == "/me/notes/shared":
            return httpx.Response(
                200,
                json={
                    "counts": {"shared": 1},
                    "notes": [
                        {
                            "id": NOTE_ID,
                            "name": "Theirs",
                            "slug": "theirs",
                            "namespaceSlug": "alice",
                            "owner": {"slug": "alice", "name": "Alice", "avatar": None},
                        }
                    ],
                },
            )
        if path.endswith("/body") and request.method == "GET":
            return httpx.Response(
                200, json={"text": self.body, "etag": self.etag, "sizeBytes": len(self.body)}
            )
        if path.endswith("/sections") and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "etag": self.etag,
                    "sections": [
                        {"anchor": "title", "title": "Title", "level": 1, "start": 0, "end": 14},
                        {"anchor": "install", "title": "Install", "level": 2, "start": 14, "end": 45},
                    ],
                },
            )
        if "/sections/" in path and request.method == "GET":
            anchor = path.rsplit("/", 1)[1]
            if anchor == "nope":
                return httpx.Response(404, json={"error": "no_such_section", "message": "no section"})
            return httpx.Response(
                200,
                json={
                    "anchor": anchor,
                    "title": "Install",
                    "level": 2,
                    "start": 14,
                    "end": 45,
                    "text": "## Install\nbrew install x\n\n",
                    "etag": self.etag,
                },
            )
        if request.method in ("PUT", "PATCH"):
            self.etag = ETAG2
            return httpx.Response(200, json={"etag": ETAG2, "sizeBytes": 42})
        return httpx.Response(500, json={"error": "unexpected", "message": path})


def _err_for(status: int, etag: str) -> dict:
    return {
        412: {"error": "stale", "message": "the note changed since you read it", "etag": etag},
        409: {"error": "note_busy", "message": "someone is editing this note right now"},
        422: {"error": "patch_failed", "message": "the diff does not apply"},
        403: {"error": "forbidden", "message": "read-only access to this note"},
        404: {"error": "not_found", "message": "note not found"},
        500: {"error": "boom", "message": "server on fire"},
    }[status]


@pytest.fixture
def server() -> Server:
    return Server()


@pytest.fixture
def n(server: Server) -> Note:
    return Note(NOTE_ID, namespace="acme", client=server.client())


def header(server: Server, method: str, name: str):
    for r in reversed(server.calls):
        if r.method == method:
            return r.headers.get(name)
    return None


# ── reading ──────────────────────────────────────────────────────────────────


class TestReading:
    def test_text_is_fetched_and_cached(self, n, server):
        assert n.text == BODY
        assert n.text == BODY
        # One request, not two: an agent touching .text in a loop should not
        # re-download the document each time.
        assert sum(1 for r in server.calls if r.url.path.endswith("/body")) == 1

    def test_refresh_rereads(self, n, server):
        n.text
        server.body = "# Changed\n"
        assert n.text == BODY  # still the cached copy
        assert n.refresh().text == "# Changed\n"

    def test_reading_records_the_version(self, n):
        assert n.etag is None
        n.text
        assert n.etag == ETAG

    def test_sections(self, n):
        got = n.sections()
        assert [s.anchor for s in got] == ["title", "install"]
        assert got[1].level == 2
        assert got[1].title == "Install"

    def test_read_one_section(self, n):
        assert n.read("install").startswith("## Install")

    def test_unknown_section_raises_not_found(self, n):
        with pytest.raises(NoteNotFound):
            n.read("nope")

    def test_sections_also_record_the_version(self, n):
        # So `sections()` then `write()` is safe without touching `.text`.
        n.sections()
        assert n.etag == ETAG

    def test_anchor_is_url_encoded(self, n, server):
        # Headings are user text: slashes and spaces must not rewrite the path.
        n.read("安装 / setup")
        assert "%E5%AE%89%E8%A3%85%20%2F%20setup" in str(server.calls[-1].url)


# ── writing ──────────────────────────────────────────────────────────────────


class TestWriting:
    def test_write_sends_if_match_from_the_read(self, n, server):
        n.sections()
        n.write("install", "## Install\npip install x\n")
        assert header(server, "PUT", "if-match") == ETAG

    def test_write_without_a_prior_read_is_unconditional(self, n, server):
        # There is nothing to be stale against yet. Inventing a validator would
        # make the first write fail for no reason.
        n.write("install", "x")
        assert header(server, "PUT", "if-match") is None

    def test_force_skips_the_precondition(self, n, server):
        n.text
        n.write("install", "x", force=True)
        assert header(server, "PUT", "if-match") is None

    def test_a_write_adopts_the_new_version(self, n):
        n.text
        n.write("install", "x")
        # Consecutive edits must work without a re-read in between.
        assert n.etag == ETAG2

    def test_a_write_invalidates_the_cached_text(self, n, server):
        n.text
        n.replace("# New\n")
        server.body = "# New\n"
        assert n.text == "# New\n"

    def test_replace_and_patch_hit_the_right_routes(self, n, server):
        n.replace("# A\n")
        assert server.calls[-1].method == "PUT" and server.calls[-1].url.path.endswith("/body")
        n.patch("--- a\n+++ b\n")
        assert server.calls[-1].method == "PATCH" and server.calls[-1].url.path.endswith("/body")

    def test_append_keeps_the_write_conditional(self, n, server):
        n.append("## New\ntail\n")
        # It read first, so the write carries a validator — two agents
        # appending at once cannot silently lose one of the additions.
        assert header(server, "PUT", "if-match") == ETAG
        sent = json.loads(server.calls[-1].read())["text"]
        assert sent.endswith("## New\ntail\n")
        assert "brew install x" in sent  # the existing body was kept

    def test_append_does_not_glue_onto_the_last_line(self, n, server):
        server.body = "no trailing newline"
        n.append("added\n")
        # Without the inserted newline the addition would join the last line
        # and, if that line were a heading, stop being one.
        assert json.loads(server.calls[-1].read())["text"] == "no trailing newline\nadded\n"


# ── the failures a caller has to tell apart ──────────────────────────────────


class TestErrors:
    def test_stale_write_raises_note_changed_with_the_current_etag(self, n, server):
        n.text
        server.status["PUT"] = 412
        with pytest.raises(NoteChanged) as e:
            n.replace("# Clobber\n")
        # Not transient: retrying reproduces it. The ETag is what to re-read
        # against, so it has to survive onto the exception.
        assert e.value.etag == ETAG

    def test_busy_note_raises_note_busy(self, n, server):
        server.status["PUT"] = 409
        with pytest.raises(NoteBusy):
            n.replace("# x\n")

    def test_bad_patch_raises_patch_failed(self, n, server):
        server.status["PATCH"] = 422
        with pytest.raises(PatchFailed):
            n.patch("--- a\n+++ b\n")

    def test_read_only_raises_note_read_only(self, n, server):
        server.status["PUT"] = 403
        with pytest.raises(NoteReadOnly):
            n.replace("# x\n")

    def test_missing_note_raises_not_found(self, n, server):
        server.status["GET"] = 404
        with pytest.raises(NoteNotFound):
            n.refresh()

    def test_other_failures_are_not_swallowed(self, n, server):
        from dreamlake.api.notes import NoteError

        server.status["GET"] = 500
        with pytest.raises(NoteError) as e:
            n.refresh()
        assert "500" in str(e.value)

    def test_the_error_says_which_note(self, n, server):
        server.status["GET"] = 404
        with pytest.raises(NoteNotFound) as e:
            n.refresh()
        assert NOTE_ID in str(e.value) and "acme" in str(e.value)


# ── finding notes ────────────────────────────────────────────────────────────


class TestLookup:
    def test_open_by_namespace_and_slug(self, server):
        n = note("acme/design-doc", client=server.client())
        assert n.id == NOTE_ID and n.namespace == "acme"

    def test_open_by_namespace_and_id_skips_the_listing(self, server):
        n = note(f"acme/{NOTE_ID}", client=server.client())
        assert n.id == NOTE_ID
        # An id is unambiguous; listing to confirm it would be a wasted request
        # on every single call.
        assert server.calls == []

    def test_a_bare_reference_is_rejected_with_a_useful_message(self, server):
        # "my-note" cannot be resolved: a note id does not say whose namespace
        # it is in. Guessing would open somebody else's document.
        with pytest.raises(ValueError) as e:
            note("design-doc", client=server.client())
        assert "namespace" in str(e.value)

    def test_unknown_slug_raises_not_found(self, server):
        with pytest.raises(NoteNotFound):
            note("acme/no-such-note", client=server.client())

    def test_list_notes(self, server):
        got = list_notes("acme", client=server.client())
        assert [r.slug for r in got] == ["design-doc", "other"]
        assert got[0].namespace == "acme"

    def test_search_passes_the_query_through(self, server):
        search_notes("design", namespace="acme", client=server.client())
        assert server.calls[-1].url.params["q"] == "design"

    def test_shared_with_me_carries_the_owner(self, server):
        got = shared_with_me(client=server.client())
        assert got[0].namespace == "alice"
        assert got[0].shared_by == "Alice"

    def test_a_ref_opens(self, server):
        c = server.client()
        ref = list_notes("acme", client=c)[0]
        assert ref.open(client=c).id == NOTE_ID


class TestTransport:
    def test_the_token_is_sent(self, n, server):
        n.text
        assert server.calls[-1].headers["authorization"] == "Bearer tok"

    def test_requests_go_to_the_configured_server(self, n, server):
        n.text
        assert str(server.calls[-1].url).startswith("https://api.test/")
