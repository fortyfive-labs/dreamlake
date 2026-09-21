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
import re
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dreamlake.api._client import DreamLakeClient  # noqa: E402
from dreamlake.api.notes import (  # noqa: E402
    Note,
    create_note,
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

        if path.endswith("/notes") and request.method == "POST":
            return httpx.Response(201, json={"note": {"id": NOTE_ID, "name": "New", "slug": "new"}})
        if path.endswith("/sections") and request.method == "POST":
            self.etag = ETAG2
            return httpx.Response(
                201,
                json={
                    "etag": ETAG2,
                    "sizeBytes": 40,
                    "sections": [
                        {"anchor": "title", "title": "Title", "level": 1, "start": 0, "end": 14},
                        {"anchor": "added", "title": "Added", "level": 2, "start": 14, "end": 30},
                    ],
                },
            )
        if "/sections/" in path and request.method == "DELETE":
            self.etag = ETAG2
            return httpx.Response(200, json={"etag": ETAG2, "sizeBytes": 10})
        if path.endswith("/notes") and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "notes": [
                        {
                            "id": NOTE_ID,
                            "name": "Design Doc",
                            "slug": "design-doc",
                            "updatedAt": "2026-01-01",
                            "matches": [
                                {"anchor": "install", "title": "Install", "snippet": "…pip install x…"}
                            ],
                        },
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
        assert n.read_section("install").startswith("## Install")

    def test_unknown_section_raises_not_found(self, n):
        with pytest.raises(NoteNotFound):
            n.read_section("nope")

    def test_sections_also_record_the_version(self, n):
        # So `sections()` then `write()` is safe without touching `.text`.
        n.sections()
        assert n.etag == ETAG

    def test_anchor_is_url_encoded(self, n, server):
        # Headings are user text: slashes and spaces must not rewrite the path.
        n.read_section("安装 / setup")
        assert "%E5%AE%89%E8%A3%85%20%2F%20setup" in str(server.calls[-1].url)


# ── writing ──────────────────────────────────────────────────────────────────


class TestWriting:
    def test_write_sends_if_match_from_the_read(self, n, server):
        n.sections()
        n.write_section("install", "## Install\npip install x\n")
        assert header(server, "PUT", "if-match") == ETAG

    def test_write_without_a_prior_read_is_unconditional(self, n, server):
        # There is nothing to be stale against yet. Inventing a validator would
        # make the first write fail for no reason.
        n.write_section("install", "x")
        assert header(server, "PUT", "if-match") is None

    def test_force_skips_the_precondition(self, n, server):
        n.text
        n.write_section("install", "x", force=True)
        assert header(server, "PUT", "if-match") is None

    def test_a_write_adopts_the_new_version(self, n):
        n.text
        n.write_section("install", "x")
        # Consecutive edits must work without a re-read in between.
        assert n.etag == ETAG2

    def test_a_write_invalidates_the_cached_text(self, n, server):
        n.text
        n.write("# New\n")
        server.body = "# New\n"
        assert n.text == "# New\n"

    def test_write_and_patch_hit_the_right_routes(self, n, server):
        n.write("# A\n")
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
            n.write("# Clobber\n")
        # Not transient: retrying reproduces it. The ETag is what to re-read
        # against, so it has to survive onto the exception.
        assert e.value.etag == ETAG

    def test_busy_note_raises_note_busy(self, n, server):
        server.status["PUT"] = 409
        with pytest.raises(NoteBusy):
            n.write("# x\n")

    def test_bad_patch_raises_patch_failed(self, n, server):
        server.status["PATCH"] = 422
        with pytest.raises(PatchFailed):
            n.patch("--- a\n+++ b\n")

    def test_read_only_raises_note_read_only(self, n, server):
        server.status["PUT"] = 403
        with pytest.raises(NoteReadOnly):
            n.write("# x\n")

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


class TestCreate:
    def test_creates_and_returns_an_editable_note(self, server):
        n = create_note("acme", "New", client=server.client())
        assert n.id == NOTE_ID and n.namespace == "acme"
        created = server.calls[0]
        assert created.method == "POST"
        assert created.url.path == "/namespaces/acme/notes"
        assert json.loads(created.read())["name"] == "New"

    def test_writes_the_initial_body_unconditionally(self, server):
        # The note is one request old; there is nothing for anyone to have
        # changed, and a validator would only be able to fail spuriously.
        create_note("acme", "New", text="# New\n", client=server.client())
        put = [c for c in server.calls if c.method == "PUT"][-1]
        assert json.loads(put.read())["text"] == "# New\n"
        assert put.headers.get("if-match") is None

    def test_skips_the_write_when_there_is_no_body(self, server):
        create_note("acme", "Empty", client=server.client())
        assert not [c for c in server.calls if c.method == "PUT"]

    def test_passes_visibility_through(self, server):
        create_note("acme", "Open", visibility="public", client=server.client())
        assert json.loads(server.calls[0].read())["visibility"] == "public"


class TestSectionsInsertDelete:
    def test_insert_at_the_end_by_default(self, n, server):
        out = n.insert_section("## Added\nbody\n")
        assert [s.anchor for s in out] == ["title", "added"]
        sent = json.loads(server.calls[-1].read())
        assert sent == {"text": "## Added\nbody\n"}

    def test_insert_after_a_named_section(self, n, server):
        n.insert_section("## Added\n", after="install")
        assert json.loads(server.calls[-1].read())["after"] == "install"

    def test_insert_before_a_named_section(self, n, server):
        n.insert_section("## Added\n", before="install")
        assert json.loads(server.calls[-1].read())["before"] == "install"

    def test_refuses_before_and_after_together(self, n, server):
        # Ambiguous, and the server would reject it — failing here saves the
        # round trip and says so more clearly.
        with pytest.raises(ValueError):
            n.insert_section("## X\n", before="a", after="b")

    def test_insert_returns_the_new_outline(self, n):
        # The new anchor is only knowable afterwards: a duplicate title takes
        # the next free suffix.
        out = n.insert_section("## Added\n")
        assert any(s.anchor == "added" for s in out)

    def test_insert_carries_the_validator(self, n, server):
        n.sections()
        n.insert_section("## Added\n")
        assert server.calls[-1].headers.get("if-match") == ETAG

    def test_delete_section(self, n, server):
        n.sections()
        etag = n.delete_section("install")
        assert etag == ETAG2
        assert server.calls[-1].method == "DELETE"
        assert server.calls[-1].url.path.endswith("/sections/install")
        assert server.calls[-1].headers.get("if-match") == ETAG

    def test_delete_adopts_the_new_version(self, n):
        n.sections()
        n.delete_section("install")
        assert n.etag == ETAG2

    def test_delete_force_skips_the_validator(self, n, server):
        n.sections()
        n.delete_section("install", force=True)
        assert server.calls[-1].headers.get("if-match") is None

    def test_a_missing_section_raises_not_found(self, n, server):
        server.status["DELETE"] = 404
        with pytest.raises(NoteNotFound):
            n.delete_section("nope")


class TestSearchMatches:
    def test_a_result_says_where_it_matched(self, server):
        [hit, other] = search_notes("install", namespace="acme", client=server.client())
        assert hit.matches[0].anchor == "install"
        assert "pip install x" in hit.matches[0].snippet

    def test_a_result_with_no_body_hit_has_no_matches(self, server):
        [_, other] = search_notes("install", namespace="acme", client=server.client())
        assert other.matches == ()

    def test_the_anchor_can_be_read_straight_back(self, server):
        c = server.client()
        [hit, _] = search_notes("install", namespace="acme", client=c)
        # The point of returning an anchor: go to the part, not the whole note.
        assert hit.open(client=c).read_section(hit.matches[0].anchor).startswith("## Install")


class TestTheNamingRule:
    """One rule, so a reader never has to check the signature.

    `_section` methods address stable section anchors. Whole-document methods,
    explicit line-range reads and the file accessor are separate public surfaces.
    """

    WHOLE = {"text", "refresh", "sections", "read", "write", "append", "patch", "diff"}
    RANGE = {"read_lines"}
    ACCESSORS = {"files"}
    PART = {"read_section", "write_section", "insert_section", "delete_section"}

    def test_every_public_method_is_on_one_side_of_the_rule(self):
        public = {
            m
            for m in dir(Note)
            if not m.startswith("_") and m not in {"id", "namespace", "etag"}
        }
        assert public == self.WHOLE | self.PART | self.RANGE | self.ACCESSORS

    def test_nothing_named_section_operates_on_the_whole_note(self):
        for name in self.WHOLE:
            assert "_section" not in name

    def test_every_section_method_takes_or_returns_an_anchor(self, n, server):
        # The rule has to be true of behaviour, not just spelling.
        n.sections()
        assert n.read_section("install").startswith("## Install")
        n.write_section("install", "## Install\nx\n")
        n.insert_section("## Added\n", after="install")
        n.delete_section("install")
        anchored = [c for c in server.calls if "/sections" in c.url.path]
        assert len(anchored) >= 4

    def test_whole_note_methods_never_take_an_anchor(self, n):
        import inspect

        for name in ("write", "append", "patch"):
            params = list(inspect.signature(getattr(Note, name)).parameters)
            assert "anchor" not in params, name


class TestNoExamplesFromRealAccounts:
    """Examples and error messages use placeholders, not somebody's namespace.

    A real name in a docstring reads as the value to type, and in an error
    message it reads as advice — both send a stranger at an account that is
    not theirs. It also leaks who wrote the example, which is nobody's
    business by the time this ships.
    """

    def test_the_module_uses_a_placeholder_namespace(self):
        from dreamlake.api import notes

        src = Path(notes.__file__).read_text()
        # Whatever the placeholder is, it must be visibly one.
        assert "<namespace>/" in src
        for name in ("charlie", "geyang", "yancy"):
            assert name not in src.lower(), f"{name!r} appears in notes.py"

    def test_the_bare_reference_error_suggests_a_placeholder(self, server):
        with pytest.raises(ValueError) as e:
            note("design-doc", client=server.client())
        assert "<namespace>" in str(e.value)


class TestPicksUpTheCliLogin:
    """`dreamlake login` then Python should just work.

    Both tools keep the token under ~/.dreamlake, so a user who logged in
    once should not have to also export DREAMLAKE_API_KEY. The failure this
    guards is silent: read the wrong key and the load returns None, the
    client is anonymous, and the first call comes back 401 with nothing
    pointing at the cause.
    """

    def test_the_storage_key_matches_the_one_login_writes(self):
        from pathlib import Path as _P

        client_src = _P(__file__).parent.parent / "src/dreamlake/api/_client.py"
        login_src = _P(__file__).parent.parent / "src/dreamlake/cli_commands/login.py"
        key = re.search(r'TOKEN_KEY\s*=\s*"([^"]+)"', login_src.read_text()).group(1)
        assert f'"{key}"' in client_src.read_text(), f"_client.py must load {key!r}"

    def test_an_explicit_token_still_wins(self, server):
        c = DreamLakeClient(dl_url="https://api.test", token="explicit", transport=None)
        assert c._token == "explicit"

    def test_the_env_var_wins_over_the_saved_login(self, monkeypatch):
        monkeypatch.setenv("DREAMLAKE_API_KEY", "from-env")
        assert DreamLakeClient()._token == "from-env"


def test_diff_uses_read_hash_without_advancing_write_validator():
    calls = []
    def handle(request):
        calls.append(request)
        if request.url.path.endswith('/diff'):
            return httpx.Response(200, json={'diff': '+new\n', 'from': ETAG, 'to': ETAG2, 'etag': ETAG2})
        return httpx.Response(200, json={'text': BODY, 'etag': ETAG})
    c = DreamLakeClient(dl_url='https://api.test', token='tok', transport=httpx.MockTransport(handle))
    n = Note(NOTE_ID, namespace='ns', client=c)
    doc = n.read()
    assert n.diff() == '+new\n'
    assert calls[-1].url.params['since'] == doc.etag
    assert n.etag == ETAG
    assert n.text == BODY
    assert n.diff(since=ETAG2, context=0) == '+new\n'
    assert calls[-1].url.params['since'] == ETAG2
    assert calls[-1].url.params['context'] == '0'


def test_diff_defaults_to_successful_write_ref_and_supports_fresh_handles():
    s = Server()
    n = Note(NOTE_ID, namespace='ns', client=s.client())
    n.write('new\n')
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={'diff': ''})
    n._client = DreamLakeClient(dl_url='https://api.test', token='tok', transport=httpx.MockTransport(handle))
    assert n.diff() == ''
    assert calls[-1].url.params['since'] == ETAG2
    fresh = Note(NOTE_ID, namespace='ns', client=n._client)
    assert fresh.diff(since=ETAG) == ''
    assert fresh.etag is None
    assert calls[-1].url.params['since'] == ETAG


def test_diff_validates_input_and_reports_unknown_refs():
    s = Server()
    n = Note(NOTE_ID, namespace='ns', client=s.client())
    with pytest.raises(ValueError, match='read the note'):
        n.diff()
    for context in [-1, 101, 1.5, True]:
        with pytest.raises(ValueError):
            n.diff(since=ETAG, context=context)
    assert not s.calls
    s.status['GET'] = 404
    with pytest.raises(NoteNotFound):
        n.diff(since=ETAG)
