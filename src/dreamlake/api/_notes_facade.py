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
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from ._client import DreamLakeClient, get_client
from .notes import (
    CommandStatus,
    Note,
    NoteError,
    NoteRef,
    NoteSnapshot,
    PatchReceipt,
    _OBJECT_ID,
    _resolve,
    list_notes,
)

__all__ = [
    "Notes",
    "NoteHandle",
    "ReadingViewRefused",
    "RevisionExpected",
    "notes",
]

#: Reading views carry this HTML comment; a write containing it is a generated
#: annotation being saved back as source, which is never what anyone meant.
READING_VIEW_MARKER = "<!-- notes-reading-view"

_MODES = ("exact", "merge")
_HASH_LIKE = re.compile(r'^(?:sha256:)?"?[a-f0-9]{64}"?$', re.I)


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
    """The RTC revision a snapshot or ``rtc:`` string names."""
    if isinstance(value, NoteSnapshot):
        return value.revision
    if isinstance(value, Mapping) and isinstance(value.get("revision"), str):
        return _revision_of(value["revision"], f"{name}.revision")
    if isinstance(value, str):
        if _HASH_LIKE.match(value):
            raise RevisionExpected(f"{name} is a content hash; pass the snapshot or its rtc: revision")
        if value.startswith("rtc:") and value.strip() == value and len(value) > 4 and "\r" not in value and "\n" not in value:
            return value
    raise ValueError(f"{name} must be a snapshot from read() or an 'rtc:' revision string")


# ── The handle ───────────────────────────────────────────────────────────────


class NoteHandle:
    """One note, opened by ``"namespace/note-id"``. Operations take named fields.

    Opening does not touch the network. A 24-hex id is addressed directly; a
    slug is allowed and resolved to its id on the first operation, so writes
    always go to the stable id.
    """

    __slots__ = ("_ns", "_ref", "_client", "_note")

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

        A bare revision has no content to plan against, so its retained source
        is read back from the server.
        """
        if isinstance(value, NoteSnapshot):
            return value
        if isinstance(value, Mapping):
            try:
                return NoteSnapshot(note=value["note"], content=value["content"],
                                    hash=value["hash"], revision=value["revision"])
            except KeyError as error:
                raise ValueError(f"{name} snapshot is missing {error.args[0]!r}") from None
        return self._target().read_snapshot(at=_revision_of(value, name))

    # ── reading ─────────────────────────────────────────────────────────────

    def read(self, *, view: str | None = None, at: Any = None) -> NoteSnapshot:
        """Read exact source: ``{note, content, hash, revision}``.

        ``at`` reads a retained revision (a snapshot or ``rtc:`` string) and
        verifies the server returned exactly it. ``view`` is ``"source"`` in
        this phase; mapped views are not exposed by the Python SDK yet.
        """
        if view not in (None, "source"):
            raise ValueError(f"view must be 'source' in this phase, not {view!r}")
        revision = None if at is None else _revision_of(at, "at")
        return self._target().read_snapshot(at=revision)

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
        are agreed. ``at`` may be a snapshot or an ``rtc:`` string.
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
        string, whose retained source is read back).
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
