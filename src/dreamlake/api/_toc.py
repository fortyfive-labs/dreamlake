"""Outlines, with both kinds of range.

A TOC entry is how an agent addresses a section without counting lines, so the
anchors have to be the SAME anchors the server and the browser produce. This is
a port of dreamlake-server's services/notesSections.ts, which is itself a port
of the browser's lib/notes/outline.ts — three implementations of one rule,
which is a hazard, but the alternative is a round-trip for something that is a
pure function of text the client already has.

The parts that are easy to get subtly wrong, and why each matters:

  * Fenced code is tracked. A `# comment` inside a shell example is not a
    heading, and treating it as one would let an agent address — and overwrite
    — a chunk of somebody's script.
  * Repeated titles get `-2`, `-3` suffixes in document order, so an anchor
    stays put as long as the headings before it do not change.
  * A section's range covers the heading AND its whole subtree, because
    "replace this section" means the part a reader would say it is.
  * Text before the first heading is addressable as `preamble`; otherwise the
    top of a document is the one part with no name.

Ranges come in both units the API uses: 1-based inclusive lines, and 0-based
end-exclusive code points.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from ._editing import line_of

__all__ = ["TocEntry", "toc", "markdown_toc", "html_toc", "slugify", "PREAMBLE_ANCHOR"]

PREAMBLE_ANCHOR = "preamble"


@dataclass(frozen=True)
class TocEntry:
    anchor: str
    title: str
    level: int
    #: 1-based, inclusive.
    line: tuple[int, int]
    #: 0-based, end-exclusive code points.
    ind: tuple[int, int]

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"TocEntry({self.anchor!r}, level={self.level}, line={self.line})"


_INLINE = [
    (re.compile(r"`([^`]+)`"), r"\1"),
    (re.compile(r"\*\*([^*]+)\*\*"), r"\1"),
    (re.compile(r"\*([^*]+)\*"), r"\1"),
    (re.compile(r"__([^_]+)__"), r"\1"),
    (re.compile(r"_([^_]+)_"), r"\1"),
    (re.compile(r"~~([^~]+)~~"), r"\1"),
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),
    (re.compile(r"\\([\\`*_{}\[\]()#+\-.!~])"), r"\1"),
]


def _strip_inline(s: str) -> str:
    """Read a title as plain text, so it slugs the way the server slugs it."""
    for rx, repl in _INLINE:
        s = rx.sub(repl, s)
    return s.strip()


def slugify(title: str) -> str:
    """GitHub-style slug. Empty or all-punctuation titles fall back to 'section'."""
    out = []
    for ch in title.lower():
        cat = unicodedata.category(ch)
        if cat[0] in ("L", "N") or ch in " -":
            out.append(ch)
    s = re.sub(r"\s+", "-", "".join(out).strip())
    return s or "section"


_ATX = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^(```|~~~)")
_BULLET = re.compile(r"^[-*+]\s")


@dataclass(frozen=True)
class _Heading:
    level: int
    text: str
    ind: int
    line: int


def _headings(md: str) -> list[_Heading]:
    out: list[_Heading] = []
    lines = md.split("\n")
    off = 0
    in_fence = False
    for i, line in enumerate(lines):
        trimmed = line.strip()
        if _FENCE.match(trimmed):
            in_fence = not in_fence
            off += len(line) + 1
            continue
        if not in_fence:
            atx = _ATX.match(line)
            if atx:
                out.append(_Heading(len(atx.group(1)), _strip_inline(atx.group(2)), off, i + 1))
            elif trimmed and not _BULLET.match(trimmed):
                nxt = (lines[i + 1] if i + 1 < len(lines) else "").strip()
                if re.fullmatch(r"=+", nxt or ""):
                    out.append(_Heading(1, _strip_inline(trimmed), off, i + 1))
                elif nxt and re.fullmatch(r"-+", nxt) and len(nxt) >= 2:
                    out.append(_Heading(2, _strip_inline(trimmed), off, i + 1))
        off += len(line) + 1
    return out


def _entry(md: str, anchor: str, title: str, level: int, start: int, end: int) -> TocEntry:
    # `end` is exclusive; the last line of the section is the one holding the
    # final character, not the one after it.
    last = line_of(md, max(start, end - 1)) if end > start else line_of(md, start)
    return TocEntry(anchor, title, level, (line_of(md, start), last), (start, end))


def markdown_toc(md: str) -> list[TocEntry]:
    """Sections in document order; each range covers its heading and subtree."""
    heads = _headings(md)
    out: list[TocEntry] = []

    if not heads:
        if md:
            out.append(_entry(md, PREAMBLE_ANCHOR, "", 0, 0, len(md)))
        return out

    if heads[0].ind > 0:
        out.append(_entry(md, PREAMBLE_ANCHOR, "", 0, 0, heads[0].ind))

    seen: dict[str, int] = {}
    for i, h in enumerate(heads):
        # The subtree ends at the next heading of the same level or shallower;
        # a deeper heading is part of this section, not the start of the next.
        end = len(md)
        for later in heads[i + 1 :]:
            if later.level <= h.level:
                end = later.ind
                break
        base = slugify(h.text)
        seen[base] = seen.get(base, 0) + 1
        n = seen[base]
        out.append(_entry(md, base if n == 1 else f"{base}-{n}", h.text, h.level, h.ind, end))
    return out


_HTML_HEADING = re.compile(
    r"<(h[1-6])(\s[^>]*)?>(.*?)</\1\s*>", re.IGNORECASE | re.DOTALL
)
_TAGS = re.compile(r"<[^>]+>")


def html_toc(html: str) -> list[TocEntry]:
    """Headings and where they are in the source.

    Deliberately NOT Markdown's subtree rule: an `<h2>` in HTML does not own
    the markup that follows it, and inventing a section boundary would let an
    agent replace a "section" that no element corresponds to. Each entry spans
    the heading element itself.
    """
    out: list[TocEntry] = []
    seen: dict[str, int] = {}
    for m in _HTML_HEADING.finditer(html):
        level = int(m.group(1)[1])
        title = _strip_inline(_TAGS.sub("", m.group(3))).strip()
        base = slugify(title)
        seen[base] = seen.get(base, 0) + 1
        n = seen[base]
        anchor = base if n == 1 else f"{base}-{n}"
        out.append(_entry(html, anchor, title, level, m.start(), m.end()))
    return out


def toc(source: str, *, html: bool | None = None) -> list[TocEntry]:
    """Outline for a document. Sniffs HTML unless told."""
    if html is None:
        head = source.lstrip()[:512].lower()
        html = head.startswith("<!doctype html") or head.startswith("<html") or bool(
            re.match(r"<(h[1-6]|div|section|article|body|head)\b", head)
        )
    return html_toc(source) if html else markdown_toc(source)
