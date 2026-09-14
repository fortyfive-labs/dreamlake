"""Outlines, and the promise that they are the SAME outlines.

An anchor is an address. If this module and dreamlake-server disagree about
what `## Setup` is called, then `read_section("setup")` and a TOC entry point
at different text and nothing in either output says so. So the first test here
is not about Markdown at all — it checks this implementation against a fixture
recorded from the server's own `sectionsOf`.

Regenerate the fixture when the server's rules change:

    npx tsx /tmp/toc-cmp/toc.ts > tests/toc_server.json

The rest are the cases that make anchors and ranges subtle, each written as the
mistake it prevents.
"""

from __future__ import annotations

import json
from pathlib import Path

from dreamlake.api._toc import (
    PREAMBLE_ANCHOR,
    html_toc,
    markdown_toc,
    slugify,
    toc,
)

HERE = Path(__file__).parent
DOCS = json.loads((HERE / "toc_docs.json").read_text())
SERVER = json.loads((HERE / "toc_server.json").read_text())


def test_agrees_with_the_server_anchor_for_anchor():
    # The anchors and source ranges an agent addresses must be the ones the
    # server resolves. A divergence here is silent in every other test.
    mismatches = []
    for doc in DOCS:
        mine = [(e.anchor, e.title, e.level, e.ind[0], e.ind[1]) for e in markdown_toc(doc["s"])]
        theirs = [(e["anchor"], e["title"], e["level"], e["start"], e["end"]) for e in SERVER[doc["n"]]]
        if mine != theirs:
            mismatches.append(f"{doc['n']}:\n    server={theirs}\n    ours  ={mine}")
    assert not mismatches, "outline diverged from the server:\n  " + "\n  ".join(mismatches)


# ── Anchors ──────────────────────────────────────────────────────────────────

def test_repeated_titles_get_stable_suffixes():
    # In document order, so an anchor keeps pointing at the same section as
    # long as the headings before it do not change.
    anchors = [e.anchor for e in markdown_toc("## Notes\na\n\n## Notes\nb\n\n## Notes\nc\n")]
    assert anchors == ["notes", "notes-2", "notes-3"]


def test_inline_markers_do_not_reach_the_anchor():
    e = markdown_toc("## `code` and **bold**\n")[0]
    assert e.title == "code and bold"
    assert e.anchor == "code-and-bold"


def test_a_title_of_only_punctuation_still_has_an_anchor():
    assert slugify("???") == "section"
    assert slugify("") == "section"


def test_unicode_titles_keep_their_letters():
    assert slugify("中文标题") == "中文标题"
    assert slugify("Café Menu") == "café-menu"


# ── Ranges ───────────────────────────────────────────────────────────────────

def test_a_section_owns_its_subtree():
    # "Replace this section" means the part a reader would point at, which
    # includes the deeper headings under it.
    md = "# A\n\n## A1\n\nx\n\n### A1a\n\ny\n\n## A2\n\nz\n\n# B\n\nw\n"
    by = {e.anchor: e for e in markdown_toc(md)}
    assert md[slice(*by["a1"].ind)] == "## A1\n\nx\n\n### A1a\n\ny\n\n"
    assert md[slice(*by["a"].ind)].endswith("## A2\n\nz\n\n")
    assert "# B" not in md[slice(*by["a"].ind)]


def test_text_before_the_first_heading_is_addressable():
    # Otherwise the top of a document is the one part with no name.
    entries = markdown_toc("intro words\n\n# First\n\nbody\n")
    assert entries[0].anchor == PREAMBLE_ANCHOR
    assert entries[0].line == (1, 2)


def test_a_document_with_no_headings_is_all_preamble():
    entries = markdown_toc("just text\nmore\n")
    assert [e.anchor for e in entries] == [PREAMBLE_ANCHOR]


def test_an_empty_document_has_no_entries():
    assert markdown_toc("") == []


def test_both_range_units_are_reported_and_agree():
    md = "# A\n\nbody\n\n## B\n\nmore\n"
    for e in markdown_toc(md):
        text = md[slice(*e.ind)]
        first, last = e.line
        # The line range must cover the same text the ind range does.
        lines = md.split("\n")
        assert text.startswith(lines[first - 1])
        assert last >= first


# ── Fenced code ──────────────────────────────────────────────────────────────

def test_a_hash_inside_a_fence_is_not_a_heading():
    # Treating it as one would let an agent address, and overwrite, a chunk of
    # somebody's shell example.
    md = "# Real\n\n```sh\n# not a heading\necho hi\n```\n\n## After\n\nx\n"
    assert [e.anchor for e in markdown_toc(md)] == ["real", "after"]


def test_tilde_fences_count_too():
    md = "# Real\n\n~~~\n# nope\n~~~\n\n## After\n"
    assert [e.anchor for e in markdown_toc(md)] == ["real", "after"]


def test_setext_headings_are_recognised():
    assert [e.level for e in markdown_toc("Title\n=====\n\nSub\n---\n")] == [1, 2]


# ── HTML ─────────────────────────────────────────────────────────────────────

def test_html_headings_describe_only_themselves():
    # An <h2> does not own the markup after it, and inventing a section
    # boundary would let an agent replace a "section" no element corresponds to.
    html = "<h1>One</h1><p>body</p><h2>Two</h2><p>more</p>"
    entries = html_toc(html)
    assert [e.anchor for e in entries] == ["one", "two"]
    assert html[slice(*entries[0].ind)] == "<h1>One</h1>"


def test_html_heading_text_drops_nested_tags():
    assert html_toc("<h2>A <em>B</em></h2>")[0].title == "A B"


def test_toc_picks_the_right_parser():
    assert toc("<h1>x</h1>")[0].anchor == "x"
    assert toc("# x\n")[0].anchor == "x"
    # An explicit choice always wins over the guess.
    assert toc("# x\n", html=False)[0].anchor == "x"
