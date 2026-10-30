"""Notes conformance fixtures (Phase 1 contract D), replayed through the facade.

`notes-conformance.fixtures.json` holds the SAME seven scenarios the CLI/TS
track writes from its implementation. Each is replayed here against a fake
transport and the request's method, path, query and body are asserted exactly;
the two files are diffed afterwards, so a difference is a finding, never
something to adjust away in one suite.

The expected values were generated from this implementation and inspected, not
written by hand: the append separator rule (one LF before the addition only
when a nonempty source lacks a final LF) and the inline escaping of newlines
are what the bytes show.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from dreamlake.api._client import DreamLakeClient
from dreamlake.api._notes_facade import FocusedRead
from dreamlake.api.notes import NoteSnapshot, PatchReceipt

FIXTURE = Path(__file__).parent / "notes-conformance.fixtures.json"
CASES = json.loads(FIXTURE.read_text())
NEXT = "rtc:" + "fedcba9876543210" * 4

REQUIRED = [
    "append-after-newline",
    "append-no-trailing-newline",
    "append-empty-body",
    "append-text-with-newline",
    "append-with-command-id",
    "read-source",
    "read-lines",
]


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _replay(case: dict):
    content, revision = case["snapshot"]["content"], case["snapshot"]["revision"]
    note = case["note"]
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.method == "GET":
            return httpx.Response(200, json=dict(note=note, content=content, hash=_sha(content), revision=revision), request=req)
        sent = json.loads(req.content)
        return httpx.Response(200, json=dict(
            note=note, mode=sent["mode"], baseRevision=sent["baseRevision"], hash=_sha(content + "Hi"), revision=NEXT,
            outcome="accepted", commandId=sent.get("commandId"), replayed=False), request=req)

    client = DreamLakeClient(dl_url="https://example.test", token="t", transport=httpx.MockTransport(handler))
    handle = client.notes(f"{case['namespace']}/{note}")
    # The fixture spells named fields the contract's way; Python's are snake_case
    # and a two-element list is a tuple, exactly as test_shared_fixtures does.
    args = {("command_id" if k == "commandId" else k): (tuple(v) if k == "lines" else v)
            for k, v in case["args"].items()}
    if case["op"] == "append":
        snapshot = NoteSnapshot(note=note, content=content, hash=_sha(content), revision=revision)
        result = handle.append(at=snapshot, **args)
    elif case["op"] == "read":
        result = handle.read(**args)
    else:
        raise AssertionError(f"unknown op {case['op']!r}")
    return seen, result


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_conformance_scenario(case):
    seen, result = _replay(case)
    assert len(seen) == 1, [r.method for r in seen]
    (req,) = seen
    expect = case["expect"]
    assert req.method == expect["method"]
    assert req.url.path == expect["path"]
    assert dict(req.url.params) == expect.get("query", {})
    if "body" in expect:
        assert json.loads(req.content) == expect["body"]
    else:
        assert not req.content
    if case["op"] == "append":
        assert isinstance(result, PatchReceipt) and result.mode == expect["body"]["mode"]
        assert result.command_id == expect["body"].get("commandId")
    if "result" in expect:
        assert isinstance(result, FocusedRead) and not isinstance(result, NoteSnapshot)
        got = result.to_dict()
        assert {k: got[k] for k in expect["result"]} == expect["result"]
    elif case["op"] == "read":
        assert isinstance(result, NoteSnapshot)
        assert result.to_dict() == dict(note=case["note"], content=case["snapshot"]["content"],
                                        hash=_sha(case["snapshot"]["content"]), revision=case["snapshot"]["revision"])


def test_exactly_the_agreed_scenarios():
    assert [c["name"] for c in CASES] == REQUIRED


def test_every_scenario_has_the_contract_shape():
    for c in CASES:
        assert set(c) == {"name", "op", "namespace", "note", "snapshot", "args", "expect"}, c["name"]
        assert c["op"] in ("append", "read")
        assert c["namespace"] == "fortyfive" and c["note"] == "6ae3f318608512b4ba34860c"
        assert set(c["snapshot"]) == {"content", "revision"}
        assert c["snapshot"]["revision"].startswith("rtc:") and len(c["snapshot"]["revision"]) == 68
        assert set(c["expect"]) <= {"method", "path", "query", "body", "result"}
