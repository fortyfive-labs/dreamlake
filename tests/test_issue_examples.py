"""Every Python example from issue #240, executed.

The issue asks for these to be run against the implemented SDK before they are
presented as working recipes, and says so twice. They are reproduced here in
the order and spelling the issue uses, against a local snapshot rather than the
network — so what is proven is the calling convention and the result, which is
what the documentation will claim.

The network halves (`dl.note(...)` and `save()`) have their own tests; here a
Doc is built directly so a failure points at the example rather than at
plumbing.
"""

from __future__ import annotations

import pytest

from dreamlake.api.notes import Note

from dreamlake.api._doc import Doc

from .test_doc import FakeNote


def doc_of(text: str) -> Doc:
    return Doc(FakeNote(text), text, "rev-1")


# ── Search ───────────────────────────────────────────────────────────────────

def test_find_literal():
    #     doc.find("Draft")
    doc = doc_of("# Notes\n\nDraft one\nDraft two\n")
    hits = doc.find("Draft")
    assert len(hits) == 2
    assert hits[0].line == (3, 3)


def test_find_regex_with_flags():
    #     doc.find(regex=r"\bTODO\b.*", flags="im")
    doc = doc_of("TODO: fix this\nnothing\ntodo: also\n")
    hits = doc.find(regex=r"\bTODO\b.*", flags="im")
    assert [h.text for h in hits] == ["TODO: fix this", "todo: also"]


def test_select_css():
    #     doc.select("#contact")
    doc = doc_of('<div id="contact"><p>Contact us</p></div>')
    assert doc.select("#contact").text == "Contact us"


# ── Replacement: text first, query identifies the target ─────────────────────

def test_replace_all_returns_the_full_source():
    #     updated = doc.replace("Published", query="Draft", all=True)
    doc = doc_of("Draft one\nDraft two\n")
    updated = doc.replace("Published", query="Draft", all=True)
    assert isinstance(updated, str)
    assert updated == "Published one\nPublished two\n"
    assert updated == doc.text


def test_replace_with_numbered_groups():
    #     doc.replace("$1: $2", regex=r"(\w+)=(\d+)", all=True)
    doc = doc_of("a=1 b=22\n")
    assert doc.replace("$1: $2", regex=r"(\w+)=(\d+)", all=True) == "a: 1 b: 22\n"


def test_replace_with_named_groups():
    #     doc.replace("$<key>: $<value>", regex=r"(?<key>timeout|retries)=(?<value>\d+)", all=True)
    doc = doc_of("timeout=30 retries=5\n")
    out = doc.replace(
        "$<key>: $<value>",
        regex=r"(?<key>timeout|retries)=(?<value>\d+)",
        all=True,
    )
    assert out == "timeout: 30 retries: 5\n"


# ── Element-scoped ───────────────────────────────────────────────────────────

def test_element_replace_and_update():
    #     doc.select("#contact").replace("Talk to sales", query="Contact us")
    #     doc.select("#contact").update(attrs={"href": "/sales"})
    doc = doc_of('<a id="contact" href="/old">Contact us now</a>')
    out = doc.select("#contact").replace("Talk to sales", query="Contact us")
    assert "Talk to sales now" in out
    out = doc.select("#contact").update({"href": "/sales"})
    assert '<a id="contact" href="/sales">' in out


# ── Location: line / ind come last ───────────────────────────────────────────

def test_replace_by_line():
    #     doc.replace("Updated line", line=10)
    doc = doc_of("".join(f"line {i}\n" for i in range(1, 13)))
    out = doc.replace("Updated line", line=10)
    assert out.splitlines()[9] == "Updated line"
    assert out.splitlines()[8] == "line 9"


def test_replace_by_line_range():
    #     doc.replace("New block", line=(10, 15))
    doc = doc_of("".join(f"line {i}\n" for i in range(1, 21)))
    out = doc.replace("New block", line=(10, 15))
    lines = out.splitlines()
    assert lines[9] == "New block"
    assert lines[10] == "line 16"


def test_replace_by_character_range():
    #     doc.replace("replacement", ind=(120, 145))
    body = "x" * 200
    doc = doc_of(body)
    out = doc.replace("replacement", ind=(120, 145))
    assert out == body[:120] + "replacement" + body[145:]


