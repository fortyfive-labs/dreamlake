"""A note's source, loaded once, edited locally, saved as one patch.

    doc = dl.note("<uuid>").read()
    doc.replace("Published", query="Draft", all=True)
    doc.save()

The shape is deliberate. Reading and saving touch the network; everything
between them is a pure function of a string this process already has. So an
agent can look, edit, look again and diff without a round trip, and without any
of those steps quietly refetching and finding a different document underneath
it.

Every editing method returns the FULL updated source rather than a handle or a
count. That is what an agent can check: it asked for one change and can see the
whole result, including the parts it did not mean to touch. Element-scoped edits
return the whole document too, for the same reason — an element's own new inner
text says nothing about what happened around it.

`save()` sends one conditional patch against the revision the read returned. A
document that changed underneath is a refusal, never a retry: the edits were
computed against text that is no longer there, and applying them anyway is how
one agent silently reverts another.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import _editing as _ed
from . import _html as _h
from ._toc import TocEntry, toc as _toc

if TYPE_CHECKING:  # pragma: no cover
    from .notes import Note

__all__ = ["Doc", "Element", "SaveResult"]


@dataclass(frozen=True)
class SaveResult:
    """What the server recorded. Not the document — `doc.text` is that."""

    etag: str
    size_bytes: int

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"SaveResult(etag={self.etag!r}, size_bytes={self.size_bytes})"


class Element:
    """One selected HTML element, scoped to the document that produced it.

    Text operations here act on the element's own decoded text nodes — not on
    tag names, not on attribute values, and not across a child tag. Matches are
    mapped back to raw source, so the document keeps its entity spelling and
    attribute quoting exactly as written.
    """

    __slots__ = ("_doc", "_selector", "_el", "_epoch")

    def __init__(self, doc: "Doc", selector: str, el: _h.Element, epoch: int) -> None:
        self._doc = doc
        self._selector = selector
        self._el = el
        self._epoch = epoch

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"Element({self._selector!r}, {self._el.tag})"

    def _resolve(self) -> tuple[_h.Document, _h.Element]:
        """Re-resolve against the CURRENT draft, never against stale offsets.

        An edit since this handle was made may have moved or removed the
        element. Reusing the old ranges would write into whatever now occupies
        them — corruption that nothing downstream can detect — so the selector
        is re-run, and a handle whose target is gone says so.
        """
        parsed = _h.parse(self._doc.text)
        try:
            el = _h.select(parsed, self._selector)
        except (_h.NoElement, _h.AmbiguousElement) as exc:
            if self._epoch == self._doc._element_epoch:
                raise
            raise _h.NoElement(
                f"{self._selector!r} no longer resolves to one element after an edit: {exc}"
            ) from exc
        self._el, self._epoch = el, self._doc._element_epoch
        return parsed, el

    # ── text ────────────────────────────────────────────────────────────────

    @property
    def text(self) -> str:
        """The element's decoded text, as a reader would see it."""
        parsed, el = self._resolve()
        return "".join(r.decoded for r in _h.text_runs(parsed, el))

    @property
    def tag(self) -> str:
        """Lower-cased tag name."""
        _, el = self._resolve()
        return el.tag

    @property
    def attrs(self) -> dict[str, str | None]:
        """Current attributes, decoded. `None` for one written without a value.

        A copy, and re-read from the CURRENT draft each time — so it cannot go
        stale behind an edit, and mutating it cannot silently diverge from the
        document. Use `update(attrs=...)` to change one.

        Readable as well as writable because the common edit is conditional:
        changing a link only if it still points where you thought it did is not
        expressible if you can only write.
        """
        _, el = self._resolve()
        return dict(el.attrs)

    def find(self, query: str | None = None, *, regex: str | None = None, flags: str = ""):
        """Matches inside this element's text, reported as SOURCE ranges."""
        return self._matches(query, regex, flags)

    def _matches(self, query, regex, flags) -> list[_ed.Match]:
        parsed, el = self._resolve()
        out: list[_ed.Match] = []
        for run in _h.text_runs(parsed, el):
            for m in _ed.find(run.decoded, query, regex=regex, flags=flags):
                # Map the decoded span back to the raw source it came from.
                start = run.spans[m.ind[0]][0]
                end = run.spans[m.ind[1] - 1][1] if m.ind[1] > m.ind[0] else start
                out.append(
                    _ed.Match(
                        text=self._doc.text[start:end],
                        ind=(start, end),
                        line=(_ed.line_of(self._doc.text, start), _ed.line_of(self._doc.text, max(start, end - 1))),
                        captures=m.captures,
                        capture_spans=m.capture_spans,
                    )
                )
        return out

    def replace(
        self,
        text: str,
        *,
        query: str | None = None,
        regex: str | None = None,
        flags: str = "",
        count: int | None = None,
        all: bool = False,  # noqa: A002
    ) -> str:
        """Replace text inside this element. Returns the whole document."""
        if query is None and regex is None:
            # Selector-only: the element's contents become this text, escaped.
            _parsed, el = self._resolve()
            from html import escape

            src = self._doc.text
            self._doc._set(src[: el.inner[0]] + escape(text, quote=False) + src[el.inner[1] :])
            return self._doc.text

        matches = self._matches(query, regex, flags)
        chosen = _ed._select_matches(
            matches, count=count, all_=all, what="query" if query else "regex"
        )
        from html import escape

        edits = [(m.ind[0], m.ind[1], escape(text, quote=False)) for m in chosen]
        self._doc._set(_ed._splice(self._doc.text, edits))
        return self._doc.text

    # ── attributes ──────────────────────────────────────────────────────────

    def update(self, attrs: dict[str, str | None]) -> str:
        """Set or remove attributes on this element. Returns the whole document.

        Only the named attributes are touched; the rest of the open tag is left
        byte-for-byte, so quoting style and attribute order survive.
        """
        import re as _re
        from html import escape

        _parsed, el = self._resolve()
        src = self._doc.text
        start, end = el.start_tag
        tag_src = src[start:end]

        for name, value in attrs.items():
            pattern = _re.compile(
                rf"(\s{_re.escape(name)})(\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+))?",
                _re.IGNORECASE,
            )
            if value is None:
                tag_src = pattern.sub("", tag_src, count=1)
                continue
            written = f'{name}="{escape(str(value), quote=True)}"'
            if pattern.search(tag_src):
                tag_src = pattern.sub(" " + written, tag_src, count=1)
            else:
                # Insert before the closing '>' (or '/>'), keeping that form.
                close = -2 if tag_src.rstrip().endswith("/>") else -1
                tag_src = tag_src[:close].rstrip() + " " + written + tag_src[close:]

        self._doc._set(src[:start] + tag_src + src[end:])
        return self._doc.text


