"""The meaning of an edit, tested where it is defined.

These are the rules the issue states, each one written as the thing that would
go wrong without it. The boundaries it calls out by name — empty files, no
final newline, CRLF, multiline matches, emoji, ambiguous matches, zero-width
patterns — are here rather than left to the SDK and CLI suites, because both of
those call this module and only one of them should have to prove it.
"""

from __future__ import annotations

import pytest

from dreamlake.api._editing import (
    AmbiguousMatch,
    EditError,
    InvalidRange,
    NoMatch,
    delete,
    find,
    insert,
    line_of,
    line_span,
    replace,
)

DOC = "# Title\n\nDraft one\nDraft two\n"


# ── Match counting ───────────────────────────────────────────────────────────

def test_one_match_is_required_by_default():
    # Quietly editing the first of several is the failure an agent cannot see
    # and a reviewer cannot spot in the diff.
    with pytest.raises(AmbiguousMatch) as e:
        replace(DOC, "Published", query="Draft")
    assert "found 2" in str(e.value)
    assert "all=True" in str(e.value)


def test_all_replaces_every_match():
    assert replace(DOC, "Published", query="Draft", all=True) == (
        "# Title\n\nPublished one\nPublished two\n"
    )


def test_count_means_exactly_that_many():
    assert replace(DOC, "X", query="Draft", count=2).count("X") == 2
    with pytest.raises(AmbiguousMatch):
        replace(DOC, "X", query="Draft", count=3)


def test_zero_matches_is_an_error_for_an_edit():
    with pytest.raises(NoMatch):
        replace(DOC, "X", query="nowhere")
    # ...but not for a search: "nothing matched" is a legitimate answer.
    assert find(DOC, "nowhere") == []


def test_count_and_all_together_are_refused():
    with pytest.raises(EditError, match="different things"):
        replace(DOC, "X", query="Draft", count=2, all=True)


def test_a_failed_edit_changes_nothing():
    before = DOC
    with pytest.raises(AmbiguousMatch):
        replace(DOC, "X", query="Draft")
    assert DOC == before


# ── Targeting ────────────────────────────────────────────────────────────────

def test_conflicting_targets_are_refused_rather_than_ranked():
    # A caller who gave two meant one; we cannot tell which.
    with pytest.raises(EditError, match="conflicting targets"):
        replace(DOC, "X", query="Draft", line=3)


def test_no_target_at_all_says_what_is_missing():
    with pytest.raises(EditError, match="say where"):
        replace(DOC, "X")


def test_lines_are_one_based_and_inclusive():
    assert replace(DOC, "Replaced\n", line=3) == "# Title\n\nReplaced\nDraft two\n"
    assert replace(DOC, "Block\n", line=(3, 4)) == "# Title\n\nBlock\n"


def test_character_ranges_are_zero_based_and_end_exclusive():
    assert replace("abcdef", "X", ind=(2, 4)) == "abXef"


def test_reversed_and_out_of_range_are_rejected_not_clamped():
    with pytest.raises(InvalidRange):
        replace(DOC, "X", ind=(5, 2))
    with pytest.raises(InvalidRange):
        replace(DOC, "X", ind=(0, 9999))
    with pytest.raises(InvalidRange):
        replace(DOC, "X", line=(4, 2))


# ── Newlines ─────────────────────────────────────────────────────────────────

def test_a_line_edit_ends_the_line():
    assert replace(DOC, "No newline given", line=3) == "# Title\n\nNo newline given\nDraft two\n"


def test_a_line_edit_does_not_double_an_existing_newline():
    assert replace(DOC, "Given\n", line=3) == "# Title\n\nGiven\nDraft two\n"


def test_character_and_query_edits_add_no_newline():
    assert replace(DOC, "X", ind=(0, 1)) == "X Title\n\nDraft one\nDraft two\n"
    assert replace("one two", "X", query="two") == "one X"


def test_crlf_convention_is_followed():
    src = "a\r\nb\r\n"
    assert replace(src, "Z", line=2) == "a\r\nZ\r\n"


def test_a_file_with_no_final_newline_keeps_its_shape():
    src = "a\nb"
    assert replace(src, "Z", line=2) == "a\nZ\n"
    # Appending after the last line starts a new one rather than joining it.
    assert insert(src, "c", line=3) == "a\nb\nc\n"


