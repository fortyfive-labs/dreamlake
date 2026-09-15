"""The snapshot API: read once, edit locally, save one patch.

A fake Note stands in for the network so these test the contract rather than
httpx. What the fake does record is how many times the network was touched,
because "edits are local" is a promise an agent relies on — a method that
secretly refetches would make every measurement in a session lie.
"""

from __future__ import annotations

import pytest

from dreamlake.api._doc import Doc
from dreamlake.api._editing import AmbiguousMatch, EditError, NoMatch
from dreamlake.api._html import AmbiguousElement, NoElement

MD = "# Title\n\nDraft one\nDraft two\n"
HTML = (
    '<div id="contact" class="card">\n'
    '  <p>Contact us at <a href="/sales">sales</a> &amp; more</p>\n'
    '</div>\n'
)


class FakeNote:
    """A Note that never leaves the process, and counts what it was asked to do."""

    def __init__(self, text: str, etag: str = "rev-1") -> None:
        self.id = "651111111111111111111111"
        self.namespace = "ns"
        self._text = text
        self.etag = etag
        self.writes: list[str] = []
        self.preconditions: list[str | None] = []
        self.stale = False

    def write(self, text: str, *, if_match: str | None = None, force: bool = False) -> str:
        # The precondition is recorded, because "save sends the revision the
        # DRAFT was read at" is a claim only visible here — a fake that
        # accepted any if_match would pass whether or not one was sent.
        self.preconditions.append(if_match)
        if self.stale and not force:
            from dreamlake.api.notes import NoteChanged

            raise NoteChanged("the note changed since it was read", etag="rev-2")
        self.writes.append(text)
        self._text = text
        self.etag = f"rev-{len(self.writes) + 1}"
        return self.etag


def doc_of(text: str = MD) -> tuple[Doc, FakeNote]:
    note = FakeNote(text)
    return Doc(note, text, note.etag), note


# ── The shape of a session ───────────────────────────────────────────────────

def test_edits_are_local_until_save():
    doc, note = doc_of()
    doc.replace("Published", query="Draft", all=True)
    assert note.writes == []
    assert doc.dirty
    doc.save()
    assert note.writes == ["# Title\n\nPublished one\nPublished two\n"]


def test_every_edit_returns_the_whole_updated_source():
    # Not a count, not a handle. An agent asked for one change and can check
    # the entire result, including what it did not mean to touch.
    doc, _ = doc_of()
    out = doc.replace("X", line=3)
    assert out == doc.text
    assert out.startswith("# Title\n\nX\n")


def test_each_edit_sees_the_previous_one():
    doc, _ = doc_of()
    doc.replace("Published", query="Draft", all=True)
    doc.replace("Final", query="Published", all=True)
    assert "Final one" in doc.text
    assert "Draft" not in doc.text


def test_toc_and_find_address_the_current_draft():
    doc, _ = doc_of("# A\n\nx\n")
    doc.insert("## B\n\ny\n", line=4)
    assert [e.anchor for e in doc.toc()] == ["a", "b"]


def test_revert_returns_to_the_source_as_read():
    doc, note = doc_of()
    doc.replace("X", line=3)
    assert doc.dirty
    doc.revert()
    assert not doc.dirty
    assert doc.text == MD
    doc.save()
    assert note.writes == []  # nothing to commit


def test_diff_describes_the_change_and_is_empty_when_there_is_none():
    doc, _ = doc_of()
    assert doc.diff() == ""
    doc.replace("Published", query="Draft one")
    d = doc.diff()
    assert "-Draft one" in d and "+Published" in d


# ── Saving ───────────────────────────────────────────────────────────────────

def test_save_reports_the_new_revision():
    doc, _ = doc_of()
    doc.replace("X", line=3)
    result = doc.save()
    assert result.etag == "rev-2"
    assert doc.etag == "rev-2"
    assert not doc.dirty


def test_saving_an_unchanged_draft_writes_nothing():
    # So a caller can save unconditionally without first checking `dirty`.
    doc, note = doc_of()
    assert doc.save().etag == "rev-1"
    assert note.writes == []


