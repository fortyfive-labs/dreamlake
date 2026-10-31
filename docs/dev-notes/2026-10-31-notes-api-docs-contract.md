# 2026-10-31 — Notes API docs become the Python contract

Tracking: Notes API design pages (`docs/pages/notes/api/**`, workspace commit
`798b16d5`, docs release 0.1.107). Their Python tabs now bind the SDK and
override the Phase 1 contract file where they differ. This note records the
gap list and the decisions the docs left to the implementation.

The branch was rebased onto `origin/main` at 0.33.1 (archive API, metadata
route). Python 0.34.0 ships the facade as the docs specify it; `dl.note()`,
`read_snapshot()` and the released `Note.append` separator remain unchanged
compatibility surfaces.

## Gap list — fixed

- IDs primary: `dl.notes(note_id)` opens by stable id with no request and
  resolves the owner on first use through `GET /notes/{id}`; a slug needs
  `namespace=` or the `"ns/slug"` convenience. Phase 1 required `"ns/id"`.
- Keyword arguments everywhere; `dl.notes.create(namespace=, name=, text=)`
  added; `dl.notes.list(namespace=, limit=, offset=)` kept offsets.
- `append(text=, at=)` is verbatim with no separator and defaults to `merge`
  (Phase 1 added an LF and required `mode`). `write` and `patch` still require
  `mode`; `patch` now requires `format` (`diff` | `inline-dff`).
- `read(view="mapped")` is first-class: it fetches the snapshot the CLI's
  `--view html` reads (`view=html&contentFormat=literal-markdown`), validates
  it exactly as `validateHtmlSnapshot` does and returns `MappedRead` — exact
  `content`, absolute `chars`, `elements` with code-point and line ranges and
  section ids, and the reading view in `text` built by the server's/CLI's
  annotation rule. `section=`/`address=` scope it (server `section`/`tag`);
  `lines=` under either view; exactly one selector.
- `outline(at=)` (toc scope), `find(text=, case_sensitive=, at=, limit=)`
  (literal search of exact source; absolute ranges; `truncated`), and
  `diff(since=revision, format=)` (retained base read back, then the v2
  `since=<hash>` inspection, then the acknowledged readback).
- `replace`/`insert`/`remove` compile one `inline-dff` record against the
  original baseline — `lines` or an observed `address` (resolved from a
  `MappedRead` without a request, or from a mapped read at that revision) and
  `before`/`after`/`cursor` — and go through `Note.patch`; default `merge`;
  identical replacement is a local `no-write`. Partial reads baseline targeted
  edits inside their span and are refused for `append`/`write`; a complete
  mapped read is a valid whole-body baseline.
- `lines` past the end are refused (`InvalidLineRange`), as the CLI refuses.
- `rename(name=)` (metadata PATCH), `versions.list()/read(version_id=)` with
  hash verification, `files.move(file=, to_folder=, name=)`, `presence()`,
  `visit(intent=, address=|cursor=, at=)` (join, then a hash-bound seek).
- Outcome categories on every error (`.outcome`): `invalid-usage`,
  `no-write`, `unknown`, `operational`; `WriteOutcomeUnknown` types the
  unidentified unknown outcome. `command_id` is kept and described as the
  documented answer to a lost-response append: the same id replays the stored
  receipt instead of appending twice.

## Gap list — left, with reason

- `watch(timeout=, debounce_ms=, max_wait_ms=, after=)`: a typed stream with
  a total timeout needs the normalized event schema; durable catch-up needs
  server retention. Not implemented.
- `move(to_namespace=, to_project=, to_folder=, name=)`: the one-facade move
  across collections is still being specified; `rename(name=)` covers the
  title-only case. Not implemented. `dl.projects(...)`, `dl.artifacts(...)`
  and the other collections' move facades are outside the Notes SDK.
- Atomic creation: `create(text=)` is two requests (metadata, then a v2 exact
  write of the initial source); a failure after creation raises
  `NoteCreatedWithoutSource` carrying the id. Unverified against a live
  server; the previous `create_note` used the legacy forced PUT.
- `find` is client-side over exact source; `case_sensitive` defaults to
  `False` like the rest of the SDK's literal searches, and the docs leave the
  default open.
- Mapped view encoding: the docs leave how `--view markdown`/`--view html`
  map onto the logical `mapped` view open; the SDK takes the HTML snapshot as
  the structured source and reproduces the annotated Markdown locally.
- Line-range out-of-bounds: standardized here as refusal.

`tests/test_notes_docs_contract.py` vendors the seven pages' Python blocks at
the pinned commit and checks every method, keyword and attribute they use
against the SDK by AST walk and introspection; the two unimplemented methods
and the eight other collections are its explicit `KNOWN_GAPS`.

Validation: `uv run --extra dev --extra dreamdb pytest -q tests test`,
`uv build`, `sphinx-build -b html docs`. Publication is a separate gate.
