"""Selecting elements, and editing them without rewriting the file.

The property under test throughout: an edit changes the characters it addresses
and leaves every other character byte-for-byte alone. Parse-and-reserialise
would pass a "did it work" test and fail this one — attribute quoting
normalised, entities respelled, whitespace moved — and the damage only shows up
in someone's diff review.
"""

from __future__ import annotations

import pytest

from dreamlake.api._html import (
    AmbiguousElement,
    NoElement,
    SelectorError,
    parse,
    select,
    select_all,
    text_runs,
)

PAGE = (
    '<!doctype html>\n'
    '<div id="contact" class="card wide">\n'
    '  <p>Contact us at <a href="/sales">sales</a> &amp; more</p>\n'
    '  <p class="note">Second</p>\n'
    '</div>\n'
    '<div id="pricing"><span>$42</span></div>\n'
)


# ── Selection ────────────────────────────────────────────────────────────────

def test_by_id_class_tag_and_attribute():
    doc = parse(PAGE)
    assert select(doc, "#contact").tag == "div"
    assert select(doc, "p.note").attrs["class"] == "note"
    assert select(doc, "a[href]").attrs["href"] == "/sales"
    assert select(doc, '[href="/sales"]').tag == "a"


def test_descendant_and_child_combinators():
    doc = parse(PAGE)
    assert select(doc, "#contact a").attrs["href"] == "/sales"
    assert select(doc, "#pricing > span").tag == "span"


def test_nth_of_type_disambiguates():
    doc = parse(PAGE)
    assert select(doc, "#contact p:nth-of-type(2)").attrs["class"] == "note"


def test_missing_and_ambiguous_are_different_errors():
    # An agent that gets "no element" knows to look again; one that gets
    # "3 matched" knows to narrow. A single generic failure tells it neither.
    doc = parse(PAGE)
    with pytest.raises(NoElement):
        select(doc, "#nope")
    with pytest.raises(AmbiguousElement) as e:
        select(doc, "p")
    assert "nth-of-type" in str(e.value)


def test_unsupported_selector_syntax_is_refused_not_approximated():
    doc = parse(PAGE)
    for bad in ["p, div", "a::before", "[href^=/s]", ""]:
        with pytest.raises(SelectorError):
            select(doc, bad)


def test_select_all_returns_document_order():
    doc = parse(PAGE)
    assert [e.attrs.get("class") for e in select_all(doc, "p")] == [None, "note"]


def test_scoping_to_a_subtree():
    doc = parse(PAGE)
    contact = select(doc, "#contact")
    assert len(select_all(doc, "p", within=contact)) == 2
    assert select_all(doc, "span", within=contact) == []


# ── Source ranges ────────────────────────────────────────────────────────────

def test_inner_and_outer_are_exact_source_ranges():
    doc = parse(PAGE)
    span = select(doc, "#pricing span")
    assert PAGE[slice(*span.outer)] == "<span>$42</span>"
    assert PAGE[slice(*span.inner)] == "$42"


def test_start_tag_range_covers_only_the_open_tag():
    doc = parse(PAGE)
    a = select(doc, "a")
    assert PAGE[slice(*a.start_tag)] == '<a href="/sales">'


def test_void_elements_have_no_inner_range():
    doc = parse('<p>a<br>b</p>')
    br = select(doc, "br")
    assert br.inner[0] == br.inner[1]


def test_self_closing_syntax():
    doc = parse('<div><img src="x"/></div>')
    assert select(doc, "img").attrs["src"] == "x"


def test_unclosed_element_runs_to_the_end():
    src = "<div id=a><p>text"
    doc = parse(src)
    assert src[slice(*select(doc, "#a").inner)] == "<p>text"


# ── Text nodes ───────────────────────────────────────────────────────────────

def test_text_is_decoded_but_maps_back_to_raw_source():
    # The reader sees `&`; the source keeps `&amp;`. An edit computed on the
    # decoded text has to land on the right raw characters anyway.
    doc = parse(PAGE)
    p = select(doc, "#contact p:nth-of-type(1)")
    runs = text_runs(doc, p)
    joined = "".join(r.decoded for r in runs)
    assert "Contact us at " in joined
    assert " & more" in joined


def test_text_nodes_are_not_joined_across_markup():
    # `<b>a</b>b` is two runs. Matching "ab" across them would produce an edit
    # that cannot be expressed as a change to one span of source.
    doc = parse("<p><b>a</b>b</p>")
    runs = text_runs(doc, select(doc, "p"))
    assert [r.decoded for r in runs] == ["a", "b"]


def test_each_decoded_character_has_a_source_span():
    doc = parse("<p>a&amp;b</p>")
    run = text_runs(doc, select(doc, "p"))[0]
    assert run.decoded == "a&b"
    assert len(run.spans) == len(run.decoded)
    # The '&' occupies five source characters; the span says so.
    amp = run.spans[1]
    assert "<p>a&amp;b</p>"[slice(*amp)] == "&amp;"


def test_tag_names_and_attributes_are_not_text():
    # Element-scoped search must not match the markup itself; otherwise
    # replacing "href" would corrupt the document.
    doc = parse('<p class="href">body</p>')
    runs = text_runs(doc, select(doc, "p"))
    assert [r.decoded for r in runs] == ["body"]


def test_nested_scopes_do_not_duplicate_text():
    doc = parse("<div><p>x</p></div>")
    outer = text_runs(doc, select(doc, "div"))
    assert "".join(r.decoded for r in outer) == "x"