def test_a_stale_document_is_refused_rather_than_retried():
    # The edits were computed against text that is no longer there. Writing
    # them anyway is how one agent silently reverts another.
    from dreamlake.api.notes import NoteChanged

    doc, note = doc_of()
    doc.replace("X", line=3)
    note.stale = True
    with pytest.raises(NoteChanged):
        doc.save()
    assert note.writes == []
    assert doc.dirty  # the draft survives, so the caller can re-read and redo


def test_force_is_available_but_never_automatic():
    doc, note = doc_of()
    doc.replace("X", line=3)
    note.stale = True
    doc.save(force=True)
    assert len(note.writes) == 1


# ── Refusals carry through ───────────────────────────────────────────────────

def test_ambiguous_and_missing_matches_reach_the_caller():
    doc, _ = doc_of()
    with pytest.raises(AmbiguousMatch):
        doc.replace("X", query="Draft")
    with pytest.raises(NoMatch):
        doc.replace("X", query="nowhere")
    # ...and the draft is untouched by a refused edit.
    assert doc.text == MD


def test_conflicting_targets_are_refused():
    doc, _ = doc_of()
    with pytest.raises(EditError):
        doc.replace("X", query="Draft", line=3)


# ── HTML ─────────────────────────────────────────────────────────────────────

def test_select_requires_exactly_one_element():
    doc, _ = doc_of(HTML)
    assert doc.select("#contact").text.strip().startswith("Contact us at")
    with pytest.raises(NoElement):
        doc.select("#nope")

    two = Doc(FakeNote("<p>a</p><p>b</p>"), "<p>a</p><p>b</p>", "rev-1")
    with pytest.raises(AmbiguousElement):
        two.select("p")


def test_element_text_replacement_leaves_the_markup_alone():
    doc, _ = doc_of(HTML)
    out = doc.select("#contact").replace("Talk to sales", query="Contact us")
    assert "Talk to sales at" in out
    # The entity keeps its spelling and the link keeps its quoting: an edit
    # that reserialised the document would have changed both.
    assert "&amp; more" in out
    assert '<a href="/sales">' in out


def test_element_edits_return_the_whole_document():
    doc, _ = doc_of(HTML)
    out = doc.select("#contact").replace("Talk to sales", query="Contact us")
    assert out == doc.text
    assert out.startswith('<div id="contact"')


def test_element_text_search_does_not_reach_tags_or_attributes():
    doc, _ = doc_of('<p class="sales">body</p>')
    with pytest.raises(NoMatch):
        doc.select("p").replace("X", query="sales")


def test_replaced_text_is_escaped():
    doc, _ = doc_of("<p>body</p>")
    out = doc.select("p").replace("a & b <tag>", query="body")
    assert "a &amp; b &lt;tag&gt;" in out


def test_update_touches_only_the_named_attributes():
    doc, _ = doc_of(HTML)
    out = doc.select("a").update({"href": "/enterprise"})
    assert '<a href="/enterprise">' in out
    # Everything else in the document is untouched.
    assert out.replace("/enterprise", "/sales") == HTML


def test_update_can_add_and_remove():
    doc, _ = doc_of('<a href="/x">t</a>')
    doc.select("a").update({"rel": "nofollow"})
    assert 'rel="nofollow"' in doc.text
    doc.select("a").update({"rel": None})
    assert "rel=" not in doc.text


def test_selector_only_replace_sets_the_contents():
    doc, _ = doc_of("<p>old</p>")
    assert doc.select("p").replace("new") == "<p>new</p>"


def test_an_element_handle_notices_when_its_target_is_gone():
    # Reusing stale offsets would write into whatever now occupies them, which
    # nothing downstream could detect.
    doc, _ = doc_of('<div id="a">x</div><div id="b">y</div>')
    handle = doc.select("#a")
    doc.replace("", query='<div id="a">x</div>')
    with pytest.raises(NoElement):
        handle.replace("z", query="x")


# ── Raw patch access ─────────────────────────────────────────────────────────

