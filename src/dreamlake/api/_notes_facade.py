"""Callable Notes facade — the Notes API the docs specify, in Python.

    note = dl.notes(note_id)                  # local, synchronous: no request
    snapshot = note.read()                    # exact source: {note, content, hash, revision}
    mapped = note.read(view="mapped")         # exact source plus observed addresses
    receipt = note.append(text="\\nHello\\n", at=snapshot)
    notes = dl.notes.list(namespace="fortyfive", limit=20, offset=0)
    created = dl.notes.create(namespace="fortyfive", name="Review", text="# Review\\n")

Stable IDs are primary: ``dl.notes(note_id)`` opens a handle without a request
and the server resolves the owner on first use. A slug needs explicit namespace
context — ``dl.notes("design-doc", namespace="fortyfive")`` or the qualified
``"fortyfive/design-doc"`` convenience. ``dl.notes`` is callable AND carries
the collection operations ``list`` and ``create``; there is no
namespace-then-get sequence. Every operation takes keyword arguments.

The handle is a thin adapter over :class:`dreamlake.api.notes.Note`: it adds no
second mutation engine. Edits compile to one ``inline-dff`` record against the
original snapshot and go through ``Note.patch``, so snapshot validation, receipt
verification and command identity are the existing ones. ``append`` is verbatim:
the payload includes every intended newline and no separator is added.

``at`` is the result of a prior read (preferred) or a bare ``rtc:`` revision. A
content hash is a different type: passing a ``sha256:`` value where a revision
belongs is ``RevisionExpected``, never a guess. A focused or scoped read can
support a targeted edit; it is refused as a whole-body baseline.

Outcome categories (the docs' CLI/SDK contract) are on every error as
``.outcome``: ``invalid-usage``, ``no-write``, ``unknown`` or ``operational``;
an accepted or no-op write is a receipt whose ``outcome`` is ``accepted`` or
``no-write``.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ._client import DreamLakeClient, get_client
from ._editing import _spans_to_match
from ._notes_mapped import HtmlSnapshot, HtmlSnapshotError, annotated_markdown, parse_html_snapshot
from .notes import (
    _OBJECT_ID,
    CommandStatus,
    Note,
    NoteError,
    NoteRef,
    NoteSnapshot,
    PatchReceipt,
    _escape_inline,
    _raise_for,
    _ref,
    _resolve,
    _seg,
    _v2_tokens,
    list_notes,
)

if False:  # pragma: no cover - typing only
    from ._files import NoteFiles

__all__ = [
    "DiffResult",
    "FindResult",
    "FocusedRead",
    "FoundMatch",
    "InvalidLineRange",
    "MappedElement",
    "MappedRead",
    "NoteCreatedWithoutSource",
    "NoteHandle",
    "NoteVersions",
    "Notes",
    "Outline",
    "OutlineEntry",
    "ReadingViewRefused",
    "RevisionExpected",
    "Roster",
    "SavedVersion",
    "SelectionError",
    "SnapshotRequired",
    "VersionPage",
    "VisitReceipt",
    "notes",
]

#: Reading views carry this HTML comment; a write containing it is a generated
#: annotation being saved back as source, which is never what anyone meant.
READING_VIEW_MARKER = "<!-- notes-reading-view"

_MODES = ("exact", "merge")
_HASH_LIKE = re.compile(r'^(?:sha256:)?"?[a-f0-9]{64}"?$', re.IGNORECASE)
_RTC_REVISION = re.compile(r"rtc:[a-f0-9]{64}")
_MARKDOWN = "markdown"


# ── Errors ───────────────────────────────────────────────────────────────────


class RevisionExpected(NoteError, ValueError):
    """A content hash (``sha256:``) was given where an RTC revision belongs.

    A hash names text; a revision names an RTC state. The server retains
    baselines by revision, so a hash cannot stand in for one — pass the
    snapshot ``read()`` returned, or its ``.revision``.
    """

    outcome = "invalid-usage"


class ReadingViewRefused(NoteError, ValueError):
    """The input contains the reading-view marker; annotations are not source.

    ``<!-- notes-reading-view`` is what a mapped reading view is stamped with.
    Writing it back would save a generated view as the canonical note.
    """

    outcome = "invalid-usage"


class SnapshotRequired(NoteError, TypeError):
    """A partial read was given where the complete source snapshot is required.

    ``append`` and ``write`` position their edit relative to the WHOLE source;
    a focused or scoped read is a slice carrying the full-source hash and
    revision, and using it as a baseline would plan the edit against the slice.
    """

    outcome = "invalid-usage"


class InvalidLineRange(NoteError, ValueError):
    """``lines`` is not two 1-based integers inside the source, or is reversed.

    Refused, not clamped: a range past the end is a misread length, not a
    request for "to the end". Same rule as the CLI.
    """

    outcome = "invalid-usage"


class SelectionError(NoteError, ValueError):
    """A selector did not resolve against the baseline it was given.

    An address that was not observed at that revision, lines outside a
    focused read, or a cursor outside the source: nothing was edited, and
    nothing is re-resolved against newer source.
    """

    outcome = "invalid-usage"


class NoteCreatedWithoutSource(NoteError):
    """The note exists but its initial source was not written.

    Creation and source initialization are two requests until the server
    offers atomic creation. ``note_id`` is the created note; write to it
    rather than creating a second one.
    """

    outcome = "unknown"

    def __init__(self, note_id: str, namespace: str, cause: BaseException):
        super().__init__(f"note {note_id!r} was created in {namespace!r} but its initial source was not written "
                         f"({cause}); write to that id instead of creating another note")
        self.note_id = note_id
        self.namespace = namespace


# ── Argument coercion ────────────────────────────────────────────────────────


def _exact(mode: Any) -> bool:
    if mode not in _MODES:
        raise ValueError(f"mode must be 'exact' or 'merge', not {mode!r}")
    return mode == "exact"


def _refuse_reading_view(text: Any, name: str) -> None:
    if not isinstance(text, str):
        raise ValueError(f"{name} must be a string")
    if READING_VIEW_MARKER in text:
        raise ReadingViewRefused(f"{name} contains {READING_VIEW_MARKER!r}; generated annotations are never saved")


def _revision_of(value: Any, name: str) -> str:
    """The RTC revision a read result, mapping or ``rtc:`` string names.

    The field inside a read result gets the same check as a bare string: a
    snapshot assembled by hand with a hash in ``revision`` is the same bug.
    """
    if isinstance(value, (NoteSnapshot, FocusedRead, MappedRead)):
        return _revision_of(value.revision, f"{name}.revision")
    if isinstance(value, Mapping) and isinstance(value.get("revision"), str):
        return _revision_of(value["revision"], f"{name}.revision")
    if isinstance(value, str):
        if _HASH_LIKE.match(value):
            raise RevisionExpected(f"{name} is a content hash; pass the read result or its rtc: revision")
        if value.startswith("rtc:") and value.strip() == value and len(value) > 4 and "\r" not in value and "\n" not in value:
            return value
    raise ValueError(f"{name} must be a read result or an 'rtc:' revision string")


def _line_chars(content: str, lines: Any) -> tuple[tuple[int, int], tuple[int, int], int | None]:
    """1-based inclusive source lines → code-point ``[start, end)`` on the v2 source.

    Lines end at LF; a CR before it stays inside the line, so CRLF sources
    slice byte-exactly. A trailing LF closes the last line rather than opening
    an empty one, so ``"a\\n"`` has one line. Python ``str`` indexes code
    points, which is what ``@@ chars`` addresses — never UTF-16 units.

    Returns the covered range, its code-point span, and the next line number
    or ``None`` at the end. A range past the last line is refused.
    """
    if (not isinstance(lines, (tuple, list)) or len(lines) != 2
            or any(isinstance(v, bool) or not isinstance(v, int) for v in lines)):
        raise InvalidLineRange("lines must be two integers (start, end): 1-based, inclusive")
    first, last = lines
    if first < 1 or last < first:
        raise InvalidLineRange(f"line range {first}:{last} is not a range; lines are 1-based and inclusive")
    starts = [0]
    pos = content.find("\n")
    while pos != -1:
        starts.append(pos + 1)
        pos = content.find("\n", pos + 1)
    if starts[-1] == len(content):
        starts.pop()
    total = len(starts)
    if last > total:
        raise InvalidLineRange(f"line {last} is past the end of a {total}-line note")
    end = starts[last] if last < total else len(content)
    return (first, last), (starts[first - 1], end), (last + 1 if last < total else None)


def _chars(raw: str | None) -> tuple[int, int]:
    try:
        start, end = (int(v) for v in (raw or "").split(":"))
    except ValueError:
        raise NoteError("invalid mapped element range") from None
    if start < 0 or end < start:
        raise NoteError("invalid mapped element range")
    return start, end


# ── Values ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FocusedRead:
    """A slice of one note's source, with the identity of the WHOLE source.

    Not a snapshot: ``content`` is ``lines`` only, while ``hash`` and
    ``revision`` describe the complete note that was read. ``chars`` is the
    slice's code-point ``[start, end)`` in that source — the coordinates an
    ``@@ chars`` patch addresses, so a targeted edit at it is supported.
    ``scope`` is ``{"lines": [a, b]}``. ``complete`` is always ``False`` and
    ``next`` is the line to continue from (``None`` at the end), so a page is
    never mistaken for the note and cannot be a whole-body ``write`` baseline.
    """

    note: str
    hash: str
    revision: str
    scope: dict[str, Any]
    lines: tuple[int, int]
    chars: tuple[int, int]
    content: str
    complete: bool = False
    next: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"note": self.note, "hash": self.hash, "revision": self.revision,
                "scope": {"lines": list(self.scope["lines"])},
                "lines": list(self.lines), "chars": list(self.chars), "content": self.content,
                "complete": self.complete, "next": self.next}


@dataclass(frozen=True)
class MappedElement:
    """One observed block and its absolute source span at the read revision.

    ``address`` is local to that revision (``s1.p2``), not a permanent id.
    ``chars`` is code-point ``[start, end)`` in the complete source and
    includes nested content: replacing the element replaces that whole span.
    """

    address: str
    chars: tuple[int, int]
    lines: tuple[int, int]
    section: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"address": self.address, "chars": list(self.chars), "lines": list(self.lines), "section": self.section}


@dataclass(frozen=True)
class MappedRead:
    """Exact source plus the mapping an agent navigates and edits by.

    ``content`` is exact source of the selected scope — never decorated.
    ``text`` is the reading view (source with generated ``<!-- … -->`` hints,
    ``None`` for an HTML-sourced note); it is presentation, not source, and a
    write containing its marker is refused. ``scope`` is one of
    ``{"article": True}``, ``{"section": id}``, ``{"address": id}`` or
    ``{"lines": [a, b]}``; ``complete`` says whether ``content`` is the whole
    note. A complete mapped read can baseline a whole-body write; a scoped one
    can baseline a targeted edit at its ``elements``.
    """

    note: str
    hash: str
    revision: str
    scope: dict[str, Any]
    chars: tuple[int, int]
    content: str
    source_type: str
    elements: tuple[MappedElement, ...]
    complete: bool
    text: str | None = None
    next: int | None = None

    def element(self, address: str) -> MappedElement | None:
        return next((e for e in self.elements if e.address == address), None)

    def to_dict(self) -> dict[str, Any]:
        return {"note": self.note, "hash": self.hash, "revision": self.revision, "scope": dict(self.scope),
                "chars": list(self.chars), "content": self.content, "sourceType": self.source_type,
                "elements": [e.to_dict() for e in self.elements], "complete": self.complete,
                "text": self.text, "next": self.next}


@dataclass(frozen=True)
class OutlineEntry:
    """A heading observed at one revision: its address, level and source span."""

    address: str
    title: str
    level: int
    chars: tuple[int, int]
    lines: tuple[int, int]
    section: str | None = None


@dataclass(frozen=True)
class Outline:
    """Headings with observed addresses, tied to one retained revision. Not writable source."""

    note: str
    hash: str
    revision: str
    entries: tuple[OutlineEntry, ...]
    text: str | None = None


@dataclass(frozen=True)
class FoundMatch:
    """One hit: exact matched source and its absolute code-point and line ranges."""

    text: str
    chars: tuple[int, int]
    lines: tuple[int, int]


@dataclass(frozen=True)
class FindResult:
    """Every match within ``limit``, at the revision searched. Not a write baseline."""

    note: str
    hash: str
    revision: str
    query: str
    case_sensitive: bool
    matches: tuple[FoundMatch, ...]
    truncated: bool
    limit: int

    def __iter__(self):
        return iter(self.matches)

    def __len__(self) -> int:
        return len(self.matches)


@dataclass(frozen=True)
class DiffResult:
    """A source diff from a retained revision to the current observed result.

    Apply ``patch`` only to source whose hash is ``base_hash`` and verify the
    result against ``hash``. An empty patch with a newer ``revision`` means
    identical content, not identical history.
    """

    note: str
    base_revision: str
    base_hash: str
    revision: str
    hash: str
    format: str
    patch: str


@dataclass(frozen=True)
class SavedVersion:
    """An immutable saved version. Its id is not an RTC revision."""

    id: str
    tag: str | None
    created_at: str | None
    summary: str | None
    hash: str
    text: str | None = None


@dataclass(frozen=True)
class VersionPage:
    versions: tuple[SavedVersion, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class Roster:
    """Who the server currently observes on the note; expired entries are not inferred active."""

    note: str
    participants: tuple[dict[str, Any], ...]
    observed_at: Any


@dataclass(frozen=True)
class VisitReceipt:
    """Awareness acknowledgment: an accepted location and lease expiry. Not a write receipt."""

    note: str
    expires_at: Any
    location: dict[str, Any] | None
    raw: dict[str, Any]


# ── The handle ───────────────────────────────────────────────────────────────


class NoteHandle:
    """One note, opened by stable id. Operations take keyword arguments only.

    Opening does not touch the network. A bare 24-hex id is addressed through
    the server's identity lookup on first use; a slug is resolved within its
    explicit namespace. Writes always go to the stable id.
    """

    __slots__ = ("_client", "_note", "_ns", "_ref")

    def __init__(self, ref: str, *, namespace: str | None = None, client: DreamLakeClient | None = None):
        if not isinstance(ref, str) or not ref or ref.strip() != ref:
            raise ValueError(f"note reference {ref!r} must be a stable 24-hex id, or a slug with namespace=")
        if "/" in ref:
            if namespace is not None:
                raise ValueError("give the namespace once: either 'namespace/ref' or namespace=")
            if ref.count("/") != 1 or not all(ref.split("/")):
                raise ValueError(f"qualified reference {ref!r} must be 'namespace/<id-or-slug>'")
            namespace, ref = ref.split("/", 1)
        if namespace is not None and (not isinstance(namespace, str) or not namespace or "/" in namespace):
            raise ValueError(f"namespace {namespace!r} must be a bare namespace slug")
        if namespace is None and not _OBJECT_ID.fullmatch(ref):
            raise ValueError(f"{ref!r} is not a stable 24-hex id; a slug needs explicit namespace context (namespace=)")
        self._ns = namespace
        self._ref = ref
        self._client = client
        self._note: Note | None = None

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<NoteHandle {self.ref}>"

    @property
    def ref(self) -> str:
        """The reference this handle was opened with: the id, or ``namespace/ref``."""
        return f"{self._ns}/{self._ref}" if self._ns else self._ref

    @property
    def id(self) -> str:
        """The stable 24-hex id. Known locally for an id; resolves a slug on first use."""
        return self._ref if _OBJECT_ID.fullmatch(self._ref) else self._target().id

    @property
    def namespace(self) -> str:
        """The owning namespace. Known locally when given; otherwise resolved on first use."""
        return self._ns if self._ns is not None else self._target().namespace

    # ── plumbing ────────────────────────────────────────────────────────────

    def _target(self) -> Note:
        if self._note is None:
            client = self._client or get_client()
            if _OBJECT_ID.fullmatch(self._ref):
                namespace, note_id = self._ns, self._ref
                if namespace is None:
                    # IDs are primary: the server resolves the owner. One request,
                    # once; opening the handle made none.
                    with client.http() as http:
                        response = http.get(f"/notes/{_seg(note_id)}")
                    _raise_for(response, f"resolve {note_id}")
                    row = response.json()
                    namespace = row.get("namespaceSlug") if isinstance(row, dict) else None
                    if row.get("id") != note_id or not isinstance(namespace, str) or not namespace:
                        raise NoteError(f"server did not identify note {note_id!r}")
            else:
                assert self._ns is not None
                _, note_id = _resolve(f"{self._ns}/{self._ref}", client)
                namespace = self._ns
            self._note = Note(note_id, namespace=namespace, client=client)
        return self._note

    def _own(self, value: Any, name: str) -> None:
        """Refuse a read result or mapping that names another note."""
        owner = (value.note if isinstance(value, (NoteSnapshot, FocusedRead, MappedRead))
                 else value.get("note") if isinstance(value, Mapping) else None)
        if owner is not None and owner != self.id:
            raise ValueError(f"{name} belongs to note {owner!r}, not {self.id!r}")

    def _snapshot_of(self, value: Any, name: str) -> NoteSnapshot:
        """A complete source snapshot for a whole-body operation.

        A focused or scoped read is refused outright; a complete mapped read
        carries exact complete source and is accepted. A bare revision has no
        content to plan against, so its retained source is read back.
        """
        if isinstance(value, FocusedRead) or (isinstance(value, MappedRead) and not value.complete):
            raise SnapshotRequired(f"{name} is a partial read ({value.scope}); append and write need the complete "
                                   "snapshot from read() or a complete mapped read")
        if isinstance(value, MappedRead):
            value = NoteSnapshot(note=value.note, content=value.content, hash=value.hash, revision=value.revision)
        if isinstance(value, Mapping):
            try:
                value = NoteSnapshot(note=value["note"], content=value["content"],
                                     hash=value["hash"], revision=value["revision"])
            except KeyError as error:
                raise ValueError(f"{name} snapshot is missing {error.args[0]!r}") from None
        if isinstance(value, NoteSnapshot):
            _revision_of(value, name)
            if not isinstance(value.content, str) or value.hash != "sha256:" + hashlib.sha256(value.content.encode("utf-8")).hexdigest():
                raise ValueError(f"{name} source does not match its content hash")
            return value
        return self._target().read_snapshot(at=_revision_of(value, name))

    def _mapped(self, *, at: str | None, section: str | None = None, tag: str | None = None,
                toc: bool = False) -> HtmlSnapshot:
        """The mapped snapshot the CLI's ``--view html`` reads, validated the same way."""
        note = self._target()
        params: dict[str, Any] = {"contract": "v2"}
        if at is not None:
            params["at"] = at
        if section is not None:
            params["section"] = section
        if tag is not None:
            params["tag"] = tag
        if toc:
            params["toc"] = "true"
        params["view"] = "html"
        params["contentFormat"] = "literal-markdown"
        with note._client.http() as http:
            response = http.get(f"{note._base}/body", params=params, follow_redirects=False)
        _raise_for(response, f"read {note.namespace}/{note.id}")
        if response.status_code != 200:
            raise NoteError("unexpected mapped read response; nothing was adopted")
        try:
            snapshot = parse_html_snapshot(response.text, note.id, section=section, tag=tag, toc=toc)
        except HtmlSnapshotError as error:
            raise NoteError(f"invalid mapped read: {error}") from None
        if at is not None and snapshot.revision != at:
            raise NoteError("server did not return the requested retained RTC revision")
        return snapshot

    @staticmethod
    def _elements(snapshot: HtmlSnapshot) -> tuple[MappedElement, ...]:
        out = []
        for address in snapshot.addresses:
            if not address.get("id"):
                continue
            out.append(MappedElement(address=address["id"], chars=_chars(address.get("data-char")),
                                     lines=_chars(address.get("data-lines")), section=address.get("data-section-id")))
        return tuple(out)

    # ── reading ─────────────────────────────────────────────────────────────

    def read(self, *, view: str | None = None, lines: tuple[int, int] | None = None, section: str | None = None,
             address: str | None = None, at: Any = None) -> NoteSnapshot | FocusedRead | MappedRead:
        """Read exact source, optionally mapped or scoped, at the current or a retained revision.

        ``view=None``/``"source"`` returns the :class:`NoteSnapshot`
        ``{note, content, hash, revision}`` — the baseline for a whole-body
        write. ``view="mapped"`` returns a :class:`MappedRead`: the same exact
        source plus the observed element addresses and their absolute ranges,
        and the reading view in ``text``. Exactly one of ``lines=(a, b)``
        (1-based, inclusive), ``section="s1"`` or ``address="s1.p2"`` scopes
        the read; the last two need the mapped view. ``at`` reads a retained
        revision (a prior result or ``rtc:`` string) and verifies the server
        returned exactly it.
        """
        if view not in (None, "source", "mapped"):
            raise ValueError(f"view must be 'source' or 'mapped', not {view!r}")
        chosen = [name for name, value in (("lines", lines), ("section", section), ("address", address)) if value is not None]
        if len(chosen) > 1:
            raise ValueError(f"choose one of lines, section or address, not {chosen}")
        if at is not None:
            self._own(at, "at")
        revision = None if at is None else _revision_of(at, "at")
        if view != "mapped":
            if section is not None or address is not None:
                raise ValueError("section and address are mapped selectors; pass view='mapped'")
            snapshot = self._target().read_snapshot(at=revision)
            if lines is None:
                return snapshot
            covered, chars, following = _line_chars(snapshot.content, lines)
            return FocusedRead(note=snapshot.note, hash=snapshot.hash, revision=snapshot.revision,
                               scope={"lines": [covered[0], covered[1]]}, lines=covered, chars=chars,
                               content=snapshot.content[chars[0]:chars[1]], complete=False, next=following)
        if lines is not None:
            page = self._mapped(at=revision)
            covered, chars, following = _line_chars(page.source, lines)
            elements = tuple(e for e in self._elements(page) if chars[0] <= e.chars[0] and e.chars[1] <= chars[1])
            content = page.source[chars[0]:chars[1]]
            text = None
            if page.source_type == _MARKDOWN:
                inside = tuple(a for a in page.addresses if chars[0] <= _chars(a.get("data-char"))[0] <= chars[1])
                text = annotated_markdown(page.revision, page.hash, f"lines {covered[0]}:{covered[1]}", content, chars[0], inside)
            return MappedRead(note=page.note, hash=page.hash, revision=page.revision,
                              scope={"lines": [covered[0], covered[1]]}, chars=chars, content=content,
                              source_type=page.source_type, elements=elements, complete=False, text=text, next=following)
        page = self._mapped(at=revision, section=section, tag=address)
        scope: dict[str, Any] = ({"section": section} if section is not None
                                 else {"address": address} if address is not None else {"article": True})
        text = None
        if page.source_type == _MARKDOWN:
            text = annotated_markdown(page.revision, page.hash, page.scope, page.source, page.start, page.addresses)
        return MappedRead(note=page.note, hash=page.hash, revision=page.revision, scope=scope,
                          chars=(page.start, page.end), content=page.source, source_type=page.source_type,
                          elements=self._elements(page), complete=section is None and address is None, text=text)

    def outline(self, *, at: Any = None) -> Outline:
        """Headings and their observed addresses at one retained revision.

        Locates a section for a focused read or a targeted edit. Not complete
        writable source; a heading number is not a permanent identifier.
        """
        if at is not None:
            self._own(at, "at")
        page = self._mapped(at=None if at is None else _revision_of(at, "at"), toc=True)
        entries = []
        for address in page.addresses:
            if not address.get("id"):
                continue
            title = address.get("data-source", "")
            level = len(title) - len(title.lstrip("#")) if page.source_type == _MARKDOWN else 0
            entries.append(OutlineEntry(address=address["id"], title=title, level=level,
                                        chars=_chars(address.get("data-char")), lines=_chars(address.get("data-lines")),
                                        section=address.get("data-section-id")))
        text = None
        if page.source_type == _MARKDOWN:
            text = annotated_markdown(page.revision, page.hash, page.scope, "", 0, page.addresses, toc=True)
        return Outline(note=page.note, hash=page.hash, revision=page.revision, entries=tuple(entries), text=text)

    def find(self, *, text: str, case_sensitive: bool = False, at: Any = None, limit: int = 1000) -> FindResult:
        """Literal search of this note's exact source at one revision.

        Returns every match up to ``limit`` with exact matched text and
        absolute code-point and line ranges; ``truncated`` says the limit was
        hit. Repeated text yields repeated matches — a unique target is never
        manufactured. Not a baseline for a whole-body write.
        """
        if not isinstance(text, str) or not text:
            raise ValueError("text must be a nonempty string")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        if at is not None:
            self._own(at, "at")
        snapshot = self._target().read_snapshot(at=None if at is None else _revision_of(at, "at"))
        pattern = re.compile(re.escape(text), 0 if case_sensitive else re.IGNORECASE)
        matches: list[FoundMatch] = []
        truncated = False
        for match in pattern.finditer(snapshot.content):
            if len(matches) >= limit:
                truncated = True
                break
            hit = _spans_to_match(snapshot.content, match.start(), match.end())
            matches.append(FoundMatch(text=hit.text, chars=hit.ind, lines=hit.line))
        return FindResult(note=snapshot.note, hash=snapshot.hash, revision=snapshot.revision, query=text,
                          case_sensitive=case_sensitive, matches=tuple(matches), truncated=truncated, limit=limit)

    def diff(self, *, since: Any, format: str = "diff") -> DiffResult:
        """Source changes from a retained revision to the current observed source.

        ``since`` is an RTC revision or a prior read result carrying one; the
        retained source is read back so the diff base is that exact source.
        Inspection only: no draft baseline moves. ``format`` is ``diff``
        (unified lines) or ``inline-dff``.
        """
        if format not in ("diff", "inline-dff"):
            raise ValueError("format must be 'diff' or 'inline-dff'")
        self._own(since, "since")
        revision = _revision_of(since, "since")
        note = self._target()
        base = note.read_snapshot(at=revision)
        with note._client.http() as http:
            response = http.get(f"{note._base}/body", params={"contract": "v2", "since": base.hash, "format": format},
                                follow_redirects=False)
        _raise_for(response, f"diff {note.namespace}/{note.id}")
        if response.status_code != 200:
            raise NoteError("unexpected diff response; nothing was adopted")
        try:
            data = response.json()
            _v2_tokens(data, note.id)
            if data.get("base") != base.hash or data.get("format") != format or not isinstance(data.get("patch"), str):
                raise NoteError("inconsistent source diff")
            if len(data["patch"].encode("utf-8")) > 8_000_000:
                raise NoteError("source diff too large")
        except (ValueError, NoteError):
            raise NoteError("invalid source diff response; nothing was adopted") from None
        acknowledged = note.read_snapshot(at=data["revision"])
        if acknowledged.hash != data["hash"]:
            raise NoteError("source diff does not match its acknowledged source hash")
        return DiffResult(note=note.id, base_revision=revision, base_hash=base.hash, revision=data["revision"],
                          hash=data["hash"], format=format, patch=data["patch"])

    # ── editing ─────────────────────────────────────────────────────────────

    def _baseline(self, at: Any, name: str = "at") -> NoteSnapshot | FocusedRead | MappedRead:
        """The read result an edit is planned against; a bare revision is read back."""
        self._own(at, name)
        if isinstance(at, (NoteSnapshot, FocusedRead, MappedRead)):
            _revision_of(at, name)
            return at
        if isinstance(at, Mapping):
            return self._snapshot_of(at, name)
        return self._target().read_snapshot(at=_revision_of(at, name))

    def _span_for_lines(self, baseline: NoteSnapshot | FocusedRead | MappedRead, lines: Any) -> tuple[tuple[int, int], str]:
        """Absolute code-point span and exact source of ``lines`` within the baseline."""
        if isinstance(baseline, NoteSnapshot) or (isinstance(baseline, MappedRead) and baseline.complete):
            _, chars, _ = _line_chars(baseline.content, lines)
            return chars, baseline.content[chars[0]:chars[1]]
        if isinstance(baseline, (FocusedRead, MappedRead)) and "lines" in baseline.scope:
            first, last = baseline.scope["lines"]
            if (not isinstance(lines, (tuple, list)) or len(lines) != 2
                    or any(isinstance(v, bool) or not isinstance(v, int) for v in lines)):
                raise InvalidLineRange("lines must be two integers (start, end): 1-based, inclusive")
            if not (first <= lines[0] <= lines[1] <= last):
                raise SelectionError(f"lines {lines[0]}:{lines[1]} are outside the focused read's lines {first}:{last}")
            _, relative, _ = _line_chars(baseline.content, (lines[0] - first + 1, lines[1] - first + 1))
            chars = (baseline.chars[0] + relative[0], baseline.chars[0] + relative[1])
            return chars, baseline.content[relative[0]:relative[1]]
        raise SelectionError("a line selection needs a complete snapshot or a line-scoped read as its baseline")

    def _span_for_address(self, baseline: NoteSnapshot | FocusedRead | MappedRead, address: str) -> tuple[tuple[int, int], str]:
        """Absolute span and exact source of an observed address at the baseline's revision."""
        if not isinstance(address, str) or not address:
            raise ValueError("address must be a nonempty observed address")
        element = baseline.element(address) if isinstance(baseline, MappedRead) else None
        if element is not None and baseline.chars[0] <= element.chars[0] and element.chars[1] <= baseline.chars[1]:
            offset = baseline.chars[0]
            return element.chars, baseline.content[element.chars[0] - offset:element.chars[1] - offset]
        page = self._mapped(at=baseline.revision)
        element = next((e for e in self._elements(page) if e.address == address), None)
        if element is None:
            raise SelectionError(f"address {address!r} was not observed at revision {baseline.revision}")
        return element.chars, page.source[element.chars[0]:element.chars[1]]

    def _submit(self, baseline: NoteSnapshot | FocusedRead | MappedRead, chars: tuple[int, int], old: str, new: str,
                *, mode: str, command_id: str | None) -> PatchReceipt:
        """One inline-dff record against the original revision, through Note.patch."""
        exact = _exact(mode)
        if old == new and not exact and command_id is None:
            return PatchReceipt(self.id, "merge", baseline.revision, baseline.hash, baseline.revision, outcome="no-write")
        body = ("[-" + _escape_inline(old) + "-]" if old else "") + ("{+" + _escape_inline(new) + "+}" if new else "")
        patch = f"@@ chars {chars[0]}:{chars[1]} @@\n~ {body}\n" if old or new else ""
        receipt = self._target().patch(patch, base_revision=baseline.revision, exact=exact, command_id=command_id)
        assert isinstance(receipt, PatchReceipt)
        return receipt

    def replace(self, *, text: str, lines: tuple[int, int] | None = None, address: str | None = None, at: Any,
                mode: str = "merge", command_id: str | None = None) -> PatchReceipt:
        """Replace one observed source span with ``text``, verbatim.

        Exactly one of ``lines=(a, b)`` or ``address="s1.p2"`` selects the span
        in the baseline ``at``; the whole span goes, nested content and line
        terminators included, and only ``text`` comes in. Empty ``text``
        deletes the span. Default ``merge`` lets unrelated edits through and
        refuses overlap; ``exact`` requires the original revision to be current.
        """
        _refuse_reading_view(text, "text")
        if (lines is None) == (address is None):
            raise ValueError("give exactly one of lines or address")
        baseline = self._baseline(at)
        chars, old = (self._span_for_lines(baseline, lines) if lines is not None
                      else self._span_for_address(baseline, address))  # type: ignore[arg-type]
        return self._submit(baseline, chars, old, text, mode=mode, command_id=command_id)

    def insert(self, *, text: str, before: str | None = None, after: str | None = None, cursor: int | None = None,
               at: Any, mode: str = "merge", command_id: str | None = None) -> PatchReceipt:
        """Insert ``text`` verbatim at exactly one placement; nothing is removed.

        ``before``/``after`` name an observed address (its full span's start or
        end); ``cursor`` is a zero-based code-point boundary in the original
        source, 0 and the document end included. No newline, indentation or
        marker is added — the caller decides whether separators are needed.
        """
        _refuse_reading_view(text, "text")
        chosen = [name for name, value in (("before", before), ("after", after), ("cursor", cursor)) if value is not None]
        if len(chosen) != 1:
            raise ValueError(f"give exactly one of before, after or cursor, not {chosen}")
        baseline = self._baseline(at)
        if cursor is not None:
            if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
                raise ValueError("cursor must be a non-negative integer code-point offset")
            if isinstance(baseline, NoteSnapshot) or (isinstance(baseline, MappedRead) and baseline.complete):
                if cursor > len(baseline.content):
                    raise SelectionError(f"cursor {cursor} is past the end of a {len(baseline.content)}-code-point source")
            elif not (baseline.chars[0] <= cursor <= baseline.chars[1]):
                raise SelectionError(f"cursor {cursor} is outside the partial read's range {baseline.chars[0]}:{baseline.chars[1]}")
            position = cursor
        else:
            span, _ = self._span_for_address(baseline, before if before is not None else after)  # type: ignore[arg-type]
            position = span[0] if before is not None else span[1]
        return self._submit(baseline, (position, position), "", text, mode=mode, command_id=command_id)

    def remove(self, *, lines: tuple[int, int] | None = None, address: str | None = None, at: Any,
               mode: str = "merge", command_id: str | None = None) -> PatchReceipt:
        """Remove exactly one observed span — its structure and newlines included.

        Takes no ``text``. Deleting a parent address deletes the nested source
        in its span; adjacent lines may become adjacent text, and no separator
        is inserted to hide that. Whole-resource deletion is a different
        operation.
        """
        if (lines is None) == (address is None):
            raise ValueError("give exactly one of lines or address")
        baseline = self._baseline(at)
        chars, old = (self._span_for_lines(baseline, lines) if lines is not None
                      else self._span_for_address(baseline, address))  # type: ignore[arg-type]
        return self._submit(baseline, chars, old, "", mode=mode, command_id=command_id)

    def append(self, *, text: str, at: Any, mode: str = "merge", command_id: str | None = None) -> PatchReceipt:
        """Append ``text`` verbatim at the original snapshot's document end.

        No separator is added: the payload carries every intended newline, so
        appending ``"\\nHello\\n"`` to a source already ending in LF leaves a
        blank line. ``at`` must be the complete snapshot (or a complete mapped
        read, or an ``rtc:`` revision whose source is read back). Default
        ``merge``; ``exact`` requires the original revision to be current.
        ``command_id`` (opt-in) is the documented answer to a lost response:
        the same id replays the stored receipt instead of appending twice.
        """
        _refuse_reading_view(text, "text")
        exact = _exact(mode)
        self._own(at, "at")
        original = self._snapshot_of(at, "at")
        # Released separator rule, retired by the next commit (the docs' append is verbatim).
        text = ("\n" if original.content and not original.content.endswith("\n") else "") + text
        if not text and not exact and command_id is None:
            return PatchReceipt(original.note, "merge", original.revision, original.hash, original.revision, outcome="no-write")
        end = len(original.content)
        patch = f"@@ chars {end}:{end} @@\n~ {{+{_escape_inline(text)}+}}\n" if text else ""
        receipt = self._target().patch(patch, base_revision=original.revision, exact=exact, command_id=command_id)
        assert isinstance(receipt, PatchReceipt)
        return receipt

    def write(self, *, text: str, at: Any, mode: str, command_id: str | None = None) -> PatchReceipt:
        """Replace the whole source with ``text``, planned against the complete snapshot ``at``.

        The difference is compiled against the original source and sent as
        retained-baseline edits; no latest-body replacement, no ETag path. A
        partial read is refused as a baseline. ``mode`` is required until a
        default is ratified; an unchanged target under ``exact`` is still
        submitted so the server checks the precondition.
        """
        _refuse_reading_view(text, "text")
        exact = _exact(mode)
        self._own(at, "at")
        receipt = self._target().write(text, at=self._snapshot_of(at, "at"), exact=exact, command_id=command_id)
        assert isinstance(receipt, PatchReceipt)
        return receipt

    def patch(self, *, patch: str, format: str, at: Any, mode: str, command_id: str | None = None) -> PatchReceipt:
        """Apply an explicit original-source patch (``diff`` or ``inline-dff``).

        ``format`` and ``mode`` are required. Coordinates refer to the retained
        original source named by ``at``; a newer revision is never attached to
        an old patch. ``at`` may be a complete or partial read result (its
        revision is the baseline) or an ``rtc:`` string.
        """
        _refuse_reading_view(patch, "patch")
        if format not in ("diff", "inline-dff"):
            raise ValueError("format must be 'diff' or 'inline-dff'")
        exact = _exact(mode)
        self._own(at, "at")
        receipt = self._target().patch(patch, base_revision=_revision_of(at, "at"), exact=exact, format=format,
                                       command_id=command_id)
        assert isinstance(receipt, PatchReceipt)
        return receipt

    def command_status(self, *, command_id: str) -> CommandStatus:
        """What the server recorded for ``command_id``; the read after ``UncertainOutcomeError``."""
        return self._target().command_status(command_id)

    # ── resources ───────────────────────────────────────────────────────────

    def rename(self, *, name: str) -> NoteRef:
        """Change the display name. Stable id, source and RTC history are untouched.

        A catalog operation: no body revision is involved and no ownership
        changes. Slug/redirect policy is the server's, not a side effect here.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a nonempty string")
        note = self._target()
        with note._client.http() as http:
            response = http.patch(note._base, json={"name": name})
        _raise_for(response, f"rename {note.namespace}/{note.id}")
        return _ref(response.json(), note.namespace)

    @property
    def versions(self) -> NoteVersions:
        """Saved immutable versions: ``versions.list()`` and ``versions.read(version_id=...)``."""
        return NoteVersions(self)

    @property
    def files(self) -> NoteFiles:
        """Files attached to this note (``files.list``, ``files.upload``, ``files.move(file=..., ...)``)."""
        return self._target().files

    # ── collaboration ───────────────────────────────────────────────────────

    def presence(self) -> Roster:
        """The roster, without joining or extending any lease."""
        note = self._target()
        with note._client.http() as http:
            response = http.get(f"{note._base}/presence", params={"format": "json"}, follow_redirects=False)
        _raise_for(response, f"presence {note.namespace}/{note.id}")
        try:
            data = response.json()
        except ValueError:
            raise NoteError("invalid presence response") from None
        if response.status_code != 200 or not isinstance(data, dict) or not isinstance(data.get("participants"), list):
            raise NoteError("invalid presence response")
        return Roster(note=note.id, participants=tuple(p for p in data["participants"] if isinstance(p, dict)),
                      observed_at=data.get("observedAt"))

    def visit(self, *, intent: str | None = None, address: str | None = None, cursor: int | None = None,
              at: Any = None) -> VisitReceipt:
        """Announce the caller once; optionally at an observed location.

        ``intent`` is a short purpose (1–280 characters). At most one of
        ``address`` or ``cursor`` locates the visit, and a location needs its
        original source baseline ``at`` (a read result; its hash binds the
        range). A visit changes awareness, never source.
        """
        summary = None
        if intent is not None:
            summary = intent.strip() if isinstance(intent, str) else ""
            if not summary or len(summary) > 280:
                raise ValueError("intent must be 1-280 characters of plain text")
        if address is not None and cursor is not None:
            raise ValueError("give at most one of address or cursor")
        located = address is not None or cursor is not None
        if located and at is None:
            raise ValueError("a located visit needs its original source baseline: pass at=")
        note = self._target()
        payload: dict[str, Any] = {"action": "join"}
        if summary:
            payload["intent"] = {"summary": summary}
        location = None
        if located:
            baseline = self._baseline(at)
            if cursor is not None:
                if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
                    raise ValueError("cursor must be a non-negative integer code-point offset")
                span = (cursor, cursor)
            else:
                span, _ = self._span_for_address(baseline, address)  # type: ignore[arg-type]
            location = {"hash": baseline.hash, "chars": [span[0], span[1]]}
        with note._client.http() as http:
            response = http.post(f"{note._base}/presence", json=payload, follow_redirects=False)
            _raise_for(response, f"visit {note.namespace}/{note.id}")
            data = self._presence_receipt(response)
            if location is not None:
                response = http.post(f"{note._base}/presence", follow_redirects=False,
                                     json={"action": "seek", "hash": location["hash"],
                                           "ranges": [{"start": location["chars"][0], "end": location["chars"][1]}]})
                _raise_for(response, f"visit {note.namespace}/{note.id}")
                data = self._presence_receipt(response)
                activity = (data.get("state") or {}).get("activity") or {}
                if activity.get("hash") != location["hash"]:
                    raise NoteError("server did not acknowledge the visited location at its source hash")
        state = data.get("state") or {}
        return VisitReceipt(note=note.id, expires_at=state.get("expiresAt"), location=location, raw=data)

    @staticmethod
    def _presence_receipt(response: Any) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            raise NoteError("invalid presence receipt") from None
        if response.status_code != 200 or not isinstance(data, dict):
            raise NoteError("invalid presence receipt")
        return data


class NoteVersions:
    """Saved immutable versions of one note. Their ids are not RTC revisions."""

    __slots__ = ("_handle",)

    def __init__(self, handle: NoteHandle):
        self._handle = handle

    def list(self, *, before: str | None = None) -> VersionPage:
        """Newest first; ``before`` continues from a previous page's ``next_cursor``."""
        note = self._handle._target()
        with note._client.http() as http:
            response = http.get(f"{note._base}/versions", params={} if before is None else {"before": before},
                                follow_redirects=False)
        _raise_for(response, f"versions of {note.namespace}/{note.id}")
        try:
            data = response.json()
        except ValueError:
            raise NoteError("invalid versions response") from None
        if response.status_code != 200 or not isinstance(data, dict) or not isinstance(data.get("versions"), list):
            raise NoteError("invalid versions response")
        return VersionPage(versions=tuple(_version(row, require_text=False) for row in data["versions"]),
                           next_cursor=data.get("nextCursor"))

    def read(self, *, version_id: str) -> SavedVersion:
        """One saved version's content and metadata, verified against its hash. Not a current-write baseline."""
        if not isinstance(version_id, str) or not version_id.startswith("v_") or version_id.strip() != version_id:
            raise ValueError("version_id must be a saved version id (v_<timestamp>_<uuid>)")
        note = self._handle._target()
        with note._client.http() as http:
            response = http.get(f"{note._base}/versions/{_seg(version_id)}", follow_redirects=False)
        _raise_for(response, f"version {version_id} of {note.namespace}/{note.id}")
        try:
            data = response.json()
        except ValueError:
            raise NoteError("invalid saved version response") from None
        if response.status_code != 200 or not isinstance(data, dict) or data.get("id") != version_id:
            raise NoteError("saved version does not match its identity")
        version = _version(data, require_text=True)
        assert version.text is not None
        if hashlib.sha256(version.text.encode("utf-8")).hexdigest() != version.hash.removeprefix("sha256:"):
            raise NoteError("saved version text does not match its hash")
        return version


