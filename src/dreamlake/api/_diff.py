"""Single-document unified diffs, with exact context and atomic application."""

import difflib
import re

from ._editing import EditError


def _lines(text: str) -> list[str]:
    # Unified diffs delimit lines with LF, not every Unicode line separator.
    parts = text.split("\n")
    return [part + "\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def unified_diff(before: str, after: str, label: str, context: int) -> str:
    result = []
    for line in difflib.unified_diff(
        _lines(before), _lines(after), fromfile=f"a/{label}", tofile=f"b/{label}", n=context
    ):
        result.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(result)


_HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:[^\n]*)\n?$")


def apply_diff(text: str, diff: str) -> str:
    if not diff:
        return text
    lines = _lines(diff)
    # Accept the usual git/jsdiff preamble, but only one file.
    i = 0
    while i < len(lines) and lines[i].startswith(("diff --git ", "index ", "Index: ", "====")):
        i += 1
    if i < len(lines) and lines[i].startswith("--- "):
        i += 1
        if i >= len(lines) or not lines[i].startswith("+++ "):
            raise EditError("missing unified diff target header")
        i += 1
    source = _lines(text)
    result: list[str] = []
    cursor = 0
    hunks = 0
    while i < len(lines):
        match = _HUNK.fullmatch(lines[i])
        if match is None:
            raise EditError("expected a unified diff hunk (one file only)")
        old_start, old_count, new_start, new_count = (
            int(value) if value is not None else 1 for value in match.groups()
        )
        old_at = old_start - 1 if old_count else old_start
        new_at = new_start - 1 if new_count else new_start
        if old_at < cursor or old_at > len(source):
            raise EditError("diff hunk is outside the draft or overlaps an earlier hunk")
        result.extend(source[cursor:old_at])
        if new_at != len(result):
            raise EditError("inconsistent unified diff target position")
        cursor = old_at
        i += 1
        old_seen = new_seen = 0
        while old_seen < old_count or new_seen < new_count:
            if i >= len(lines) or lines[i][:1] not in (" ", "+", "-"):
                raise EditError("incomplete unified diff hunk")
            op, value = lines[i][0], lines[i][1:]
            if not value.endswith("\n"):
                raise EditError("unterminated diff line; use the no-newline marker")
            i += 1
            if i < len(lines) and lines[i].rstrip("\n") == "\\ No newline at end of file":
                value = value[:-1]
                i += 1
            if op != "+":
                if cursor >= len(source) or source[cursor] != value:
                    raise EditError("diff context does not match the current draft")
                cursor += 1
                old_seen += 1
            if op != "-":
                result.append(value)
                new_seen += 1
            if old_seen > old_count or new_seen > new_count:
                raise EditError("unified diff hunk counts do not match")
        hunks += 1
    if not hunks:
        raise EditError("diff contains no hunks")
    result.extend(source[cursor:])
    if any(not line.endswith("\n") for line in result[:-1]):
        raise EditError("no-newline marker occurs before the end of the document")
    return "".join(result)
