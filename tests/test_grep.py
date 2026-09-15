"""grep_notes: the cross-note search, and what it says about what it missed.

The server owns the matching rules; those are tested there. What is tested
here is the client's half — that pages are followed so `limit` means hits,
that the fields warning about a bounded answer survive the round trip, and
that a hit can be turned into an edit without re-reading the note.
"""

from __future__ import annotations

import httpx
import pytest

from dreamlake.api.notes import GrepResult, Hit, grep_notes


class FakeHttp:
    def __init__(self, pages, captured):
        self._pages = pages
        self._captured = captured

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None):
        self._captured.append(dict(params or {}))
        page = self._pages.pop(0)
        return httpx.Response(200, json=page, request=httpx.Request("GET", url))


class FakeClient:
    remote = "https://example.test"

    def __init__(self, pages):
        self.pages = list(pages)
        self.captured: list[dict] = []

    def http(self):
        return FakeHttp(self.pages, self.captured)


def hit_row(line: int, **over):
    row = {
        "noteId": "651111111111111111111111",
        "noteSlug": "design-doc",
        "noteName": "Design Doc",
        "etag": '"' + "a" * 64 + '"',
        "line": line,
        "column": 1,
        "ind": [10, 15],
        "text": f"line {line} text",
        "match": "Draft",
        "before": [],
        "after": [],
        "anchor": "plan",
    }
    row.update(over)
    return row


def page(hits, cursor=None, **over):
    p = {
        "hits": hits,
        "nextCursor": cursor,
        "truncated": False,
        "notesSearched": 1,
        "partialNotes": [],
        "staleNotes": 0,
    }
    p.update(over)
    return p


def test_returns_hits_with_every_coordinate():
    c = FakeClient([page([hit_row(3)])])
    res = grep_notes("Draft", namespace="me", client=c)

    assert isinstance(res, GrepResult)
    assert len(res) == 1
    h = res.hits[0]
    assert isinstance(h, Hit)
    assert (h.line, h.column, h.ind) == (3, 1, (10, 15))
    assert h.note_slug == "design-doc"
    # The revision the offsets belong to. Without it the caller has no
    # precondition for the write and has to re-read the note — the work this
    # call exists to remove.
    assert h.etag.startswith('"')


def test_a_hit_knows_which_note_to_open():
    c = FakeClient([page([hit_row(1)])])
    note = grep_notes("Draft", namespace="me", client=c).hits[0].open(client=c)
    assert note._id == "651111111111111111111111"
    assert note._ns == "me"


def test_literal_is_the_default_and_nothing_is_sent_that_was_not_asked_for():
    c = FakeClient([page([])])
    grep_notes("v1.2", namespace="me", client=c)
    sent = c.captured[0]
    assert sent["q"] == "v1.2"
    # Absent, not false: sending caseSensitive=false would override the
    # server's regex default for anyone who later adds regex= to the same call.
    assert "caseSensitive" not in sent
    assert "regex" not in sent


def test_regex_and_flags_are_passed_through_explicitly():
    c = FakeClient([page([])])
    grep_notes(namespace="me", regex=r"\bTODO\b", flags="im", case_sensitive=True, client=c)
    sent = c.captured[0]
    assert sent["regex"] == r"\bTODO\b"
    assert sent["flags"] == "im"
    assert sent["caseSensitive"] == "true"
    assert "q" not in sent


def test_filters_are_sent_in_the_shape_the_route_expects():
    c = FakeClient([page([])])
    grep_notes("x", namespace="me", glob="spec-*", notes=["a", "b"], context=2, client=c)
    sent = c.captured[0]
    assert sent["glob"] == "spec-*"
    assert sent["note"] == "a,b"
    assert sent["context"] == 2


def test_limit_counts_hits_and_pages_are_followed_to_reach_it():
    # A caller asking for 4 hits means 4 hits. Where the server chose to end a
    # page is not something they can see, so it must not decide the answer.
    c = FakeClient([
        page([hit_row(1), hit_row(2)], cursor="c1"),
        page([hit_row(3), hit_row(4)], cursor="c2"),
    ])
    res = grep_notes("Draft", namespace="me", limit=4, client=c)
    assert [h.line for h in res] == [1, 2, 3, 4]
    assert c.captured[1]["cursor"] == "c1"


def test_stops_at_the_end_even_when_the_limit_is_not_reached():
    c = FakeClient([page([hit_row(1)], cursor=None)])
    res = grep_notes("Draft", namespace="me", limit=50, client=c)
    assert len(res) == 1
    assert res.next_cursor is None


def test_never_asks_for_more_than_the_remaining_limit():
    # Otherwise the last page overshoots and the caller is handed more hits
    # than they asked for, which breaks anyone sizing a buffer from `limit`.
    c = FakeClient([page([hit_row(1)], cursor="c1"), page([hit_row(2)])])
    grep_notes("Draft", namespace="me", limit=2, client=c)
    assert c.captured[0]["limit"] == 2
    assert c.captured[1]["limit"] == 1


def test_warnings_about_an_incomplete_answer_survive_paging():
    # `truncated` on any page means the whole answer is incomplete. Taking the
    # last page's value would let an early warning be forgotten, and a caller
    # who then replaces "all of them" misses some.
    c = FakeClient([
        page([hit_row(1)], cursor="c1", truncated=True, partialNotes=["n1"], staleNotes=2),
        page([hit_row(2)], truncated=False, partialNotes=["n2"], staleNotes=0),
    ])
    res = grep_notes("Draft", namespace="me", limit=10, client=c)
    assert res.truncated is True
    assert res.partial_notes == ("n1", "n2")
    assert res.stale_notes == 2


def test_notes_searched_accumulates_across_pages():
    c = FakeClient([
        page([hit_row(1)], cursor="c1", notesSearched=3),
        page([hit_row(2)], notesSearched=4),
    ])
    assert grep_notes("Draft", namespace="me", limit=10, client=c).notes_searched == 7


def test_partial_notes_are_not_repeated_when_a_note_spans_pages():
    c = FakeClient([
        page([hit_row(1)], cursor="c1", partialNotes=["n1"]),
        page([hit_row(2)], partialNotes=["n1"]),
    ])
    assert grep_notes("Draft", namespace="me", limit=10, client=c).partial_notes == ("n1",)


def test_an_error_is_raised_rather_than_returned_as_no_hits():
    # "No hits" and "your query was rejected" must not look the same; a caller
    # would conclude the phrase is nowhere in the namespace.
    class Failing(FakeClient):
        def http(self):
            class H:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *a):
                    return False

                def get(self_inner, url, params=None):
                    return httpx.Response(
                        400,
                        json={"error": "bad_request", "message": "invalid regex"},
                        request=httpx.Request("GET", url),
                    )

            return H()

    with pytest.raises(Exception, match="invalid regex"):
        grep_notes(namespace="me", regex="(unclosed", client=Failing([]))