def _version(row: Any, *, require_text: bool) -> SavedVersion:
    if (not isinstance(row, dict) or not isinstance(row.get("id"), str) or not isinstance(row.get("hash"), str)
            or (require_text and not isinstance(row.get("text"), str))):
        raise NoteError("invalid saved version record")
    return SavedVersion(id=row["id"], tag=row.get("tag"), created_at=row.get("createdAt"), summary=row.get("summary"),
                        hash=row["hash"], text=row.get("text") if isinstance(row.get("text"), str) else None)


# ── The collection ───────────────────────────────────────────────────────────


class Notes:
    """``dl.notes``: call it to open a handle; ``.list`` and ``.create`` for the collection.

    Unbound (``client=None``) it uses the process default client at call time,
    so importing the package reads no credentials. ``DreamLakeClient.notes``
    returns one bound to that client.
    """

    __slots__ = ("_client",)

    def __init__(self, client: DreamLakeClient | None = None):
        self._client = client

    def __call__(self, ref: str, *, namespace: str | None = None) -> NoteHandle:
        return NoteHandle(ref, namespace=namespace, client=self._client)

    def list(self, *, namespace: str, limit: int = 50, offset: int = 0, include_archived: bool = False) -> list[NoteRef]:
        """Notes you can see in ``namespace``: ``limit`` rows from zero-based row ``offset``."""
        return list_notes(namespace, limit=limit, offset=offset, include_archived=include_archived, client=self._client)

    def create(self, *, namespace: str, name: str | None = None, text: str | None = None,
               visibility: str = "private") -> NoteHandle:
        """Create a note and return its handle; ``name`` and ``text`` are optional.

        Omitted ``text`` means exact empty source; omitted ``name`` displays as
        ``Untitled``. Initial source is written through the retained-baseline
        contract after creation (two requests: the server has no atomic
        creation yet). If that second step fails, ``NoteCreatedWithoutSource``
        carries the created id so the caller writes to it rather than creating
        a second note.
        """
        if not isinstance(namespace, str) or not namespace:
            raise ValueError("namespace is required: an explicit namespace or a configured default")
        if name is not None and (not isinstance(name, str) or not name.strip()):
            raise ValueError("name must be a nonempty string when given")
        if text is not None:
            _refuse_reading_view(text, "text")
        if visibility not in ("private", "public"):
            raise ValueError("visibility must be 'private' or 'public'")
        client = self._client or get_client()
        with client.http() as http:
            response = http.post(f"/namespaces/{_seg(namespace)}/notes",
                                 json={"name": name if name is not None else "Untitled", "visibility": visibility})
        _raise_for(response, f"create note in {namespace}")
        try:
            note_id = response.json()["note"]["id"]
        except (ValueError, KeyError, TypeError):
            raise NoteError("create did not return the note's id") from None
        handle = NoteHandle(note_id, namespace=namespace, client=client)
        if text:
            try:
                note = handle._target()
                note.write(text, at=note.read_snapshot(), exact=True)
            except NoteError as error:
                raise NoteCreatedWithoutSource(note_id, namespace, error) from error
        return handle

    def __repr__(self) -> str:  # pragma: no cover - display only
        return "<dreamlake.notes>" if self._client is None else f"<notes of {self._client.dl_url}>"


#: Module-level entry point: ``import dreamlake as dl; dl.notes(note_id)``.
notes = Notes()