def test_empty_document():
    assert insert("", "first", line=1) == "first\n"
    assert insert("", "x", ind=0) == "x"
    with pytest.raises(NoMatch):
        replace("", "X", query="a")


# ── Insertion ────────────────────────────────────────────────────────────────

def test_insert_goes_before_the_addressed_line():
    assert insert(DOC, "New", line=3) == "# Title\n\nNew\nDraft one\nDraft two\n"


def test_insert_at_a_character_boundary_including_both_ends():
    assert insert("abc", "X", ind=0) == "Xabc"
    assert insert("abc", "X", ind=3) == "abcX"
    with pytest.raises(InvalidRange):
        insert("abc", "X", ind=4)
    with pytest.raises(InvalidRange):
        insert("abc", "X", ind=-1)


def test_insert_needs_exactly_one_of_line_or_ind():
    with pytest.raises(EditError):
        insert(DOC, "X")
    with pytest.raises(EditError):
        insert(DOC, "X", line=1, ind=0)


# ── Regex ────────────────────────────────────────────────────────────────────

def test_regex_replacement_expands_javascript_groups():
    assert replace("a=1 b=22", "$1: $2", regex=r"(\w+)=(\d+)", all=True) == "a: 1 b: 22"


def test_named_captures():
    out = replace(
        "timeout=30 retries=5",
        "$<key>: $<value>",
        regex=r"(?<key>timeout|retries)=(?<value>\d+)",
        all=True,
    )
    assert out == "timeout: 30 retries: 5"


def test_replacements_are_computed_against_the_pre_edit_snapshot():
    # Without this, a replacement can match text an earlier replacement wrote,
    # and `all=True` becomes order-dependent.
    assert replace("aa", "aa", query="a", all=True) == "aaaa"


def test_zero_width_regex_replacement_is_refused():
    # It has no text to stand in for; the operation meant is an insert.
    with pytest.raises(EditError, match="insert"):
        replace("abc", "-", regex="(?=b)", all=True)


def test_find_reports_ranges_and_captures():
    m = find("x timeout=30", regex=r"(?<key>\w+)=(?<value>\d+)")[0]
    assert m.text == "timeout=30"
    assert m.ind == (2, 12)
    assert m.line == (1, 1)
    assert m.captures["key"] == "timeout"
    assert m.capture_spans["value"] == (10, 12)


def test_an_unmatched_optional_capture_has_no_span():
    m = find("y", regex=r"(?<a>x)?y")[0]
    assert m.captures["a"] is None
    assert m.capture_spans["a"] is None


def test_find_bounds_its_output():
    many = find("a" * 5000, "a", limit=10)
    assert len(many) == 10


def test_an_empty_query_is_refused():
    with pytest.raises(EditError, match="empty query"):
        find(DOC, "")


# ── Unicode ──────────────────────────────────────────────────────────────────

def test_indices_are_code_points_not_utf16_units():
    # An emoji is one character here, as the API contract requires — even
    # though JavaScript's native offsets would count it as two.
    src = "a😀b"
    assert len(src) == 3
    m = find(src, "b")[0]
    assert m.ind == (2, 3)
    assert replace(src, "X", ind=(1, 2)) == "aXb"


def test_multiline_match_spans_report_both_lines():
    src = "start\nmid\nend\n"
    m = find(src, regex=r"start[\s\S]*?end")[0]
    assert m.line == (1, 3)


# ── Line helpers ─────────────────────────────────────────────────────────────

def test_line_of_and_line_span():
    src = "a\nbb\nccc\n"
    assert line_of(src, 0) == 1
    assert line_of(src, 2) == 2
    assert line_span(src, 2, 2) == (2, 5)
    assert src[slice(*line_span(src, 2, 3))] == "bb\nccc\n"


def test_line_span_past_the_end_is_refused():
    with pytest.raises(InvalidRange):
        line_span("a\n", 5, 5)


# ── Delete ───────────────────────────────────────────────────────────────────

def test_delete_by_query_and_by_line():
    assert delete(DOC, query="Draft one\n") == "# Title\n\nDraft two\n"
    assert delete(DOC, line=(3, 4)) == "# Title\n\n"


def test_delete_obeys_the_same_match_rules():
    with pytest.raises(AmbiguousMatch):
        delete(DOC, query="Draft")
