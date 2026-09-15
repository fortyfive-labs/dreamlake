"""Differential tests: this module against real JavaScript.

The claim `_jsregex` makes is "JavaScript semantics", and the only honest way
to check that is to ask JavaScript. Every case in jsregex_cases.json is run
through Node and through this module, and the outputs must agree.

Node is used when it is on PATH and the whole comparison is skipped otherwise,
so the suite still runs somewhere without it — but the cases are also pinned
with expected values recorded from Node, so a machine without Node still
catches a regression. The differential test is what keeps those recorded values
honest when the module changes.

One case disagrees on purpose; see EXPECTED_DIVERGENCE.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from dreamlake.api._jsregex import (
    InvalidPattern,
    JsRegex,
    UnsupportedPattern,
    compile_js,
    expand_replacement,
)

CASES = json.loads((Path(__file__).parent / "jsregex_cases.json").read_text())
NODE = shutil.which("node")

#: Cases where matching JavaScript would be the wrong answer.
#:
#: A regex without the `u` flag counts UTF-16 code units, so JavaScript sees an
#: emoji as two characters. The Notes API specifies character indices as
#: "0-based, end-exclusive Unicode code points" and requires JavaScript offsets
#: to be converted at the boundary — so code points are the contract, and
#: reproducing the UTF-16 behaviour here would put the bug in the wrong place.
EXPECTED_DIVERGENCE = {"astral-without-u"}


def _run_node(cases: list[dict]) -> dict[str, dict]:
    script = """
    const cs = JSON.parse(process.argv[1]);
    const out = {};
    for (const c of cs) {
      try {
        const flags = c.f.includes('g') ? c.f : c.f + 'g';
        out[c.n] = { ok: true, replaced: c.s.replace(new RegExp(c.p, c.f), c.r) };
      } catch (e) { out[c.n] = { ok: false, err: String(e.constructor.name) }; }
    }
    process.stdout.write(JSON.stringify(out));
    """
    proc = subprocess.run(
        [NODE, "-e", script, json.dumps(cases)],
        capture_output=True, text=True, timeout=60,
    )
    proc.check_returncode()
    return json.loads(proc.stdout)


def _apply(case: dict) -> str:
    """Run one case through this module, mirroring JavaScript's `g` flag."""
    flags = case["f"].replace("g", "")
    rx = JsRegex(case["p"], flags)
    return rx.replace(case["s"], case["r"], count=0 if "g" in case["f"] else 1)


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_matches_node_exactly():
    truth = _run_node(CASES)
    mismatches = []
    for case in CASES:
        name = case["n"]
        if name in EXPECTED_DIVERGENCE:
            continue
        expected = truth[name]
        if not expected["ok"]:
            # JavaScript itself rejects it; so must we.
            with pytest.raises((InvalidPattern, UnsupportedPattern)):
                _apply(case)
            continue
        got = _apply(case)
        if got != expected["replaced"]:
            mismatches.append(f"{name}: node={expected['replaced']!r} ours={got!r}")
    assert not mismatches, "diverged from JavaScript:\n  " + "\n  ".join(mismatches)


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_the_one_divergence_is_the_one_we_chose():
    # If this ever passes, either JavaScript changed or someone made the module
    # count UTF-16 units — both worth noticing.
    truth = _run_node([{"n": "astral-without-u", "p": ".", "f": "g", "s": "a😀b", "r": "."}])
    assert truth["astral-without-u"]["replaced"] == "...."  # UTF-16 units
    assert JsRegex(".", "").replace("a😀b", ".", count=0) == "..."  # code points


# ── Translation rules, stated directly ───────────────────────────────────────

def test_named_groups_use_javascript_syntax():
    assert JsRegex(r"(?<year>\d{4})").findall("2026")[0].group("year") == "2026"
    # Python's own syntax is not silently accepted — it would work by accident
    # and then differ from the CLI.
    assert compile_js(r"(?<a>x)").pattern == "(?P<a>x)"


def test_lookbehind_is_not_mistaken_for_a_named_group():
    assert JsRegex("(?<=a)b").replace("ab", "B", count=0) == "aB"
    assert JsRegex("(?<!a)b").replace("cb", "B", count=0) == "cB"


def test_digit_and_word_are_ascii_without_the_u_flag():
    # JavaScript's \d is [0-9] unless /u is given; Python's is every Unicode
    # digit. A pattern written for JavaScript would otherwise match more here.
    assert JsRegex(r"\d+").replace("١٢٣ 42", "<N>", count=0) == "١٢٣ <N>"
    assert JsRegex(r"\d+", "u").replace("١٢٣ 42", "<N>", count=0) == "<N> <N>"


def test_ascii_classes_translate_inside_a_character_class():
    # The bracketed form would nest and change meaning.
    assert JsRegex(r"[\d.]+").replace("١٢ 3.5", "<n>", count=0) == "١٢ <n>"


def test_unmatched_group_is_empty_not_an_error():
    assert JsRegex("(a)|(b)").replace("b", "[$1][$2]", count=0) == "[][b]"


def test_reference_to_a_group_that_does_not_exist_stays_literal():
    assert JsRegex("(a)").replace("a", "$3", count=0) == "$3"


def test_dollar_tokens():
    assert JsRegex("x").replace("x", "$$", count=0) == "$"
    assert JsRegex("b").replace("abc", "[$`]", count=0) == "a[a]c"
    assert JsRegex("b").replace("abc", "[$']", count=0) == "a[c]c"
    assert JsRegex("b").replace("abc", "<$&>", count=0) == "a<b>c"


