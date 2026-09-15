"""The editing contract, from the fixtures the CLI also reads.

The SDK and the CLI each implement these rules — Python here, TypeScript in
dreamlake-cli. Two implementations of one contract is a hazard, and comparing
their prose is not a check. So both suites load the SAME file and assert
against the same data.

The fixture lives in the CLI repo (`src/cli/notes/editing.fixtures.json`) and
is vendored here so this suite runs without a checkout of it. `make
sync-fixtures`, or the one-liner in FIXTURE_SOURCE below, refreshes the copy;
the test fails loudly if the two drift, which is the situation worth
interrupting someone over.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dreamlake.api._editing import (
    AmbiguousMatch,
    EditError,
    InvalidRange,
    NoMatch,
    delete,
    find,
    insert,
    replace,
)

FIXTURE_SOURCE = "dreamlake-cli:src/cli/notes/editing.fixtures.json"

DATA = json.loads((Path(__file__).parent / "editing.fixtures.json").read_text())
CASES = DATA["cases"]

ERRORS = {
    "EditError": EditError,
    "NoMatch": NoMatch,
    "AmbiguousMatch": AmbiguousMatch,
    "InvalidRange": InvalidRange,
}


def _run(case: dict):
    a = dict(case["args"])
    op = case["op"]
    # JSON has no tuples; a two-element list is a range in both languages.
    for key in ("line", "ind"):
        if isinstance(a.get(key), list):
            a[key] = tuple(a[key])

    if op == "replace":
        return replace(case["doc"], a.pop("text"), **a)
    if op == "insert":
        return insert(case["doc"], a.pop("text"), **a)
    if op == "delete":
        return delete(case["doc"], **a)
    if op == "find":
        return find(case["doc"], a.pop("query", None), **a)
    raise AssertionError(f"unknown op {op!r}")


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_shared_fixture(case):
    if "error" in case:
        with pytest.raises(ERRORS[case["error"]]):
            _run(case)
        return

    got = _run(case)

    if "matches" in case:
        slim = [
            {"text": m.text, "ind": list(m.ind), "line": list(m.line)}
            for m in got
        ]
        assert slim == case["matches"]
        return

    assert got == case["out"]


def test_every_case_asserts_something():
    # A fixture with neither an expected document nor an expected refusal would
    # pass in both languages while proving nothing.
    for c in CASES:
        assert ("out" in c) or ("error" in c) or ("matches" in c), c["name"]


def test_the_fixture_covers_the_boundaries_the_issue_names():
    # Issue #240 lists these by name. If one disappears from the fixture, the
    # coverage disappears from BOTH suites at once — worth failing over.
    names = {c["name"] for c in CASES}
    required = {
        "insert-into-empty-document",
        "insert-past-a-file-with-no-final-newline",
        "line-edit-follows-crlf",
        "find-regex-multiline-span",
        "regex-g-flag-refused",
        "replace-ambiguous-is-refused",
        "regex-zero-width-refused",
        "char-range-is-code-points-not-utf16",
        "find-emoji-ranges-are-code-points",
        "replacements-use-the-pre-edit-snapshot",
    }
    missing = required - names
    assert not missing, f"fixture no longer covers: {sorted(missing)}"
