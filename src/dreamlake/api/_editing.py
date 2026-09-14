"""Locating and changing text in a document snapshot.

This is the shared meaning of an edit, kept apart from both the SDK's network
calls and the CLI's argument parsing so the two cannot drift. Everything here
is pure: it takes source in and gives source out, and raises rather than
guessing.

Three ways to say where:

    query / regex   the text itself, which is what an agent can state reliably
    line=           1-based, inclusive — what a reader and a TOC entry use
    ind=            0-based, end-exclusive Unicode code points — what a parser
                    produces, and what slicing a Python string already means

Only one of them per call. Mixing them is rejected rather than ranked, because
a caller who supplied two meant one of them and we cannot tell which.

Match counting is strict on purpose. One match is required by default, `count=N`
requires exactly N, and `all=True` takes every match; zero is always an error.
The alternative — quietly editing the first of several — is the failure an
agent cannot see and a reviewer cannot spot in a diff.

Counts are checked BEFORE anything is written, and every replacement is
computed against the same pre-edit snapshot, so a failed edit leaves the draft
exactly as it was and a replacement can never match text another replacement
just inserted.
"""

from __future__ import annotations

from dataclasses import dataclass

from ._jsregex import InvalidPattern, JsRegex, UnsupportedPattern

__all__ = [
    "EditError",
    "NoMatch",
    "AmbiguousMatch",
    "InvalidRange",
    "BadPattern",
    "Match",
    "find",
    "replace",
    "insert",
    "delete",
    "line_span",
    "line_of",
]


class EditError(ValueError):
    """Base for every refusal in this module.

    A bad pattern is one of them. `_jsregex` raises its own types, but a caller
    is working with an EDIT and should be able to catch one thing — and the
    CLI, which has no separate regex module, raises its EditError for the same
    inputs. The shared fixtures pin that agreement.
    """


class NoMatch(EditError):
    """Nothing matched, and an edit needs something to act on."""


class AmbiguousMatch(EditError):
    """More matches than the caller said they expected."""


class InvalidRange(EditError):
    """A line or character range that cannot address anything."""


class BadPattern(EditError):
    """The regex is invalid, or uses something the shared dialect excludes."""


@dataclass(frozen=True)
class Match:
    """One hit, described the way every other API here describes a location."""

    text: str
    #: 0-based, end-exclusive code points.
    ind: tuple[int, int]
    #: 1-based, inclusive.
    line: tuple[int, int]
    #: Present for regex matches: group name or number → text (None if the
    #: group took part in no match, which JavaScript renders as empty).
    captures: dict[str | int, str | None] | None = None
    #: Source span of each capture, or None where it did not participate.
    capture_spans: dict[str | int, tuple[int, int] | None] | None = None

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"Match({self.text!r}, line={self.line}, ind={self.ind})"


# ── Line arithmetic ──────────────────────────────────────────────────────────
#
# Lines are counted by '\n'. A '\r' is left inside the line's text, so CRLF
# sources keep their bytes and a replacement that ends with '\n' does not
# quietly convert the file's convention.


