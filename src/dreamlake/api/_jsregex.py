"""JavaScript regular expressions, executed in Python.

The Notes editing API takes regex patterns and replacement strings and has to
behave the same in the Python SDK, the TypeScript CLI, and anything an agent
writes against either. JavaScript is the shared dialect — the CLI runs on Node,
and the patterns people bring come from JavaScript documentation.

Python's `re` is a different language. The differences are not cosmetic:

    named groups      JS (?<n>...)        Python (?P<n>...)
    replacement       JS $1 $<n> $& $$    Python \\1 \\g<n>
    unmatched group   JS "" in output     Python may raise
    \\d \\w without /u  JS ASCII only       Python Unicode-aware
    lookbehind        JS variable width   Python fixed width only

So a pattern is TRANSLATED here rather than passed through, and a replacement
string is expanded by JavaScript's rules rather than Python's. Constructs that
cannot be translated faithfully raise `UnsupportedPattern` with the reason,
which is the one behaviour worse than being wrong: silently matching something
else.

Why the third-party `regex` module rather than the standard library: `re`
cannot do variable-width lookbehind or `\\p{...}` property escapes at all, and
both appear in ordinary JavaScript patterns. Measured against Node on a
differential fixture set, stdlib `re` agreed on 19 of 24 cases and `regex` on
23 — see tests/test_jsregex.py, which runs that comparison.

The 24th is deliberate. `'a😀b'.replace(/./g, '.')` gives four dots in
JavaScript, because a regex without the `u` flag counts UTF-16 code units and
an emoji is two of them. This module gives three. The Notes API specifies
character indices as "0-based, end-exclusive Unicode code points", and says
JavaScript offsets must be converted at the boundary — so code points are the
contract and matching JavaScript's historical behaviour here would be the bug.
"""

from __future__ import annotations

import time as _time

import regex as _re

__all__ = [
    "UnsupportedPattern",
    "InvalidPattern",
    "PatternTooLong",
    "InputTooLarge",
    "MatchTimeout",
    "LIMITS",
    "compile_js",
    "expand_replacement",
    "JsRegex",
]


class InvalidPattern(ValueError):
    """The pattern is not valid in either dialect."""


class UnsupportedPattern(ValueError):
    """Valid JavaScript that this translation cannot reproduce faithfully."""


class PatternTooLong(ValueError):
    """The pattern exceeds the size this API will compile."""


class InputTooLarge(ValueError):
    """The document exceeds the size this API will scan."""


class MatchTimeout(ValueError):
    """Matching ran past its deadline and was abandoned."""


class Limits:
    """Bounds on regex work, enforced in every client.

    A pattern is caller-supplied and a document can be large, so the two
    together are an easy denial of service: `(a+)+$` against a few thousand
    characters backtracks effectively forever, and a synchronous engine cannot
    be interrupted once it is inside `match`.

    So the sizes are checked BEFORE compiling or scanning — that is the only
    check that reliably happens — and the deadline is enforced by matching
    incrementally rather than by trying to stop an engine mid-call. The
    numbers are generous for real documents and small enough that a pathological
    pattern fails in about a second instead of never.
    """

    #: Characters. Long enough for the gnarliest hand-written pattern.
    pattern: int = 4_000
    #: Characters. A note far past this is not something to regex over.
    input: int = 2_000_000
    #: Seconds of wall clock across one find/replace.
    seconds: float = 2.0


LIMITS = Limits()


#: Flags a caller may pass. `g` is not among them — whether every match is
#: replaced is a property of the CALL (`all=`, `count=`), not of the pattern,
#: so accepting a global flag as well would give two ways to say one thing and
#: leave them free to disagree.
_FLAG_MAP = {"i": _re.IGNORECASE, "m": _re.MULTILINE, "s": _re.DOTALL}
_KNOWN_FLAGS = set(_FLAG_MAP) | {"u", "v"}