class Doc:
    """A loaded snapshot of a note's source, and the edits made to it."""

    __slots__ = ("_note", "_original", "_text", "_etag", "_element_epoch")

    def __init__(self, note: "Note", text: str, etag: str | None) -> None:
        self._note = note
        self._original = text
        self._text = text
        self._etag = etag
        self._element_epoch = 0

    # ── state ───────────────────────────────────────────────────────────────

    @property
    def text(self) -> str:
        """The current draft, including every local edit."""
        return self._text

    @property
    def original(self) -> str:
        """The source as read, before any local edit."""
        return self._original

    @property
    def etag(self) -> str | None:
        """The revision this snapshot was read at. `save()` writes against it."""
        return self._etag

    @property
    def dirty(self) -> bool:
        return self._text != self._original

    def _set(self, text: str) -> None:
        self._text = text
        self._element_epoch += 1

    def __str__(self) -> str:  # pragma: no cover - display only
        return self._text

    def __repr__(self) -> str:  # pragma: no cover - display only
        state = "edited" if self.dirty else "clean"
        return f"Doc({self._note.namespace}/{self._note.id}, {len(self._text)} chars, {state})"

    # ── looking ─────────────────────────────────────────────────────────────

    def find(
        self,
        query: str | None = None,
        *,
        regex: str | None = None,
        flags: str = "",
        limit: int = 1000,
    ) -> list[_ed.Match]:
        """Matches in the current draft, in source order."""
        return _ed.find(self._text, query, regex=regex, flags=flags, limit=limit)

    def select(self, css: str) -> Element:
        """Exactly one element, or a refusal saying whether it was missing or ambiguous."""
        el = _h.select(_h.parse(self._text), css)
        return Element(self, css, el, self._element_epoch)

    def toc(self, *, html: bool | None = None) -> list[TocEntry]:
        """The outline of the current draft, with line and character ranges."""
        return _toc(self._text, html=html)

    def diff(self, *, context: int = 3) -> str:
        """A unified diff from the source as read to the current draft."""
        if not self.dirty:
            return ""
        label = f"{self._note.namespace}/{self._note.id}"
        return "".join(
            difflib.unified_diff(
                self._original.splitlines(keepends=True),
                self._text.splitlines(keepends=True),
                fromfile=f"a/{label}",
                tofile=f"b/{label}",
                n=context,
            )
        )

    # ── editing ─────────────────────────────────────────────────────────────

    def replace(self, text: str, **where) -> str:
        """Replace the addressed text. Returns the whole updated source."""
        self._set(_ed.replace(self._text, text, **where))
        return self._text

    def insert(self, text: str, **where) -> str:
        """Insert at the addressed position. Returns the whole updated source."""
        self._set(_ed.insert(self._text, text, **where))
        return self._text

    def delete(self, **where) -> str:
        """Remove the addressed text. Returns the whole updated source."""
        self._set(_ed.delete(self._text, **where))
        return self._text

    def revert(self) -> str:
        """Throw the local edits away and go back to the source as read."""
        self._set(self._original)
        return self._text

    # ── saving ──────────────────────────────────────────────────────────────

    def save(self, *, force: bool = False) -> SaveResult:
        """Commit the draft as one conditional patch.

        Refuses when the note changed since it was read. That is not a
        transient error to retry: the edits were computed against text that is
        no longer there, and writing them anyway is how one agent silently
        undoes another. Read again and redo the edits on the current source.
        """
        if not self.dirty:
            # Nothing to commit. Reporting the revision already held is more
            # useful than a write that changes nothing, and it means a caller
            # can save unconditionally without checking `dirty` first.
            return SaveResult(etag=self._etag or "", size_bytes=len(self._text.encode()))
        etag = self._note.write(self._text, force=force)
        self._original = self._text
        self._etag = etag
        return SaveResult(etag=etag, size_bytes=len(self._text.encode()))