def test_patch_takes_the_revision_to_write_against():
    # A caller holding a revision from an earlier read is writing against THAT
    # one; substituting a newer one this object happens to hold would defeat
    # the check it asked for.
    import httpx

    from dreamlake.api.notes import Note

    sent: dict = {}

    class FakeHttp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def request(self, method, url, json=None, headers=None):
            sent["headers"] = headers or {}
            return httpx.Response(200, json={"etag": "rev-9"}, request=httpx.Request(method, url))

    class FakeClient:
        remote = "https://example.test"

        def http(self):
            return FakeHttp()

    n = Note("651111111111111111111111", namespace="ns", client=FakeClient())
    n._etag = "rev-cached"

    n.patch("--- a\n+++ b\n", if_match="rev-explicit")
    assert sent["headers"]["If-Match"] == "rev-explicit"

    n.patch("--- a\n+++ b\n")
    assert sent["headers"]["If-Match"] == "rev-9"  # adopted from the last write

    n.patch("--- a\n+++ b\n", force=True)
    assert "If-Match" not in sent["headers"]


def test_patch_refuses_if_match_and_force_together():
    from dreamlake.api.notes import Note

    n = Note("651111111111111111111111", namespace="ns", client=object())
    with pytest.raises(ValueError, match="give one"):
        n.patch("d", if_match="r", force=True)


# ── Ranged reads ─────────────────────────────────────────────────────────────

def test_read_lines_reports_what_it_left_out():
    # The danger: a caller reads part of a note, rewrites what it read, and
    # posts that back as the body — deleting everything it never saw. These
    # fields are what make that visible beforehand.
    import httpx

    from dreamlake.api.notes import Note

    captured: dict = {}

    class FakeHttp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            captured["params"] = params
            return httpx.Response(
                200,
                json={
                    "text": "one\ntwo\n",
                    "etag": "rev-whole",
                    "startLine": 1,
                    "endLine": 2,
                    "totalLines": 6,
                    "truncated": True,
                },
                request=httpx.Request("GET", url),
            )

    class FakeClient:
        remote = "https://example.test"

        def http(self):
            return FakeHttp()

    n = Note("651111111111111111111111", namespace="ns", client=FakeClient())
    part = n.read_lines(1, 2)

    assert captured["params"] == {"startLine": "1", "endLine": "2"}
    assert part.text == "one\ntwo\n"
    assert part.truncated is True
    assert (part.start_line, part.end_line, part.total_lines) == (1, 2, 6)
    # The revision is the WHOLE note's, which is what a later write is checked
    # against — a validator describing only the range would turn a partial read
    # into a whole-document overwrite.
    assert part.etag == "rev-whole"
    assert "truncated" in repr(part)


# ── Boundaries ───────────────────────────────────────────────────────────────
#
# Every case here is one where a wrong answer is SILENT: the edit succeeds and
# lands on the wrong characters, or a refusal that should have happened did
# not. They are the ones worth spending tests on.

def test_an_element_handle_survives_an_edit_that_moved_it():
    # The trap: the handle stored offsets when it was made, an earlier edit
    # shifted everything after it, and the handle now points into the middle of
    # different text. Nothing about the result would say so.
    doc, _ = doc_of("<p id=a>one</p><p id=b>two</p>")
    later = doc.select("#b")
    doc.replace("ONE!!!!!!!!!!", query="one")

    assert later.text == "two"
    later.replace("TWO", query="two")
    assert doc.text == "<p id=a>ONE!!!!!!!!!!</p><p id=b>TWO</p>"


def test_a_handle_to_something_since_deleted_refuses_rather_than_guessing():
    doc, _ = doc_of("<p id=a>one</p><p id=b>two</p>")
    gone = doc.select("#b")
    doc.replace("", query="<p id=b>two</p>")

    with pytest.raises(NoElement):
        gone.text


def test_a_failed_edit_leaves_the_draft_byte_for_byte_unchanged():
    doc, _ = doc_of("x\n")
    with pytest.raises(NoMatch):
        doc.replace("Z", query="not here")
    assert doc.text == "x\n"
    assert doc.dirty is False