def test_empty_character_class_matches_nothing():
    assert JsRegex("[]a").replace("a", "Z", count=0) == "a"


def test_negated_empty_class_matches_anything_including_newline():
    assert JsRegex("[^]").replace("a\nb", ".", count=0) == "..."


# ── Refusals ─────────────────────────────────────────────────────────────────

def test_g_flag_is_rejected_with_an_explanation():
    # Whether every match is replaced belongs to the call, not the pattern;
    # accepting both would give two ways to say it and let them disagree.
    with pytest.raises(UnsupportedPattern, match="all=True"):
        compile_js("a", "g")


def test_unknown_flags_are_rejected():
    with pytest.raises(UnsupportedPattern):
        compile_js("a", "x")


def test_property_escape_without_u_is_refused_rather_than_guessed():
    with pytest.raises(UnsupportedPattern, match="flags='u'"):
        compile_js(r"\p{L}", "")


def test_invalid_pattern_says_so():
    with pytest.raises(InvalidPattern):
        compile_js("(unclosed", "")


def test_expand_replacement_needs_no_regex_object():
    rx = JsRegex(r"(?<a>x)")
    m = next(rx.finditer("x"))
    assert expand_replacement("[$<a>]", m, "x") == "[x]"


# ── Limits ───────────────────────────────────────────────────────────────────
#
# A pattern is caller-supplied and a document can be large, so the two together
# are an easy denial of service. These are checked before the engine is
# entered, which is the only place a check reliably happens.

def test_an_oversized_pattern_is_refused_before_compiling():
    from dreamlake.api._jsregex import LIMITS, PatternTooLong

    with pytest.raises(PatternTooLong, match=str(LIMITS.pattern)):
        compile_js("a" * (LIMITS.pattern + 1))


def test_an_oversized_document_is_refused_before_scanning():
    from dreamlake.api._jsregex import InputTooLarge, LIMITS

    rx = JsRegex("a")
    huge = "b" * (LIMITS.input + 1)
    with pytest.raises(InputTooLarge):
        rx.findall(huge)
    with pytest.raises(InputTooLarge):
        rx.replace(huge, "c")


def test_a_runaway_scan_is_abandoned_with_an_explicit_error():
    # Not "returns fewer matches" — a truncated result presented as the whole
    # answer is worse than a failure, because a replacement built on it would
    # silently apply to a subset.
    import dreamlake.api._jsregex as jr

    original = jr.LIMITS.seconds
    jr.LIMITS.seconds = 0.0  # every step is past the deadline
    try:
        rx = JsRegex("a")
        with pytest.raises(jr.MatchTimeout, match="narrow the pattern"):
            rx.findall("aaa")
    finally:
        jr.LIMITS.seconds = original


def test_limits_are_generous_enough_for_real_documents():
    # A limit that trips on ordinary use would push people to force-flags.
    from dreamlake.api._jsregex import LIMITS

    assert LIMITS.pattern >= 1000
    assert LIMITS.input >= 1_000_000
    rx = JsRegex(r"\bTODO\b")
    assert len(rx.findall("TODO x " * 5000)) == 5000


# ── Zero-width matches and the scan cursor ───────────────────────────────────

def test_a_zero_width_match_at_end_of_input_does_not_repeat_forever():
    # `search(s, pos)` CLAMPS a position past the end back to the end, so a
    # zero-width match at EOF is found again at every step after it. The symptom
    # is not a hang: it is the SAME span returned up to the result limit, and as
    # edits those would all overlap.
    #
    # Checked against Node below; this states the span list directly so the
    # failure names the shape rather than just "diverged".
    from dreamlake.api._jsregex import JsRegex

    spans = [m.span() for m in JsRegex("x*").finditer("ab")]
    assert spans == [(0, 0), (1, 1), (2, 2)]


def test_zero_width_scanning_terminates_on_an_empty_input():
    from dreamlake.api._jsregex import JsRegex

    assert [m.span() for m in JsRegex("x*").finditer("")] == [(0, 0)]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_zero_width_spans_agree_with_javascript():
    # The same scan JavaScript performs, asked of Node directly — `replace`
    # goes through a different code path and would not have caught this.
    script = """
    const cs = JSON.parse(process.argv[1]);
    const out = {};
    for (const c of cs) {
      const rx = new RegExp(c.p, c.f.includes('g') ? c.f : c.f + 'g');
      const spans = []; let m;
      while ((m = rx.exec(c.s)) !== null) {
        spans.push([m.index, m.index + m[0].length]);
        if (m.index === rx.lastIndex) rx.lastIndex++;
      }
      out[c.n] = spans;
    }
    process.stdout.write(JSON.stringify(out));
    """
    cases = [
        {"n": "eof", "p": "x*", "f": "g", "s": "ab"},
        {"n": "empty", "p": "x*", "f": "g", "s": ""},
        {"n": "anchors", "p": "^", "f": "gm", "s": "a\nb\n"},
        {"n": "boundaries", "p": r"\b", "f": "g", "s": "ab cd"},
    ]
    proc = subprocess.run(
        [NODE, "-e", script, json.dumps(cases)], capture_output=True, text=True, timeout=60
    )
    proc.check_returncode()
    truth = json.loads(proc.stdout)

    for case in cases:
        ours = [list(m.span()) for m in JsRegex(case["p"], case["f"].replace("g", "")).finditer(case["s"])]
        assert ours == truth[case["n"]], f"{case['n']}: node={truth[case['n']]} ours={ours}"
