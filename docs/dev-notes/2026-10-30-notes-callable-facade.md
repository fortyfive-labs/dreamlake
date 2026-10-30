# 2026-10-30 — Notes callable facade, command identity, focused reads

Tracking: Notes API convergence Phase 1 (Ge Yang's proposal note
`6ae3f318608512b4ba34860c`; docs work in
https://github.com/dreamlake-ai/dreamlake-workspace/pull/1522).

Python gains the public syntax agreed across CLI, TS and Python: `dl.notes` is
callable and carries `list`, nothing else. `dl.notes("fortyfive/note-id")`
opens a handle locally; `note.read()` returns the v2 `NoteSnapshot`;
`note.append(text=..., at=snapshot, mode="exact")` writes. `mode` is required
on every facade write because divergent defaults across clients are the bug
being fixed. `at` is the snapshot (preferred) or a bare `rtc:` revision; a
`sha256:` value where a revision belongs is `RevisionExpected`, a bare
revision has its retained source read back before planning. Writes containing
`<!-- notes-reading-view` raise `ReadingViewRefused`. `patch` and `write` are
exposed as PROVISIONAL adapters. The facade lives in `api/_notes_facade.py` as
a thin layer over `Note`: it adds no second mutation engine, so the separator
rule, inline escaping, snapshot validation and receipt checks are the existing
ones and the bytes on the wire are those `Note.append(..., at=)` already sends.
`DreamLakeClient.notes` returns the same facade bound to one client. The
existing `dl.note()`, `read_snapshot()`, `append()`, `patch()`, `write()` and
`list_notes()` are unchanged.

Command identity (contract A) is opt-in on `patch`/`write`/`append`, facade
and existing methods alike. `command_id` is validated as
`[A-Za-z0-9._:-]{8,128}` and sent as `commandId` only when supplied; a
receipt that does not echo it is `CommandIdentityUnsupportedError`; any
transport failure once the request was handed to the transport (a connect
error included: an injected transport may forward and then raise), an unknown
status or a non-receipt response is `UncertainOutcomeError` carrying the id,
whose message says to resend with the same id or call
`command_status(command_id)` (`GET .../commands/{id}`). With a
`command_id` an unchanged MERGE is still submitted so the server records the
command; without one the local observational receipt now says
`outcome="no-write"`. `PatchReceipt` gains `outcome`, `command_id`,
`replayed`, each `None` when the server did not send it; `to_dict()` adds the
camel-case keys only when present, so receipts from today's servers are
unchanged. The default stays opt-in until the recording server is deployed.

`note.read(lines=(a, b))` (contract C) is the same v2 request sliced locally:
1-based inclusive lines mapped to code-point `chars` `[start, end)` with
Python `str` indexing, so CJK and emoji count one each and CRLF slices
byte-exactly. The result is `FocusedRead {note, hash, revision, lines, chars,
content, complete=False, next}`; `hash`/`revision` are the whole note's, `next`
is the line to continue from. It is not a `NoteSnapshot`: `write`/`append`
refuse it with `SnapshotRequired`, `patch(at=focused)` uses its revision for a
targeted edit at its `chars`. `end` past the last line is clamped and reported;
`start` past it is an error.

`tests/notes-conformance.fixtures.json` holds the seven contract-D scenarios,
generated from this implementation and inspected rather than hand-written;
`test_notes_conformance.py` replays them through the facade against a fake
transport and asserts method/path/query/body exactly. `args` keeps the
contract's spelling (`commandId`, `lines: [1, 1]`) and the runner maps it to
Python names, as `test_shared_fixtures.py` does for ranges. The CLI/TS track
writes the same file from its implementation; the two are diffed afterwards.

Validation: `uv run pytest tests test`, `sphinx-build -b html docs`, and
`ruff check` on the new modules. No version bump; publication is separate.
