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
    note,
    create_note,
    list_notes,
    grep_notes,
    search_notes,
    shared_with_me,
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
    "note",
    "create_note",
    "list_notes",
    "grep_notes",
    "search_notes",
    "shared_with_me",
    "Prefix",
]