def test_an_ambiguous_edit_says_how_many_it_found_and_how_to_proceed():
    # Editing the first of several is the failure an agent cannot see and a
    # reviewer cannot spot in a diff.
    doc, _ = doc_of("dup dup\n")
    with pytest.raises(AmbiguousMatch, match="found 2"):
        doc.replace("X", query="dup")
    assert doc.text == "dup dup\n"


def test_count_must_match_exactly_in_both_directions():
    doc, _ = doc_of("dup dup\n")
    with pytest.raises(AmbiguousMatch):
        doc.replace("X", query="dup", count=3)
    doc.replace("X", query="dup", count=2)
    assert doc.text == "X X\n"


def test_revert_restores_the_source_as_read():
    doc, _ = doc_of("a\nb\n")
    doc.replace("B", query="b")
    assert doc.dirty is True
    doc.revert()
    assert doc.text == "a\nb\n"
    assert doc.dirty is False
    assert doc.diff() == ""


def test_attribute_values_are_escaped_so_a_value_cannot_close_its_own_tag():
    # An unescaped quote ends the attribute and everything after it becomes
    # markup — which is how an attribute update turns into an injection.
    doc, _ = doc_of('<a>x</a>')
    doc.select("a").update(attrs={"t": 'he said "hi" <b>'})
    assert "&quot;" in doc.text
    assert "&lt;b&gt;" in doc.text
    # And the element still parses as one element with that attribute — which
    # is the actual claim; escaping that produced broken markup would pass the
    # two assertions above and still be wrong.
    assert doc.select("a").attrs["t"] == 'he said "hi" <b>'


def test_removing_an_attribute_removes_it_rather_than_emptying_it():
    doc, _ = doc_of('<a id="n">x</a>')
    doc.select("a").update(attrs={"id": None})
    assert doc.text == "<a>x</a>"


def test_successive_edits_each_see_the_previous_result():
    doc, _ = doc_of("a a a\n")
    doc.replace("b", query="a", all=True)
    assert doc.text == "b b b\n"
    doc.replace("c", query="b", all=True)
    assert doc.text == "c c c\n"


def test_an_element_handle_can_be_read_as_well_as_written():
    # The common edit is conditional — change this link only if it still points
    # where you thought. Not expressible if attributes are write-only.
    doc, _ = doc_of('<a href="/a" download>x</a>')
    el = doc.select("a")
    assert el.tag == "a"
    assert el.attrs["href"] == "/a"
    # An attribute written without a value reads as None, not "" — the two mean
    # different things in HTML and conflating them changes the markup on write.
    assert el.attrs["download"] is None


def test_attrs_are_read_from_the_current_draft_not_from_when_the_handle_was_made():
    doc, _ = doc_of('<a href="/a">x</a>')
    el = doc.select("a")
    el.update(attrs={"href": "/b"})
    assert el.attrs["href"] == "/b"


def test_mutating_the_returned_attrs_does_not_change_the_document():
    # It is a copy. Otherwise a caller "trying something" silently edits the
    # draft without going through update(), and the change never reaches save().
    doc, _ = doc_of('<a href="/a">x</a>')
    doc.select("a").attrs["href"] = "/hacked"
    assert doc.text == '<a href="/a">x</a>'
    assert doc.dirty is False


def test_save_is_conditional_on_the_revision_the_draft_was_read_at():
    # Not on whatever the note object currently holds. Those differ the moment
    # anything else writes through the same note, and then the precondition
    # passes against a revision this draft never saw — a silent overwrite of
    # somebody else's work, reported as success.
    doc, note = doc_of("one\n")
    read_at = doc.etag

    note.write("somebody else\n")          # the note object's cache moves on
    doc.replace("mine", query="one")
    doc.save()

    assert note.preconditions[-1] == read_at


def test_force_sends_no_precondition_at_all():
    doc, note = doc_of("one\n")
    doc.replace("mine", query="one")
    doc.save(force=True)
    assert note.preconditions[-1] is None
