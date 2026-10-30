"""Callable Notes facade — the public syntax agreed across CLI, TS and Python.

    note = dl.notes("fortyfive/note-id")      # local, synchronous: no request
    snapshot = note.read()
    receipt = note.append(text="Hello", at=snapshot, mode="exact")
    notes = dl.notes.list(namespace="fortyfive")

``dl.notes`` is callable AND carries the collection method; it has nothing
else. One positional reference ``"namespace/note-id"`` opens a handle; every
operation takes named fields, and ``mode`` is required on every write because
divergent defaults across clients are the bug this surface exists to fix.

The handle is a thin adapter over :class:`dreamlake.api.notes.Note`: it adds no
second mutation engine. The append separator, inline escaping, snapshot
validation and receipt verification are the existing ones, so the bytes on the
wire are the same as ``Note.append(..., at=snapshot)`` sends.

``at`` accepts the snapshot object ``read()`` returned (preferred) or a bare
``rtc:`` revision string. A content hash is a different type: passing a
``sha256:`` value where a revision belongs is a typed error, never a guess.

``command_id`` is opt-in on every write until the server that records it is
deployed: supplied, it is sent and must be echoed; absent, nothing new is sent.

``read(lines=(a, b))`` is a focused read: the same v2 source request, sliced
locally to 1-based inclusive lines, with the line → code-point conversion done
here rather than left to the caller. Its result is a distinct type that cannot
be passed as the baseline of a whole-body write.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ._client import DreamLakeClient, get_client
from .notes import (
    _OBJECT_ID,
    CommandStatus,
    Note,
    NoteError,
    NoteRef,
    NoteSnapshot,
    PatchReceipt,
    _resolve,
    list_notes,
)

__all__ = [
    "FocusedRead",
    "NoteHandle",
    "Notes",
    "ReadingViewRefused",
    "RevisionExpected",
    "SnapshotRequired",
    "notes",
]

#: Reading views carry this HTML comment; a write containing it is a generated
#: annotation being saved back as source, which is never what anyone meant.
READING_VIEW_MARKER = "<!-- notes-reading-view"

_MODES = ("exact", "merge")
_HASH_LIKE = re.compile(r'^(?:sha256:)?"?[a-f0-9]{64}"?$', re.IGNORECASE)


# ── Errors ───────────────────────────────────────────────────────────────────


class RevisionExpected(NoteError, ValueError):
    """A content hash (``sha256:``) was given where an RTC revision belongs.

    A hash names text; a revision names an RTC state. The server retains
    baselines by revision, so a hash cannot stand in for one — pass the
    snapshot ``read()`` returned, or its ``.revision``.
    """


class ReadingViewRefused(NoteError, ValueError):
    """The input contains the reading-view marker; annotations are not source.

    ``<!-- notes-reading-view`` is what a mapped/HTML reading view is stamped
    with. Writing it back would save a generated view as the canonical note.
    """


class SnapshotRequired(NoteError, TypeError):
    """A focused read was given where the complete source snapshot is required.

    ``append`` and ``write`` position their edit relative to the WHOLE source;
    a focused read is a slice carrying the full-source hash and revision, and
    using it as a baseline would plan the edit against the slice.
    """


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
    """The RTC revision a snapshot, focused read or ``rtc:`` string names.

    The field inside a snapshot gets the same check as a bare string: a
    snapshot assembled by hand with a hash in ``revision`` is the same bug.
    """
    if isinstance(value, (NoteSnapshot, FocusedRead)):
        return _revision_of(value.revision, f"{name}.revision")
    if isinstance(value, Mapping) and isinstance(value.get("revision"), str):
        return _revision_of(value["revision"], f"{name}.revision")
    if isinstance(value, str):
        if _HASH_LIKE.match(value):
            raise RevisionExpected(f"{name} is a content hash; pass the snapshot or its rtc: revision")
        if value.startswith("rtc:") and value.strip() == value and len(value) > 4 and "\r" not in value and "\n" not in value:
            return value
    raise ValueError(f"{name} must be a snapshot from read() or an 'rtc:' revision string")


def _line_chars(content: str, lines: Any) -> tuple[tuple[int, int], tuple[int, int], int | None]:
    """1-based inclusive source lines → code-point ``[start, end)`` on the v2 source.

    Lines end at LF; a CR before it stays inside the line, so CRLF sources
    slice byte-exactly. A trailing LF closes the last line rather than opening
    an empty one, so ``"a\n"`` has one line. Python ``str`` indexes code
    points, which is what ``@@ chars`` addresses — never UTF-16 units.

    Returns the range actually covered (``end`` is clamped to the last line),
    its code-point span, and the next line number or ``None`` at the end.
    """
    if (not isinstance(lines, (tuple, list)) or len(lines) != 2
            or any(isinstance(v, bool) or not isinstance(v, int) for v in lines)):
        raise ValueError("lines must be two integers (start, end): 1-based, inclusive")
    first, last = lines
    if first < 1 or last < first:
        raise ValueError("lines must satisfy 1 <= start <= end")
    starts = [0]
    pos = content.find("\n")
    while pos != -1:
        starts.append(pos + 1)
        pos = content.find("\n", pos + 1)
    if starts[-1] == len(content):
        starts.pop()
    total = len(starts)
    if first > total:
        raise ValueError(f"lines start {first} is past the last line; the source has {total} line(s)")
    last = min(last, total)
    end = starts[last] if last < total else len(content)
    return (first, last), (starts[first - 1], end), (last + 1 if last < total else None)


# ── Values ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FocusedRead:
    """A slice of one note's source, with the identity of the WHOLE source.

    Not a snapshot: ``content`` is ``lines`` only, while ``hash`` and
    ``revision`` describe the complete note that was read. ``chars`` is the
    slice's code-point ``[start, end)`` in that source — the coordinates an
    ``@@ chars`` patch addresses, so a targeted ``patch(at=focused)`` is
    supported. ``complete`` is always ``False`` and ``next`` is the line to
    continue from (``None`` at the end), so a page is never mistaken for the
    note and cannot be passed as a whole-body ``write`` baseline.
    """

    note: str
    hash: str
    revision: str
    lines: tuple[int, int]
    chars: tuple[int, int]
    content: str
    complete: bool = False
    next: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"note": self.note, "hash": self.hash, "revision": self.revision,
                "lines": list(self.lines), "chars": list(self.chars), "content": self.content,
                "complete": self.complete, "next": self.next}


# ── The handle ───────────────────────────────────────────────────────────────


class NoteHandle:
    """One note, opened by ``"namespace/note-id"``. Operations take named fields.

    Opening does not touch the network. A 24-hex id is addressed directly; a
    slug is allowed and resolved to its id on the first operation, so writes
    always go to the stable id.
    """

    __slots__ = ("_client", "_note", "_ns", "_ref")

    def __init__(self, ref: str, *, client: DreamLakeClient | None = None):
        if not isinstance(ref, str) or ref.count("/") != 1 or not all(ref.split("/")):
            raise ValueError(
                f"note reference {ref!r} must be 'namespace/note-id' (a slug may stand in for the id)"
            )
        self._ns, self._ref = ref.split("/", 1)
        self._client = client
        self._note: Note | None = None

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<NoteHandle {self._ns}/{self._ref}>"

    @property
    def namespace(self) -> str:
        return self._ns

    @property
    def ref(self) -> str:
        """The reference this handle was opened with, ``"namespace/note-id"``."""
        return f"{self._ns}/{self._ref}"

    @property
    def id(self) -> str:
        """The stable 24-hex id. Resolves a slug on first use."""
        return self._target().id

    # ── plumbing ────────────────────────────────────────────────────────────

    def _target(self) -> Note:
        if self._note is None:
            client = self._client or get_client()
            if _OBJECT_ID.match(self._ref):
                note_id = self._ref
            else:
                _, note_id = _resolve(f"{self._ns}/{self._ref}", client)
            self._note = Note(note_id, namespace=self._ns, client=client)
        return self._note

    def _snapshot_of(self, value: Any, name: str) -> NoteSnapshot:
        """A complete source snapshot for a whole-body operation.

        A focused read is refused outright. A bare revision has no content to
        plan against, so its retained source is read back from the server.
        """
        if isinstance(value, FocusedRead):
            raise SnapshotRequired(
                f"{name} is a focused read of lines {value.lines[0]}-{value.lines[1]}; "
                "append and write need the complete snapshot from read()"
            )
        if isinstance(value, Mapping):
            try:
                value = NoteSnapshot(note=value["note"], content=value["content"],
                                     hash=value["hash"], revision=value["revision"])
            except KeyError as error:
                raise ValueError(f"{name} snapshot is missing {error.args[0]!r}") from None
        if isinstance(value, NoteSnapshot):
            _revision_of(value, name)
            return value
        return self._target().read_snapshot(at=_revision_of(value, name))

    # ── reading ─────────────────────────────────────────────────────────────

    def read(self, *, view: str | None = None, lines: tuple[int, int] | None = None,
             at: Any = None) -> NoteSnapshot | FocusedRead:
        """Read exact source: ``{note, content, hash, revision}``.

        ``lines=(a, b)`` returns a :class:`FocusedRead` of those 1-based
        inclusive source lines instead — the same request, sliced here with
        code-point ``chars`` so the caller never computes offsets. ``at``
        reads a retained revision (a snapshot or ``rtc:`` string) and verifies
        the server returned exactly it. ``view`` is ``"source"`` in this
        phase; mapped views are not exposed by the Python SDK yet.
        """
        if view not in (None, "source"):
            raise ValueError(f"view must be 'source' in this phase, not {view!r}")
        revision = None if at is None else _revision_of(at, "at")
        snapshot = self._target().read_snapshot(at=revision)
        if lines is None:
            return snapshot
        covered, chars, following = _line_chars(snapshot.content, lines)
        return FocusedRead(note=snapshot.note, hash=snapshot.hash, revision=snapshot.revision,
                           lines=covered, chars=chars, content=snapshot.content[chars[0]:chars[1]],
                           complete=False, next=following)

    # ── writing ─────────────────────────────────────────────────────────────

    def append(self, text: str, *, at: Any, mode: str, command_id: str | None = None) -> PatchReceipt:
        """Append ``text`` to the source ``at`` names; return the receipt.

        ``mode="exact"`` requires ``at`` to still be current; ``mode="merge"``
        lets the server merge around independent edits. Separator rule, as the
        CLI: a nonempty source without a final LF receives one before ``text``.
        ``command_id`` (opt-in) makes the request retry-safe; see ``Note.patch``.
        """
        _refuse_reading_view(text, "text")
        exact = _exact(mode)
        receipt = self._target().append(text, at=self._snapshot_of(at, "at"), exact=exact, command_id=command_id)
        assert isinstance(receipt, PatchReceipt)
        return receipt

    def patch(self, patch: str, *, at: Any, mode: str, format: str | None = None,
              command_id: str | None = None) -> PatchReceipt:
        """PROVISIONAL — apply a source patch (``inline-dff`` or ``diff``) against ``at``.

        The name and shape may change; only ``read``, ``append`` and ``list``
        are agreed. ``at`` may be a snapshot, a focused read (its revision is
        a valid baseline for a targeted edit at its ``chars``) or an ``rtc:``
        string.
        """
        _refuse_reading_view(patch, "patch")
        exact = _exact(mode)
        receipt = self._target().patch(patch, base_revision=_revision_of(at, "at"), exact=exact, format=format,
                                       command_id=command_id)
        assert isinstance(receipt, PatchReceipt)
        return receipt

    def write(self, text: str, *, at: Any, mode: str, command_id: str | None = None) -> PatchReceipt:
        """PROVISIONAL — replace the whole source with ``text``, planned against ``at``.

        The name and shape may change; only ``read``, ``append`` and ``list``
        are agreed. ``at`` must be the COMPLETE snapshot (or an ``rtc:``
        string, whose retained source is read back); a focused read is refused.
        """
        _refuse_reading_view(text, "text")
        exact = _exact(mode)
        receipt = self._target().write(text, at=self._snapshot_of(at, "at"), exact=exact, command_id=command_id)
        assert isinstance(receipt, PatchReceipt)
        return receipt

    def command_status(self, command_id: str) -> CommandStatus:
        """What the server recorded for ``command_id``; the read after ``UncertainOutcomeError``."""
        return self._target().command_status(command_id)


# ── The collection ───────────────────────────────────────────────────────────


class Notes:
    """``dl.notes``: call it to open a handle, ``.list`` to enumerate. Nothing else.

    Unbound (``client=None``) it uses the process default client at call time,
    so importing the package reads no credentials. ``DreamLakeClient.notes``
    returns one bound to that client.
    """

    __slots__ = ("_client",)

    def __init__(self, client: DreamLakeClient | None = None):
        self._client = client

    def __call__(self, ref: str) -> NoteHandle:
        return NoteHandle(ref, client=self._client)

    def list(self, *, namespace: str, limit: int = 50, offset: int = 0) -> list[NoteRef]:
        """Every note in ``namespace`` you can see. The namespace is required."""
        return list_notes(namespace, limit=limit, offset=offset, client=self._client)

    def __repr__(self) -> str:  # pragma: no cover - display only
        return "<dreamlake.notes>" if self._client is None else f"<notes of {self._client.dl_url}>"


#: Module-level entry point: ``import dreamlake as dl; dl.notes("ns/id")``.
notes = Notes()
