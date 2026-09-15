"""HTML selection and element edits, from the fixtures the CLI also reads.

Third shared fixture file. Same reason as the other two: the SDK and the CLI
each implement these rules, and comparing their prose is not a check.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dreamlake.api._doc import Doc
from dreamlake.api._editing import AmbiguousMatch, NoMatch
from dreamlake.api._html import (
    AmbiguousElement,
    NoElement,
    SelectorError,
    parse,
    select,
    text_runs,
)

from .test_doc import FakeNote

_DATA = json.loads((Path(__file__).parent / "html.fixtures.json").read_text())
CASES = _DATA["cases"]
ELEMENT_INSERTS = _DATA["elementInserts"]

ERRORS = {
    "NoElement": NoElement,
    "AmbiguousElement": AmbiguousElement,
    "SelectorError": SelectorError,
    "NoMatch": NoMatch,
    "AmbiguousMatch": AmbiguousMatch,
}


def _replace_in(doc_src: str, css: str, query: str, text: str, all_: bool) -> str:
    doc = Doc(FakeNote(doc_src), doc_src, "rev-1")
    return doc.select(css).replace(text, query=query, all=all_)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_html_fixture(case):
    if "error" in case:
        with pytest.raises(ERRORS[case["error"]]):
            if "replaceIn" in case:
                r = case["replaceIn"]
                _replace_in(case["doc"], case["select"], r["query"], r["text"], r.get("all", False))
            else:
                select(parse(case["doc"]), case["select"])
        return

    if "replaceIn" in case:
        r = case["replaceIn"]
        got = _replace_in(case["doc"], case["select"], r["query"], r["text"], r.get("all", False))
        assert got == case["out"]
        return

    parsed = parse(case["doc"])
    el = select(parsed, case["select"])
    assert case["doc"][slice(*el.inner)] == case["inner"], "inner source"
    assert "".join(r.decoded for r in text_runs(parsed, el)) == case["text"], "decoded text"


# ── Element-relative insertion ───────────────────────────────────────────────
#
# The same cases the CLI suite runs. Two implementations of "put this markup
# next to that element" is exactly the pair that drifts, and the drift shows up
# as markup in the wrong place rather than as an error.

@pytest.mark.parametrize("case", ELEMENT_INSERTS, ids=lambda c: c["name"])
def test_element_insert_fixture(case):
    from dreamlake.api._editing import EditError

    doc = Doc(FakeNote(case["doc"]), case["doc"], "rev-1")
    el = doc.select(case["select"])

    if case.get("error"):
        with pytest.raises(EditError):
            el.insert(case["text"], position=case["position"])
        return

    assert el.insert(case["text"], position=case["position"]) == case["out"]
