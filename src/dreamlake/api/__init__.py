"""
DreamLake Python API.

Objects:
    TextTrack   — time-aligned text entries
    VectorIndex — named vector index (Qdrant)
    Memo        — a collaborative memo, readable and writable by section

Functions:
    load        — generic loader (parses type prefix)
    upload      — chunked upload with auto type detection
    text_track  — create a TextTrack
    vec_index   — create/connect to a VectorIndex
    memo        — open a Memo by "namespace/slug"
    list_memos / search_memos / grep_memos / shared_with_me
    Memos       — the callable facade: dl.memos("ns/id") / dl.memos.list(namespace=...)

Context:
    Prefix      — context manager for project/path scoping
"""

from .text_track import TextTrack
from .vector_index import VectorIndex
from .prefix import Prefix
from .memos import (
    Memo,
    MemoRef,
    MemoSnapshot,
    PatchReceipt,
    PatchResult,
    Section,
    SectionMatch,
    MemoError,
    MemoNotFound,
    MemoChanged,
    MemoBusy,
    MemoReadOnly,
    PatchFailed,
    CommandIdentityUnsupportedError,
    UncertainOutcomeError,
    CommandInProgressError,
    CommandIntentMismatchError,
    CommandStatusInvalid,
    CommandStatus,
    WriteOutcomeUnknown,
    note,
    create_note,
    list_notes,
    grep_notes,
    search_notes,
    shared_with_me,
)
# Callable facades live on the top-level package. Keep api.memos and
# api.notes available as importable modules.
from ._memos_facade import (
    AgentIdentityRequired,
    DiffResult,
    FindResult,
    FocusedRead,
    FoundMatch,
    InvalidLineRange,
    MappedElement,
    MappedRead,
    MemoCreatedWithoutSource,
    MemoHandle,
    MemoVersions,
    Memos,
    Outline,
    OutlineEntry,
    ReadingViewRefused,
    RevisionExpected,
    Roster,
    SavedVersion,
    SelectionError,
    SnapshotRequired,
    VersionPage,
    VisitReceipt,
)

__all__ = [
    "TextTrack",
    "VectorIndex",
    "Memo",
    "MemoRef",
    "MemoSnapshot",
    "PatchReceipt",
    "PatchResult",
    "Section",
    "SectionMatch",
    "MemoError",
    "MemoNotFound",
    "MemoChanged",
    "MemoBusy",
    "MemoReadOnly",
    "PatchFailed",
    "CommandIdentityUnsupportedError",
    "UncertainOutcomeError",
    "CommandInProgressError",
    "CommandIntentMismatchError",
    "CommandStatusInvalid",
    "CommandStatus",
    "WriteOutcomeUnknown",
    "note",
    "create_note",
    "list_notes",
    "grep_notes",
    "search_notes",
    "shared_with_me",
    "Memos",
    "AgentIdentityRequired",
    "DiffResult",
    "FindResult",
    "FocusedRead",
    "FoundMatch",
    "InvalidLineRange",
    "MappedElement",
    "MappedRead",
    "MemoCreatedWithoutSource",
    "MemoHandle",
    "MemoVersions",
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
    "Prefix",
]

# Canonical Memo functions and legacy import compatibility.
from .memos import memo, create_memo, list_memos, search_memos, grep_memos
NoteCreatedWithoutSource = MemoCreatedWithoutSource  # Deprecated compatibility name.
NoteHandle = MemoHandle  # Deprecated compatibility name.
NoteVersions = MemoVersions  # Deprecated compatibility name.
Notes = Memos  # Deprecated compatibility name.
NoteError = MemoError  # Deprecated compatibility name.
NoteNotFound = MemoNotFound  # Deprecated compatibility name.
NoteChanged = MemoChanged  # Deprecated compatibility name.
NoteBusy = MemoBusy  # Deprecated compatibility name.
NoteReadOnly = MemoReadOnly  # Deprecated compatibility name.
NoteSnapshot = MemoSnapshot  # Deprecated compatibility name.
NoteRef = MemoRef  # Deprecated compatibility name.
Note = Memo  # Deprecated compatibility name.
__all__ += ["memo", "create_memo", "list_memos", "search_memos", "grep_memos"]

# Preserve published star imports during the phased API transition.
__all__ += ['Note', 'NoteRef', 'NoteSnapshot', 'NoteError', 'NoteNotFound', 'NoteChanged', 'NoteBusy', 'NoteReadOnly', 'Notes', 'NoteCreatedWithoutSource', 'NoteHandle', 'NoteVersions']