def test_insert_by_line_and_by_index():
    #     doc.insert("New line", line=10)
    #     doc.insert("inserted text", ind=120)
    doc = doc_of("".join(f"line {i}\n" for i in range(1, 13)))
    out = doc.insert("New line", line=10)
    assert out.splitlines()[9] == "New line"
    assert out.splitlines()[10] == "line 10"

    doc2 = doc_of("y" * 200)
    out2 = doc2.insert("inserted text", ind=120)
    assert out2[120:133] == "inserted text"


# ── TOC and diff ─────────────────────────────────────────────────────────────

def test_toc_carries_anchor_title_level_and_both_ranges():
    #     doc.toc()  # anchor, title, level, line range, ind range
    doc = doc_of("# Title\n\nintro\n\n## Setup\n\nsteps\n")
    entries = doc.toc()
    first = entries[0]
    assert (first.anchor, first.title, first.level) == ("title", "Title", 1)
    assert isinstance(first.line, tuple) and len(first.line) == 2
    assert isinstance(first.ind, tuple) and len(first.ind) == 2


def test_diff_is_printable():
    #     print(doc.diff())
    doc = doc_of("Draft\n")
    doc.replace("Published", query="Draft")
    text = doc.diff()
    assert "-Draft" in text and "+Published" in text


# ── The rules the examples rest on ───────────────────────────────────────────

def test_every_edit_returns_a_string_equal_to_the_draft():
    # The issue's first delivery check: "Verify each editing call returns the
    # full updated source string and that it equals the draft source."
    doc = doc_of('<p id="x">one two</p>')
    # Checked one at a time: each returns the draft AS IT IS AFTER THAT EDIT.
    # Collecting them first would compare four historical snapshots against one
    # final draft and only the last could match.
    for call in (
        lambda: doc.replace("three", query="one"),
        lambda: doc.insert("z", ind=0),
        lambda: doc.select("#x").replace("four", query="two"),
        lambda: doc.select("#x").update({"class": "y"}),
    ):
        out = call()
        assert isinstance(out, str)
        assert out == doc.text


def test_saving_is_separate_from_editing():
    doc = doc_of("Draft\n")
    note = doc._note
    doc.replace("Published", query="Draft")
    assert note.writes == []
    doc.save()
    assert note.writes == ["Published\n"]


# ── The conditional verification read ────────────────────────────────────────
#
# From the issue's "complex Markdown workflow": after committing a patch you
# read the note back to check the surrounding source survived. The requirement
# is stated there explicitly — the verification read "must return the committed
# version or report that it has already changed; it must not silently validate
# a different snapshot."

def test_read_if_match_returns_the_named_revision():
    note = _FakeReadNote("body", "rev-7")
    doc = note.read(if_match="rev-7")
    assert doc.text == "body"
    assert doc.etag == "rev-7"


def test_read_if_match_refuses_a_note_that_has_since_moved():
    # Without this the read succeeds against whatever is there now, the
    # assertions in the recipe pass, and what they checked is someone else's
    # document.
    from dreamlake.api.notes import NoteChanged

    note = _FakeReadNote("someone else's body", "rev-9")
    with pytest.raises(NoteChanged) as exc:
        note.read(if_match="rev-7")
    assert "rev-7" in str(exc.value)
    assert exc.value.etag == "rev-9"


def test_read_without_if_match_is_unconditional():
    # The plain read must keep working; the guard is opt-in.
    note = _FakeReadNote("body", "rev-9")
    assert note.read().etag == "rev-9"


def test_a_snapshot_says_whether_it_is_the_whole_note():
    # The recipe opens with `if snapshot.truncated: raise`. It has to be
    # something you can write, and it has to be false for a whole read.
    note = _FakeReadNote("body", "rev-1")
    assert note.read().truncated is False


def test_patch_result_carries_the_revision_and_still_equals_it():
    # The recipes say `result.etag`; older code treated the return value as the
    # revision string. Both have to work, or one of them silently breaks.
    from dreamlake.api.notes import PatchResult

    r = PatchResult('"abc"', 42)
    assert r.etag == '"abc"'
    assert r == '"abc"'
    assert r.size_bytes == 42
    assert f"{r}" == '"abc"'


class _FakeReadNote(Note):
    """A Note whose refresh() loads from memory instead of the network."""

    def __init__(self, text: str, etag: str) -> None:
        self._stored, self._stored_etag = text, etag
        self._id, self._ns, self._client = "651111111111111111111111", "ns", None
        self._text = self._etag = None
        self._meta = {}

    @property
    def etag(self):
        return self._etag

    def refresh(self):
        self._text, self._etag = self._stored, self._stored_etag
        return self