def _line_starts(source: str) -> list[int]:
    starts = [0]
    for i, ch in enumerate(source):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def line_of(source: str, ind: int) -> int:
    """The 1-based line containing a character index."""
    starts = _line_starts(source)
    lo, hi = 0, len(starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if starts[mid] <= ind:
            lo = mid
        else:
            hi = mid - 1
    return lo + 1


def line_span(source: str, first: int, last: int) -> tuple[int, int]:
    """Character range covering lines `first`..`last`, inclusive, with their newline."""
    starts = _line_starts(source)
    total = len(starts)
    if first < 1 or last < first:
        raise InvalidRange(f"line range ({first}, {last}) is not a range; lines are 1-based and inclusive")
    if first > total:
        raise InvalidRange(f"line {first} is past the end of a {total}-line document")
    last = min(last, total)
    start = starts[first - 1]
    end = starts[last] if last < total else len(source)
    return start, end


def _newline_style(source: str) -> str:
    """The document's own line ending, so an edit does not change it."""
    return "\r\n" if "\r\n" in source else "\n"


def _ends_line(text: str) -> bool:
    return text.endswith("\n")


# ── Finding ──────────────────────────────────────────────────────────────────


def _spans_to_match(source: str, start: int, end: int, m=None) -> Match:
    captures = capture_spans = None
    if m is not None and m.re.groups + len(m.re.groupindex) > 0:
        captures, capture_spans = {}, {}
        for i in range(1, m.re.groups + 1):
            captures[i] = m.group(i)
            captures[i] = m.group(i)
            span = m.span(i)
            capture_spans[i] = None if span == (-1, -1) else span
        for name in m.re.groupindex:
            captures[name] = m.group(name)
            span = m.span(name)
            capture_spans[name] = None if span == (-1, -1) else span
    return Match(
        text=source[start:end],
        ind=(start, end),
        line=(line_of(source, start), line_of(source, max(start, end - 1))),
        captures=captures,
        capture_spans=capture_spans,
    )


def find(
    source: str,
    query: str | None = None,
    *,
    regex: str | None = None,
    flags: str = "",
    limit: int = 1000,
) -> list[Match]:
    """Every match in source order. Literal by default; `regex=` opts in.

    `limit` bounds the result rather than the search: an unbounded list from a
    pattern like `.*` is a denial of service against whatever renders it.
    """
    if (query is None) == (regex is None):
        raise EditError("give exactly one of query or regex")

    out: list[Match] = []
    if query is not None:
        if query == "":
            raise EditError("an empty query matches everywhere; say what to look for")
        start = 0
        while len(out) < limit:
            i = source.find(query, start)
            if i < 0:
                break
            out.append(_spans_to_match(source, i, i + len(query)))
            start = i + len(query)
        return out

    try:
        rx = JsRegex(regex, flags)
    except (InvalidPattern, UnsupportedPattern) as exc:
        raise BadPattern(str(exc)) from exc
    for m in rx.finditer(source):
        if len(out) >= limit:
            break
        out.append(_spans_to_match(source, m.start(), m.end(), m))
    return out


# ── Targeting ────────────────────────────────────────────────────────────────


def _resolve_span(
    source: str,
    *,
    line: int | tuple[int, int] | None,
    ind: int | tuple[int, int] | None,
) -> tuple[int, int]:
    if line is not None:
        first, last = (line, line) if isinstance(line, int) else line
        return line_span(source, first, last)
    start, end = (ind, ind) if isinstance(ind, int) else ind
    n = len(source)
    if start < 0 or end < start:
        raise InvalidRange(
            f"character range ({start}, {end}) is not a range; indices are 0-based, end-exclusive code points"
        )
    if end > n:
        raise InvalidRange(f"character index {end} is past the end of a {n}-character document")
    return start, end


def _check_targets(query, regex, line, ind) -> str:
    given = [n for n, v in (("query", query), ("regex", regex), ("line", line), ("ind", ind)) if v is not None]
    if not given:
        raise EditError("say where: query=, regex=, line= or ind=")
    if len(given) > 1:
        raise EditError(f"conflicting targets: {', '.join(given)} — give one")
    return given[0]


def _select_matches(matches: list[Match], *, count: int | None, all_: bool, what: str) -> list[Match]:
    if all_ and count is not None:
        raise EditError("count and all say different things; give one")
    n = len(matches)
    if n == 0:
        raise NoMatch(f"{what} matched nothing")
    if all_:
        return matches
    wanted = 1 if count is None else count
    if wanted < 1:
        raise EditError(f"count must be at least 1, got {wanted}")
    if n != wanted:
        raise AmbiguousMatch(
            f"expected {wanted} match{'' if wanted == 1 else 'es'}, found {n} — "
            f"narrow the query, or pass all=True or count={n}"
        )
    return matches


def _splice(source: str, edits: list[tuple[int, int, str]]) -> str:
    """Apply non-overlapping (start, end, text) edits computed on one snapshot."""
    for (a, b, _), (c, _d, _t) in zip(edits, edits[1:]):
        if c < b:
            raise EditError("overlapping edits")
    out, last = [], 0
    for start, end, text in edits:
        out.append(source[last:start])
        out.append(text)
        last = end
    out.append(source[last:])
    return "".join(out)


# ── Edits ────────────────────────────────────────────────────────────────────


def replace(
    source: str,
    text: str,
    *,
    query: str | None = None,
    regex: str | None = None,
    flags: str = "",
    line: int | tuple[int, int] | None = None,
    ind: int | tuple[int, int] | None = None,
    count: int | None = None,
    all: bool = False,  # noqa: A002 - the issue's spelling
) -> str:
    """Replace the addressed text. Returns the whole updated source."""
    mode = _check_targets(query, regex, line, ind)

    if mode in ("line", "ind"):
        if count is not None or all:
            raise EditError("count and all apply to query/regex matching, not to a range")
        start, end = _resolve_span(source, line=line, ind=ind)
        body = text
        if mode == "line" and not _ends_line(body):
            # A line edit yields whole lines, including at end of file — but
            # never two terminators where the caller already wrote one.
            body += _newline_style(source)
        return source[:start] + body + source[end:]

    matches = find(source, query, regex=regex, flags=flags)
    chosen = _select_matches(matches, count=count, all_=all, what="query" if query else "regex")

    if regex is not None:
        try:
            rx = JsRegex(regex, flags)
        except (InvalidPattern, UnsupportedPattern) as exc:
            raise BadPattern(str(exc)) from exc
        # Every replacement is expanded against the ORIGINAL source, so an
        # expansion can never see text a previous replacement introduced.
        edits = []
        for m, raw in zip(chosen, rx.finditer(source)):
            if (raw.start(), raw.end()) != m.ind:
                continue
            if raw.start() == raw.end():
                raise EditError(
                    "zero-width regex match: use insert(line=/ind=) instead of replace"
                )
            from ._jsregex import expand_replacement

            edits.append((raw.start(), raw.end(), expand_replacement(text, raw, source)))
        return _splice(source, edits)

    return _splice(source, [(m.ind[0], m.ind[1], text) for m in chosen])


def insert(
    source: str,
    text: str,
    *,
    line: int | None = None,
    ind: int | None = None,
) -> str:
    """Insert before the addressed line, or at the character boundary."""
    if (line is None) == (ind is None):
        raise EditError("give exactly one of line or ind")

    if line is not None:
        if line < 1:
            raise InvalidRange(f"line {line} is not a line; lines are 1-based")
        starts = _line_starts(source)
        # Inserting at total+1 appends at end of file, which is the only way to
        # add a line after the last one.
        if line > len(starts) + 1:
            raise InvalidRange(f"line {line} is past the end of a {len(starts)}-line document")
        at = starts[line - 1] if line <= len(starts) else len(source)
        body = text if _ends_line(text) else text + _newline_style(source)
        if at == len(source) and source and not source.endswith(("\n", "\r")):
            # Appending past a file with no final newline: start a new line
            # rather than joining onto the last one.
            body = _newline_style(source) + body
        return source[:at] + body + source[at:]

    if ind < 0 or ind > len(source):
        raise InvalidRange(
            f"character index {ind} is outside 0..{len(source)}; insertion positions are inclusive of both ends"
        )
    return source[:ind] + text + source[ind:]


def delete(
    source: str,
    *,
    query: str | None = None,
    regex: str | None = None,
    flags: str = "",
    line: int | tuple[int, int] | None = None,
    ind: int | tuple[int, int] | None = None,
    count: int | None = None,
    all: bool = False,  # noqa: A002
) -> str:
    """Remove the addressed text. Returns the whole updated source."""
    mode = _check_targets(query, regex, line, ind)
    if mode in ("line", "ind"):
        if count is not None or all:
            raise EditError("count and all apply to query/regex matching, not to a range")
        start, end = _resolve_span(source, line=line, ind=ind)
        return source[:start] + source[end:]

    matches = find(source, query, regex=regex, flags=flags)
    chosen = _select_matches(matches, count=count, all_=all, what="query" if query else "regex")
    return _splice(source, [(m.ind[0], m.ind[1], "") for m in chosen])
