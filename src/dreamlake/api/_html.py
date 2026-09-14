"""CSS selection over HTML source, with edits that land on the source.

The constraint that shapes all of this: an edit must change the bytes it
addresses and leave every other byte alone. Parsing into a tree and serialising
it back would normalise attribute quoting, reorder nothing but respell
everything, and turn `&amp;` into `&` or `&#38;` depending on the library's
mood — a one-word edit would arrive as a diff touching the whole file, and a
reviewer could not see what actually changed.

So nothing is rebuilt. The parser records where each tag, text node and entity
began and ended in the ORIGINAL string, and an edit is a splice into that
string.

Two consequences worth stating:

  * Text edits operate on decoded descendant text, but land on raw source. An
    element containing `a &amp; b` reads as `a & b`; replacing `b` rewrites
    four characters and leaves the entity spelled as it was.
  * Text nodes are not joined across markup. `<b>a</b>b` has two of them, and a
    query for `ab` finds nothing rather than matching across a tag boundary and
    producing an edit that cannot be expressed.

Only the selector subset that is unambiguous to implement is supported:
tag, `#id`, `.class`, `[attr]`, `[attr=value]`, descendant and child
combinators, and `:nth-of-type(n)`. Anything else raises rather than silently
matching something adjacent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

__all__ = [
    "SelectorError",
    "NoElement",
    "AmbiguousElement",
    "Element",
    "parse",
    "select",
    "select_all",
]

#: Elements HTML5 says never have a closing tag.
_VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


class SelectorError(ValueError):
    """The selector cannot be parsed, or uses something unsupported."""


class NoElement(ValueError):
    """Nothing matched the selector."""


class AmbiguousElement(ValueError):
    """More than one element matched where exactly one was needed."""


@dataclass
class TextRun:
    """A run of text inside an element, and where it came from.

    `decoded` is what a reader sees; `spans` maps each decoded character back to
    its source range, which is how an edit on decoded text becomes an edit on
    raw source without disturbing entity spelling.
    """

    decoded: str
    spans: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class Element:
    tag: str
    attrs: dict[str, str | None]
    #: Source range of the whole element, open tag through close tag.
    outer: tuple[int, int]
    #: Source range between the tags. Equal endpoints for a void element.
    inner: tuple[int, int]
    #: Source range of the open tag alone, for attribute edits.
    start_tag: tuple[int, int]
    depth: int
    index_of_type: int
    parent: "Element | None" = None
    children: list["Element"] = field(default_factory=list)

    def __repr__(self) -> str:  # pragma: no cover - display only
        ident = f"#{self.attrs['id']}" if self.attrs.get("id") else ""
        return f"<{self.tag}{ident} inner={self.inner}>"


class _Collector(HTMLParser):
    def __init__(self, source: str) -> None:
        # Entities stay as separate events so their original spelling survives.
        super().__init__(convert_charrefs=False)
        self.source = source
        self._line_starts = [0]
        for i, ch in enumerate(source):
            if ch == "\n":
                self._line_starts.append(i + 1)
        self.elements: list[Element] = []
        self._open: list[Element] = []
        self._type_counts: list[dict[str, int]] = [{}]
        #: (element, decoded text, source span) for every text/entity event.
        self.text_events: list[tuple[Element | None, str, tuple[int, int]]] = []

    def _pos(self) -> int:
        line, col = self.getpos()
        return self._line_starts[line - 1] + col

    def _push_text(self, decoded: str, raw_len: int) -> None:
        start = self._pos()
        span = (start, start + raw_len)
        self.text_events.append((self._open[-1] if self._open else None, decoded, span))

    # ── parser callbacks ────────────────────────────────────────────────────

    def handle_starttag(self, tag, attrs):
        start = self._pos()
        raw = self.get_starttag_text() or f"<{tag}>"
        end = start + len(raw)
        counts = self._type_counts[-1]
        counts[tag] = counts.get(tag, 0) + 1
        el = Element(
            tag=tag,
            attrs=dict(attrs),
            outer=(start, end),
            inner=(end, end),
            start_tag=(start, end),
            depth=len(self._open),
            index_of_type=counts[tag],
            parent=self._open[-1] if self._open else None,
        )
        if el.parent is not None:
            el.parent.children.append(el)
        self.elements.append(el)
        if tag in _VOID:
            return
        self._open.append(el)
        self._type_counts.append({})

    def handle_startendtag(self, tag, attrs):
        start = self._pos()
        raw = self.get_starttag_text() or f"<{tag}/>"
        end = start + len(raw)
        counts = self._type_counts[-1]
        counts[tag] = counts.get(tag, 0) + 1
        el = Element(tag, dict(attrs), (start, end), (end, end), (start, end),
                     len(self._open), counts[tag], self._open[-1] if self._open else None)
        if el.parent is not None:
            el.parent.children.append(el)
        self.elements.append(el)

    def handle_endtag(self, tag):
        for i in range(len(self._open) - 1, -1, -1):
            if self._open[i].tag == tag:
                el = self._open[i]
                close_start = self._pos()
                el.inner = (el.start_tag[1], close_start)
                el.outer = (el.outer[0], close_start + len(f"</{tag}>"))
                del self._open[i:]
                del self._type_counts[i + 1 :]
                return

    def handle_data(self, data):
        self._push_text(data, len(data))

    def handle_entityref(self, name):
        import html as _h

        self._push_text(_h.unescape(f"&{name};"), len(name) + 2)

    def handle_charref(self, name):
        import html as _h

        self._push_text(_h.unescape(f"&#{name};"), len(name) + 3)


@dataclass
class Document:
    source: str
    elements: list[Element]
    text_events: list[tuple[Element | None, str, tuple[int, int]]]


def parse(source: str) -> Document:
    c = _Collector(source)
    c.feed(source)
    c.close()
    # An element left open at EOF runs to the end of the document, which is
    # what a browser does with unterminated markup.
    for el in c.elements:
        if el.inner[0] == el.inner[1] and el.tag not in _VOID and el.outer[1] == el.start_tag[1]:
            el.inner = (el.start_tag[1], len(source))
            el.outer = (el.outer[0], len(source))
    return Document(source, c.elements, c.text_events)


# ── Selectors ────────────────────────────────────────────────────────────────

_SIMPLE = re.compile(
    r"""
    (?P<tag>[A-Za-z][\w-]*|\*)?
    (?P<rest>(?:
        \#[\w-]+
      | \.[\w-]+
      | \[[^\]]+\]
      | :nth-of-type\(\d+\)
    )*)
    """,
    re.X,
)
_PIECE = re.compile(r"\#([\w-]+)|\.([\w-]+)|\[([^\]]+)\]|:nth-of-type\((\d+)\)")
_ATTR = re.compile(r"^\s*([\w-]+)\s*(?:([~|^$*]?=)\s*(.*?)\s*)?$")


@dataclass
class _Step:
    tag: str | None
    ids: list[str]
    classes: list[str]
    attrs: list[tuple[str, str | None, str | None]]
    nth: int | None
    combinator: str  # ' ' descendant, '>' child


def _parse_selector(sel: str) -> list[_Step]:
    if not sel.strip():
        raise SelectorError("empty selector")
    if "," in sel:
        raise SelectorError("selector lists (',') are not supported; select one element")
    tokens = re.split(r"\s*(>)\s*|\s+", sel.strip())
    tokens = [t for t in tokens if t]

    steps: list[_Step] = []
    combinator = " "
    for tok in tokens:
        if tok == ">":
            combinator = ">"
            continue
        m = _SIMPLE.fullmatch(tok)
        if not m or (not m.group("tag") and not m.group("rest")):
            raise SelectorError(f"unsupported selector fragment: {tok!r}")
        ids, classes, attrs, nth = [], [], [], None
        for p in _PIECE.finditer(m.group("rest") or ""):
            if p.group(1):
                ids.append(p.group(1))
            elif p.group(2):
                classes.append(p.group(2))
            elif p.group(3):
                a = _ATTR.match(p.group(3))
                if not a:
                    raise SelectorError(f"unsupported attribute selector: [{p.group(3)}]")
                op = a.group(2)
                if op not in (None, "="):
                    raise SelectorError(
                        f"attribute operator {op!r} is not supported; use [attr] or [attr=value]"
                    )
                val = a.group(3)
                if val is not None:
                    val = val.strip("\"'")
                attrs.append((a.group(1), op, val))
            elif p.group(4):
                nth = int(p.group(4))
        tag = m.group("tag")
        steps.append(_Step(None if tag in (None, "*") else tag.lower(), ids, classes, attrs, nth, combinator))
        combinator = " "
    return steps


def _matches(el: Element, step: _Step) -> bool:
    if step.tag and el.tag != step.tag:
        return False
    for want in step.ids:
        if el.attrs.get("id") != want:
            return False
    if step.classes:
        have = set((el.attrs.get("class") or "").split())
        if not set(step.classes) <= have:
            return False
    for name, op, val in step.attrs:
        if name not in el.attrs:
            return False
        if op == "=" and el.attrs.get(name) != val:
            return False
    if step.nth is not None and el.index_of_type != step.nth:
        return False
    return True


def _ancestors(el: Element):
    cur = el.parent
    while cur is not None:
        yield cur
        cur = cur.parent


def select_all(doc: Document, selector: str, *, within: Element | None = None) -> list[Element]:
    """Every element matching the selector, in document order."""
    steps = _parse_selector(selector)
    last = steps[-1]
    out: list[Element] = []
    for el in doc.elements:
        if within is not None and within not in list(_ancestors(el)):
            continue
        if not _matches(el, last):
            continue
        cur = el
        ok = True
        for step in reversed(steps[:-1]):
            if step.combinator == ">" or (steps[steps.index(step) + 1].combinator == ">"):
                parent = cur.parent
                if parent is None or not _matches(parent, step):
                    ok = False
                    break
                cur = parent
            else:
                found = None
                for anc in _ancestors(cur):
                    if _matches(anc, step):
                        found = anc
                        break
                if found is None:
                    ok = False
                    break
                cur = found
        if ok:
            out.append(el)
    return out


def select(doc: Document, selector: str, *, within: Element | None = None) -> Element:
    """Exactly one element, or a refusal saying which way it went wrong."""
    found = select_all(doc, selector, within=within)
    if not found:
        raise NoElement(f"no element matches {selector!r}")
    if len(found) > 1:
        where = ", ".join(repr(e) for e in found[:4])
        more = "" if len(found) <= 4 else f" (and {len(found) - 4} more)"
        raise AmbiguousElement(
            f"{len(found)} elements match {selector!r}: {where}{more} — "
            "narrow it, or use :nth-of-type(n)"
        )
    return found[0]


# ── Text inside an element ───────────────────────────────────────────────────


def text_runs(doc: Document, el: Element) -> list[TextRun]:
    """Decoded text nodes under an element, each mapped back to source.

    One run per text node. They are NOT concatenated: a query is not allowed to
    match across a tag boundary, because the resulting edit could not be
    expressed as a change to one span of source.
    """
    runs: list[TextRun] = []
    lo, hi = el.inner
    pending: TextRun | None = None
    last_end: int | None = None

    for owner, decoded, span in doc.text_events:
        if not (lo <= span[0] and span[1] <= hi):
            continue
        # Entities arrive as their own events; join them onto the adjacent text
        # so `a &amp; b` reads as one run, but keep each character's own span.
        if pending is not None and last_end == span[0]:
            pending.decoded += decoded
        else:
            pending = TextRun(decoded)
            runs.append(pending)
        # One span per decoded character, so a slice of decoded text maps back
        # exactly even when an entity stands for a single character.
        if len(decoded) == 1:
            pending.spans.append(span)
        else:
            width = (span[1] - span[0]) / max(len(decoded), 1)
            for i in range(len(decoded)):
                pending.spans.append(
                    (span[0] + int(i * width), span[0] + int((i + 1) * width))
                )
        last_end = span[1]

    return runs
