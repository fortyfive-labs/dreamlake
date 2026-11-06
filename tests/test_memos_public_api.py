"""Canonical imports and published compatibility paths must share one engine."""
import hashlib
import importlib
import subprocess
import sys

import httpx
import pytest

import dreamlake as dl
from dreamlake.api._client import DreamLakeClient
from dreamlake.api.memos import Memo, MemoSnapshot

MEMO_ID = "6ae3f318608512b4ba34860c"


def test_canonical_types_and_facades_are_real_implementations():
    assert Memo.__name__ == "Memo"
    assert Memo.__module__ == "dreamlake.api.memos"
    assert dl.Memo is Memo is dl.Note
    assert dl.memos is dl.notes
    assert type(dl.memos).__name__ == "Memos"
    assert type(dl.memos).__module__ == "dreamlake.api._memos_facade"
    assert dl.memos(MEMO_ID).id == MEMO_ID
    assert "Memo" in dl.__all__ and "memos" in dl.__all__


@pytest.mark.parametrize("old,new", [
    ("notes", "memos"), ("_notes_facade", "_memos_facade"),
    ("_notes_mapped", "_memos_mapped"),
])
def test_legacy_modules_alias_the_canonical_module(old, new):
    assert importlib.import_module(f"dreamlake.api.{old}") is importlib.import_module(f"dreamlake.api.{new}")


@pytest.mark.parametrize("first,second", [("notes", "memos"), ("memos", "notes")])
def test_import_order_never_replaces_modules_with_callable_facades(first, second):
    # Isolated interpreters exercise Python's package attribute side effects.
    result = subprocess.run([sys.executable, "-c", f"""
import dreamlake as dl
import dreamlake.api.{first} as first
import dreamlake.api.{second} as second
from types import ModuleType
assert isinstance(first, ModuleType) and first is second
assert first.Memo is dl.Memo
assert callable(dl.memos) and dl.memos is dl.notes
assert first.memo is dl.memo
"""], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_canonical_client_read_preserves_existing_wire():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={
            "note": MEMO_ID, "content": "shared source", "hash":
            "sha256:" + hashlib.sha256(b"shared source").hexdigest(),
            "revision": "rtc:" + "ab" * 32,
        })
    client = DreamLakeClient(dl_url="https://example.test", token="fixture",
                             transport=httpx.MockTransport(handler))
    result = client.memos(MEMO_ID, namespace="team").read()
    assert isinstance(result, MemoSnapshot)
    assert result.content == "shared source"
    assert [r.url.path for r in calls] == [f"/namespaces/team/notes/{MEMO_ID}/body"]
    assert isinstance(client.notes(MEMO_ID), dl.MemoHandle)


def test_canonical_and_legacy_function_imports_resolve_to_same_callable():
    module = importlib.import_module("dreamlake.api.memos")
    for canonical, legacy in [("memo", "note"), ("create_memo", "create_note"),
                              ("list_memos", "list_notes"), ("search_memos", "search_notes"),
                              ("grep_memos", "grep_notes")]:
        assert getattr(dl, canonical) is getattr(dl, legacy)
        assert getattr(module, canonical) is getattr(module, legacy)
        assert canonical in module.__all__