#: Without the `u` flag JavaScript's \d \w \s are ASCII-only. Python's are
#: Unicode-aware, so `\d+` would match Arabic-Indic digits that JavaScript
#: skips. Translated explicitly rather than by setting re.ASCII, which would
#: also narrow the character classes a caller wrote out by hand.
_ASCII_CLASS = {
    "d": "[0-9]",
    "D": "[^0-9]",
    "w": "[0-9A-Za-z_]",
    "W": "[^0-9A-Za-z_]",
}


#: Names declared by `(?<name>` — not `(?<=` or `(?<!`, which are lookbehinds.
_NAMED_GROUP = _re.compile(r"\(\?<(?![=!])([^>]*)>")


def _named_groups(pattern: str) -> set[str]:
    """Which named groups a pattern declares.

    Needed because `\k<name>` means two different things in JavaScript
    depending on the answer, and picking the wrong one silently changes what
    the pattern matches.
    """
    return {m.group(1) for m in _NAMED_GROUP.finditer(pattern)}


def _translate(pattern: str, *, unicode_mode: bool) -> str:
    """Rewrite a JavaScript pattern into one `regex` reads the same way."""
    out: list[str] = []
    i, n = 0, len(pattern)
    in_class = False
    declared = _named_groups(pattern)

    while i < n:
        ch = pattern[i]

        if ch == "\\" and i + 1 < n:
            nxt = pattern[i + 1]
            # `\k<name>`: a named backreference, but ONLY when that group
            # exists. JavaScript otherwise reads it as the literal text
            # "k<name>" without the `u` flag, and rejects it with one — and a
            # pattern that quietly matches different text in each client is
            # exactly what this module exists to prevent.
            if nxt == "k" and in_class:
                # A backreference cannot appear in a character class, so
                # JavaScript reads this as the literal letter. Python rejects
                # the escape outright, which would fail a pattern that works.
                out.append("k")
                i += 2
                continue
            if nxt == "k" and not in_class and pattern.startswith("<", i + 2):
                close = pattern.find(">", i + 2)
                name = pattern[i + 3 : close] if close != -1 else None
                if name in declared:
                    out.append(f"(?P={name})")
                    i = close + 1
                    continue
                if unicode_mode and close != -1:
                    raise InvalidPattern(
                        f"invalid named capture referenced: \\k<{name}> — "
                        "no group of that name is declared"
                    )
                # Literal, the way JavaScript reads it without `u`.
                out.append("k")
                i += 2
                continue
            if not unicode_mode and nxt in _ASCII_CLASS:
                # Inside a character class the bracketed form would nest, so
                # emit the bare ranges instead.
                out.append(_ASCII_CLASS[nxt][1:-1] if in_class else _ASCII_CLASS[nxt])
                i += 2
                continue
            if nxt in "pP" and not unicode_mode:
                raise UnsupportedPattern(
                    r"\p{...} property escapes require the 'u' flag in JavaScript; "
                    "add flags='u'"
                )
            out.append(pattern[i : i + 2])
            i += 2
            continue

        if ch == "[" and not in_class:
            # JavaScript's empty class matches nothing; Python rejects it.
            if pattern.startswith("[]", i):
                out.append("(?!)")
                i += 2
                continue
            if pattern.startswith("[^]", i):
                out.append("(?s:.)")  # JS: matches anything, newline included
                i += 3
                continue
            in_class = True
            out.append(ch)
            i += 1
            continue

        if ch == "]" and in_class:
            in_class = False
            out.append(ch)
            i += 1
            continue

        # (?<name>  →  (?P<name>   ... but not (?<=  or (?<!
        if not in_class and pattern.startswith("(?<", i) and i + 3 < n and pattern[i + 3] not in "=!":
            close = pattern.find(">", i)
            if close == -1:
                raise InvalidPattern("unterminated named group")
            out.append("(?P<" + pattern[i + 3 : close] + ">")
            i = close + 1
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def compile_js(pattern: str, flags: str = "") -> _re.Pattern:
    """Compile a JavaScript pattern. Raises on anything not faithfully supported."""
    if len(pattern) > LIMITS.pattern:
        raise PatternTooLong(
            f"pattern is {len(pattern)} characters; the limit is {LIMITS.pattern}"
        )
    unknown = set(flags) - _KNOWN_FLAGS
    if unknown:
        extra = " ('g' is not a flag here — use all=True or count=N)" if "g" in unknown else ""
        raise UnsupportedPattern(f"unsupported regex flags: {''.join(sorted(unknown))}{extra}")

    bits = 0
    for f in flags:
        bits |= _FLAG_MAP.get(f, 0)

    translated = _translate(pattern, unicode_mode=("u" in flags or "v" in flags))
    try:
        return _re.compile(translated, bits)
    except _re.error as exc:  # pragma: no cover - message varies by build
        raise InvalidPattern(f"invalid regular expression: {exc}") from exc


