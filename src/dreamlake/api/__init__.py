"""
DreamLake Python API.

Objects:
    TextTrack   — time-aligned text entries
    VectorIndex — named vector index (Qdrant)
    Note        — a collaborative note, readable and writable by section

Functions:
    load        — generic loader (parses type prefix)
    upload      — chunked upload with auto type detection
    text_track  — create a TextTrack
    vec_index   — create/connect to a VectorIndex
    note        — open a Note by "namespace/slug"
    list_notes / search_notes / grep_notes / shared_with_me
    Notes       — the callable facade: dl.notes("ns/id") / dl.notes.list(namespace=...)

Context:
    Prefix      — context manager for project/path scoping
"""

from .text_track import TextTrack
from .vector_index import VectorIndex
from .prefix import Prefix
from .notes import (
    Note,
    NoteRef,
    NoteSnapshot,
    PatchReceipt,
    PatchResult,
    Section,
    SectionMatch,
    NoteError,
    NoteNotFound,
    NoteChanged,
    NoteBusy,
    NoteReadOnly,
    PatchFailed,
    CommandIdentityUnsupportedError,
    UncertainOutcomeError,
    CommandInProgressError,
    CommandIntentMismatchError,
    CommandStatusInvalid,
    CommandStatus,
    note,
    create_note,
    list_notes,
    grep_notes,
    search_notes,
    shared_with_me,
)
# The callable facade. The module-level `notes` object is exported from the
# top-level package only: here it would shadow the `dreamlake.api.notes` module.
from ._notes_facade import (
    Notes,
    NoteHandle,
    FocusedRead,
    ReadingViewRefused,
    RevisionExpected,
    SnapshotRequired,
)

__all__ = [
    "TextTrack",
    "VectorIndex",
    "Note",
    "NoteRef",
    "NoteSnapshot",
    "PatchReceipt",
    "PatchResult",
    "Section",
    "SectionMatch",
    "NoteError",
    "NoteNotFound",
    "NoteChanged",
    "NoteBusy",
    "NoteReadOnly",
    "PatchFailed",
    "CommandIdentityUnsupportedError",
    "UncertainOutcomeError",
    "CommandInProgressError",
    "CommandIntentMismatchError",
    "CommandStatusInvalid",
    "CommandStatus",
    "note",
    "create_note",
    "list_notes",
    "grep_notes",
    "search_notes",
    "shared_with_me",
    "Notes",
    "NoteHandle",
    "FocusedRead",
    "ReadingViewRefused",
    "RevisionExpected",
    "SnapshotRequired",
    "Prefix",
]