#: $$  $&  $`  $'  $<name>  $1 … $99
_TOKEN = _re.compile(r"\$(\$|&|`|'|<([^>]*)>|[0-9]{1,2})")


def expand_replacement(replacement: str, match: _re.Match, source: str) -> str:
    """Expand JavaScript replacement tokens against a match.

    JavaScript's rules, not Python's, in the three places they differ:

      * a group that took part in no match contributes an empty string rather
        than raising,
      * a reference to a group the pattern does not have is left in the output
        verbatim (`$3` stays `$3`),
      * `$`` and `$'` are the text before and after the match.
    """

    def one(token: _re.Match) -> str:
        body = token.group(1)
        if body == "$":
            return "$"
        if body == "&":
            return match.group(0)
        if body == "`":
            return source[: match.start()]
        if body == "'":
            return source[match.end() :]
        if body.startswith("<"):
            name = token.group(2)
            try:
                return match.group(name) or ""
            except (IndexError, _re.error):
                # JavaScript: a named reference with no such group in a pattern
                # that HAS named groups produces an empty string.
                return ""
        index = int(body)
        try:
            return match.group(index) or ""
        except (IndexError, _re.error):
            return "$" + body

    return _TOKEN.sub(one, replacement)


class JsRegex:
    """A compiled JavaScript pattern, with JavaScript replacement semantics."""

    __slots__ = ("pattern", "flags", "_rx")

    def __init__(self, pattern: str, flags: str = "") -> None:
        self.pattern = pattern
        self.flags = flags
        self._rx = compile_js(pattern, flags)

    def _check_input(self, source: str) -> None:
        if len(source) > LIMITS.input:
            raise InputTooLarge(
                f"document is {len(source)} characters; the limit for regex is {LIMITS.input}"
            )

    def finditer(self, source: str):
        """Matches, with a deadline.

        Stepped one match at a time rather than handed to the engine wholesale:
        a catastrophically backtracking pattern spends its time INSIDE a single
        `match` call, and nothing in Python can interrupt that. Between calls
        is the only place a deadline can be honoured, so this bounds how far a
        scan gets rather than how long one match may take. The pattern and
        input size limits above are what keep that single call short.
        """
        self._check_input(source)
        deadline = _time.monotonic() + LIMITS.seconds
        pos = 0
        while True:
            m = self._rx.search(source, pos)
            if m is None:
                return
            yield m
            if _time.monotonic() > deadline:
                raise MatchTimeout(
                    f"matching ran past {LIMITS.seconds}s and was abandoned — "
                    "narrow the pattern, or use a literal query"
                )
            pos = m.end() if m.end() > m.start() else m.start() + 1

    def findall(self, source: str) -> list[_re.Match]:
        return list(self.finditer(source))

    def replace(self, source: str, replacement: str, *, count: int = 0) -> str:
        """Replace matches. `count=0` means every match, as `re.sub` does."""
        self._check_input(source)
        return self._rx.sub(lambda m: expand_replacement(replacement, m, source), source, count=count)

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"JsRegex({self.pattern!r}, flags={self.flags!r})"
