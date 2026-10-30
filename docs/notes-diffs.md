# Note diffs

## MERGE and EXACT patches (Python 0.20.0)

Read an original RTC snapshot, prepare a patch against its exact source, and
keep that snapshot while submitting the patch. MERGE is the default and sends
no `If-Match` header. The server validates the original source, compiles edits
against its original RTC identities, and uses ordinary CRDT synchronization.
Concurrent edits are handled by the existing CRDT behavior, without fuzzy
rebasing onto the newest text.

```python
import dreamlake as dl

note = dl.note("<namespace>/<note-id>")
snapshot = note.read_snapshot()
# This illustrative patch requires snapshot.content == "Hello world."
patch = "@@ chars 0:12 @@\n~ Hello [-world-]{+team+}.\n"
receipt = note.patch(patch, base_revision=snapshot.revision)
assert receipt.mode == "merge"
print(receipt.to_dict())
# Optional verification of the revision acknowledged by this request:
verified = note.read_snapshot(at=receipt.revision)
```

For a separate request that must refuse any intervening revision, select EXACT:

```python
receipt = note.patch(patch, base_revision=snapshot.revision, exact=True)
```

EXACT is per request, never a permanent document mode. `if_match=revision` is an
explicit compatibility spelling for EXACT. It can supply the original baseline
when `base_revision` is absent; when both are supplied they must match.
`format="inline-dff"` is the default; `format="diff"` accepts the line format.
The Python client transports the patch without applying DMP or fetching a newer
baseline. The API must retain the original native identity snapshot; an old
content hash alone is not an RTC baseline.

`NoteSnapshot` is immutable and carries `note`, `content`, `hash`, `revision`.
A v2 `PatchReceipt` is a separate immutable value carrying `note`, `mode`,
`base_revision`, `hash`, and `revision`. Its `to_dict()` uses the same JSON fields
as CLI/API receipts: `note`, `mode`, `baseRevision`, `hash`, `revision`.
`hash` describes content; `revision` identifies an RTC state. The receipt is not
a string and does not pretend that these two references are interchangeable.
Snapshot reads do not alter the legacy `Note`/`Doc` cache.

Missing or expired original baselines, mismatched patch source, and failed EXACT
checks are explicit errors. No HTTP patch request is automatically retried.
Preserve the draft and original snapshot on any error. For an ambiguous lost
acknowledgment, inspect the current state and reconcile before resubmitting;
separate HTTP requests do not share an idempotency receipt. Unknown baselines
raise `NoteNotFound`; EXACT conflicts raise `NoteChanged`; source mismatches
raise `PatchFailed`; unavailable RTC responses raise `NoteBusy`.

Python 0.20.0 provides this contract with the matching Notes v2 API. Use
`legacy=True` for the earlier ETag patch interface. Package and deployed
acceptance receipts are tracked in [workspace #706](https://github.com/dreamlake-ai/dreamlake-workspace/issues/706).

## Concurrent edit walkthrough

Use a private test Note containing exactly `Hello world.`. Run the first part,
then prepend `Human: ` in the browser and wait for that edit to sync. Keep the
original immutable snapshot and patch unchanged.

```python
import dreamlake as dl
from dreamlake import NoteChanged

note = dl.note("<namespace>/<test-note-id>")
original = note.read_snapshot()
assert original.content == "Hello world."
patch = "@@ chars 0:12 @@\n~ Hello [-world-]{+team+}.\n"
input('Prepend "Human: " in the browser, wait for sync, then press Enter: ')

try:
    note.patch(patch, base_revision=original.revision, exact=True)
except NoteChanged as error:
    print(type(error).__name__)  # NoteChanged: HTTP 412, no patch operations
else:
    raise AssertionError("the intervening revision should reject EXACT")

receipt = note.patch(patch, base_revision=original.revision)
print(receipt.mode)              # merge
print(receipt.base_revision)     # original.revision, unchanged
print(receipt.hash)              # content SHA-256 of the acknowledged observation
print(receipt.revision)          # RTC revision of that same observation
print(receipt.to_dict())         # note, mode, baseRevision, hash, revision
assert note.read_snapshot().content == "Human: Hello team."
assert original.content == "Hello world."
```

The SDK itself prints nothing: values are returned, failures are exceptions.
The example prints only when requested. Receipt `hash` and `revision` describe
one coherent observation after acknowledgment, not a guarantee that all peers
are synchronized or that the state remains current forever. Original native
identity preservation and zero patch writes after EXACT rejection are checked
by the paired API/RTC acceptance fixture. The SDK does not fall back from EXACT
to MERGE or overwrite the saved snapshot after a failure.

### Successful EXACT and the next MERGE request

Continue from the merged fixture, taking a new snapshot for this separate edit.
If a writer intervenes again, EXACT raises `NoteChanged`; retain this snapshot
and draft and inspect the note before deciding on another request.

```python
exact_base = note.read_snapshot()
assert exact_base.content == "Human: Hello team."
exact_patch = "@@ chars 18:18 @@\n~ {+!+}\n"
exact_receipt = note.patch(exact_patch, base_revision=exact_base.revision, exact=True)
print(exact_receipt.mode)           # exact
print(exact_receipt.base_revision)  # exact_base.revision
print(exact_receipt.hash)           # content hash of the post-ACK observation
print(exact_receipt.revision)       # RTC revision of that same observation
print(exact_receipt.to_dict())      # note, mode, baseRevision, hash, revision
assert note.read_snapshot().content == "Human: Hello team.!"

# EXACT applies to one request. This separate no-op uses the MERGE default.
next_base = note.read_snapshot()
next_receipt = note.patch("", base_revision=next_base.revision)
assert next_receipt.mode == "merge"
assert next_receipt.base_revision == next_base.revision
assert next_receipt.revision == next_base.revision
print(next_receipt.to_dict())
```

### Recorded Python fixture values

The following values were replayed through the candidate SDK from an isolated
API + RTC + Mongo fixture. Authentication, catalog, and S3 projections were
mocked. This is test evidence, not a released-package or production transcript.

```text
receipt.mode == "merge"
receipt.base_revision == "rtc:9a67f239fc8b862aa557dd10dd6512a7669861287ac8fe262d9c80afebcd18e7"
receipt.hash == "sha256:163af32ec4bc25f27e3b9ae68fe85c75e5b4436a769cc82a4050692643ce92cf"
receipt.revision == "rtc:94c1a7696f6bcd27fa880e4b38b3f73cdd3971f28b44edf9018fadf817df0f3f"
```

`receipt.to_dict()` has the same field names as CLI/API JSON:

```json
{
  "note": "507f1f77bcf86cd799439099",
  "mode": "merge",
  "baseRevision": "rtc:9a67f239fc8b862aa557dd10dd6512a7669861287ac8fe262d9c80afebcd18e7",
  "hash": "sha256:163af32ec4bc25f27e3b9ae68fe85c75e5b4436a769cc82a4050692643ce92cf",
  "revision": "rtc:94c1a7696f6bcd27fa880e4b38b3f73cdd3971f28b44edf9018fadf817df0f3f"
}
```

EXACT raised `NoteChanged` with this message, after HTTP 412 and zero patch
journal writes. The SDK emitted no stdout or stderr:

```text
patch fixture/507f1f77bcf86cd799439099: The RTC baseline changed
```

The fixture's missing-baseline response was HTTP 404 with
`error="revision_not_found"`, `message="Original RTC baseline is not retained"`.
Malformed inline input was HTTP 422 with `error="patch_failed"`,
`message="Expected inline header and one record"`. The SDK raises `NoteNotFound`
and `PatchFailed` respectively; it does not turn either response into a MERGE
retry against newer source.

### Recorded successful EXACT return value

A separate run of the same isolated API/RTC/Mongo fixture produced this receipt,
replayed through the candidate SDK. The SDK emitted no stdout or stderr; these
are returned attributes, not a published-package or production transcript.

```text
exact_receipt.mode == "exact"
exact_receipt.base_revision == "rtc:71371be6e3227abca241161ebb7d8d65e2d851b8872b5a5d51ce7ec80369d95e"
exact_receipt.hash == "sha256:2b8c0f5475f23494272f3800abb11a7680b3360bc2e927763c10aec9cf4398f5"
exact_receipt.revision == "rtc:63834c3821f7b76779815f3a670449268711811e69f86f6b208fb52236713484"
```

`exact_receipt.to_dict()`:

```json
{
  "note": "507f1f77bcf86cd799439099",
  "mode": "exact",
  "baseRevision": "rtc:71371be6e3227abca241161ebb7d8d65e2d851b8872b5a5d51ce7ec80369d95e",
  "hash": "sha256:2b8c0f5475f23494272f3800abb11a7680b3360bc2e927763c10aec9cf4398f5",
  "revision": "rtc:63834c3821f7b76779815f3a670449268711811e69f86f6b208fb52236713484"
}
```

The next empty patch omitted `exact=True`, returned `mode == "merge"`, and kept
the same hash and revision. Its `to_dict()` was:

```json
{
  "note": "507f1f77bcf86cd799439099",
  "mode": "merge",
  "baseRevision": "rtc:63834c3821f7b76779815f3a670449268711811e69f86f6b208fb52236713484",
  "hash": "sha256:2b8c0f5475f23494272f3800abb11a7680b3360bc2e927763c10aec9cf4398f5",
  "revision": "rtc:63834c3821f7b76779815f3a670449268711811e69f86f6b208fb52236713484"
}
```

## Legacy ETag compatibility

`read()` still returns the editable `Doc`; `Doc.save()`, whole-body writes, and
section writes retain their previous ETag behavior. For old unified-diff callers,
select `patch(..., legacy=True)` explicitly. That path continues to return
`PatchResult(str)` with `.etag` and `.size_bytes`, and retains the previous cached
ETag precondition. A legacy content ETag must not be passed as a v2 RTC baseline.

## Legacy comparisons against your last read or edit

The legacy `note.read()` body read returns `doc.etag`, a quoted SHA-256 hash of the complete text.
Keep it as a revision reference, including across processes:

```python
import dreamlake as dl

note = dl.note("<note-id>")
doc = note.read()
ref = doc.etag

# Later, including edits made by collaborators:
print(note.diff(since=ref))
print(note.diff())          # defaults to this handle's last read/edit ETag

# Apply your patch against the exact revision you used to prepare it:
result = note.patch(my_diff, legacy=True, if_match=ref)
ref = result.etag
```

The server retains note-scoped content snapshots for body/section reads and API
writes. Identical content has the same hash; a ref identifies text, not an RTC
operation or author. Partial body reads return the hash of the complete body.
Diff inspection does not advance the handle's cached revision or write
precondition. Use `read()`/`refresh()` when you want to adopt the latest state.

`GET /namespaces/:slug/notes/:noteId/diff?since=<etag>` returns `diff`, `from`,
`to`, and `etag` (the current ref). `context` accepts 0–100 lines, default 3.
The endpoint enforces current note read permissions. Unknown or unavailable
snapshots return 404, never an empty-baseline diff. Old ETags issued before
snapshot retention was deployed may be unavailable. Retention requires the
matching server deployment; the SDK does not infer historical bytes.

## Inspect and patch a local draft

Read a note once, inspect edits, apply unified diffs locally, and save against
that snapshot's revision:

```python
note = dl.note("<note-id>")
doc = note.read()
doc.replace("Published", query="Draft", all=True)
print(doc.diff(since="last_edit"))  # the latest local change
print(doc.diff())                   # all changes since read or successful save

doc.patch(unified_diff)             # edits the draft; returns its full text
doc.save()                         # conditional remote write
```

`since="last_edit"` compares the current draft with its contents immediately
before its latest content-changing operation, including element edits, patches,
and `revert()`. Before any edit it returns an empty string. No-op edits and
successful saves preserve this comparison; a new `read()` creates a new draft.
It does not retrieve other collaborators' history or persisted server revisions.

`doc.patch(diff)` accepts a single-file unified diff, including diffs from
`doc.diff()`, `diff -u`, and git. Hunk positions and context must match exactly;
there is no fuzzy relocation. Invalid patches raise `EditError` and leave both
the draft and last-edit comparison unchanged. Empty diffs do nothing. Diffs
preserve Unicode, CRLF, and missing final newlines. `context=0` is supported;
the default for `diff()` is three context lines.

To apply a patch directly to the remote note, use the existing API:

```python
result = note.patch(unified_diff, legacy=True, if_match=doc.etag)
```

The CLI equivalent is:

```bash
dreamlake notes patch "$NOTE_ID" --legacy --file change.patch --if-match '"<etag>"'
```

Remote writes reject stale revisions. A local patch is committed only when
`doc.save()` succeeds; a rejected save retains the draft for inspection.

## Original-revision append (Python 0.26.0 release candidate)

This candidate changes `note.append(text)` from a cached whole-body PUT returning
an ETag string to a native original-revision insertion returning `PatchReceipt`.
Requires Python 0.26.0; publication acceptance is tracked separately. Read exact
source once, retain it, and append:

```python
original = note.read_snapshot()
receipt = note.append("New paragraph", at=original)
verified = note.read_snapshot(at=receipt.revision)
assert verified.hash == receipt.hash
```

Without `at`, append reads one fresh hash-verified snapshot. With `at`, it validates
Note identity, source hash and the original opaque revision before any network
request; it never substitutes current source. The insertion uses Unicode code
point positions and escapes inline framing and delimiter punctuation (`+`,
`-`) in the canonical form the CLI and server use, preserving CRLF and LF bytes.
An existing nonempty body lacking a final LF receives one LF before the addition,
including when the addition is empty. A completely unchanged MERGE returns an
observational receipt without PATCH or a new commit; it does not claim current
revision verification. `exact=True` submits even an unchanged patch so the server
checks the saved baseline. No request is automatically replayed.

Retained `read_snapshot(at=revision)` verifies the returned revision and source
hash even if another edit has since arrived. `if_match=revision` instead checks
current source; combining the two is rejected. A successful patch updates the
legacy cached ETag only in its content-hash domain, never to an RTC revision.
Unknown write outcomes preserve caches and drafts: keep the original baseline
and draft, inspect retained/current source, and reconcile before resubmitting.

For previous cached-text/LF/ETag PUT behavior and string return values, choose
`note.append(text, legacy=True)`. Existing `force=True` also selects that legacy
path. Combining either with `at` or `exact` is rejected before HTTP. `Doc.save()`,
whole-body writes and section APIs retain their existing behavior.

## Native body drafts (Python 0.27.0 release candidate)

Python 0.27.0 changes `note.write(text)` from whole-body PUT to disjoint
Unicode-codepoint patches against original RTC source. `at=original_snapshot`
pins identity and source without GET; without it one verified snapshot is read
before planning. Unchanged gaps remain untouched, and alignment work/trace/source
limits fail before PATCH instead of replacing a coarse region. No automatic
replay occurs. Explicit `legacy=True`, existing `force=True`, or legacy
`if_match=etag` retain the ETag PUT/string contract; modern `at`/`exact` cannot
be mixed with those compatibility options.

`note.read()` creates a modern editable Doc retaining its original immutable
snapshot. Edits remain local. `doc.save()` submits against that same snapshot,
returns `PatchReceipt`, and reads the acknowledged retained revision to adopt
its coherent merged content as the next draft/original. A different write through
the Note handle does not move this draft's baseline. `exact=True` checks even
unchanged drafts; unchanged MERGE returns an observational receipt without a new
commit or a current-revision claim. `note.read(at=revision)` opens retained source.

If post-ACK readback fails, the raised `NoteError.receipt` contains the known
acknowledgment; draft and original source stay available. Further save calls are
refused until the caller reconciles: inspect `note.read_snapshot(at=receipt.revision)`
and current source, then explicitly open a new draft and reapply intended edits.
This failure is not a rollback and must not be blindly resubmitted. Ambiguous
PATCH failures retain drafts and snapshots without adopting a newer baseline.

For legacy editable drafts, choose `note.read(legacy=True)` or the legacy ETag
verification `if_match=etag`; their save behavior and `SaveResult` are retained.
`doc.save(legacy=True)` / `force=True` explicitly choose legacy body behavior.
Publication acceptance remains separate. Partial reads, section writes and section
selector mapping remain legacy and are a separate pending migration; their
ranges must not be guessed into original native snapshot coordinates.

## Full source reads and remote inspection (Python 0.28.0 release candidate)

Default `note.refresh()` and `note.text` read full source through verified Notes
v2 snapshots. Cached ETags stay in the quoted content-hash domain; RTC revisions
stay on immutable snapshots. `note.read()` also populates that verified cache,
while existing Docs retain their own original snapshot even after another refresh.
`read_snapshot()` itself still leaves caches unchanged. Explicit
`refresh(legacy=True)` and `get_text(legacy=True)` preserve older body-read behavior.
Legacy append and legacy editable reads select that path explicitly on cache miss.
No v2 failure silently falls back to a legacy read.

`note.diff(since=content_hash)` uses the shipped v2 body inspection endpoint and
continues returning a diff string. It checks Note, base hash, format and revision,
then reads the retained acknowledged revision to verify its source hash. Inspection
updates no cache or draft baseline. `since` is a content hash (raw, quoted ETag or
sha256-prefixed), never an RTC revision or a native patch baseRevision. Omitting
since uses this handle's last full read/edit content-hash ETag. The returned string
is inspection material; retaining only its hash does not establish a writable
original identity snapshot.

The v2 endpoint has fixed diff formatting and no context-selection parameter.
Explicit nondefault context selects the retained compatibility `/diff` endpoint
before transport, preserving existing calls; `legacy=True` also selects it. This
is option-based compatibility, never a fallback after a failed v2 response. V2
context selection remains an unresolved Namespace-owned endpoint capability. There is no existing Python DiffResult value; no new
protocol or parser is introduced. Sections and partial reads still await their
retained range/discovery contracts and remain an explicit separate migration.
Publication acceptance for Python 0.28.0 remains separate.

## Callable facade, command identity and focused reads (Phase 1, unreleased)

The agreed public syntax, shared with the CLI and TypeScript tracks. `dl.notes`
is callable and carries `list`; nothing else. Opening is local and synchronous.

```python
import dreamlake as dl

note = dl.notes("fortyfive/note-id")      # no request; a slug resolves on first use
snapshot = note.read()                    # NoteSnapshot {note, content, hash, revision}
receipt = note.append(text="Hello", at=snapshot, mode="exact")
notes = dl.notes.list(namespace="fortyfive")
```

`mode` is required on every facade write (`"exact"` or `"merge"`); there is no
default. `at` is the snapshot `read()` returned or a bare `rtc:` revision; a
bare revision has its retained source read back before the edit is planned. A
`sha256:` content hash where a revision belongs raises `RevisionExpected`.
Input containing `<!-- notes-reading-view` raises `ReadingViewRefused`:
generated annotations are never saved. The facade is an adapter over the
existing `Note` methods, so the request bytes are those `note.append(text,
at=snapshot, exact=True)` already sends, separator rule included. The existing
`dl.note()`, `read_snapshot()`, `append()`, `patch()` and `write()` are
unchanged; `DreamLakeClient.notes` is the same facade bound to one client.

`note.patch(patch=..., at=..., mode=...)` and `note.write(text=..., at=...,
mode=...)` are exposed on the handle but PROVISIONAL: their names and shapes
may change, and only `read`, `append` and `list` are agreed. Every facade
operation takes named fields only; the payload is not positional.

`note.read(lines=(40, 80))` is a focused read: 1-based inclusive source lines,
two integers required. It makes the same v2 request and slices locally,
returning `FocusedRead {note, hash, revision, scope, lines, chars, content,
complete=False, next}` where `scope` is `{"lines": [a, b]}` (the range
actually covered), `chars` is the slice's code-point `[start, end)` in the
whole source (CJK and emoji count one each) and `hash`/`revision` describe
the whole note. It is not a `NoteSnapshot`: `write`/`append` refuse
it with `SnapshotRequired`; `patch(at=focused)` targets its `chars`.

`command_id` is opt-in on `append`/`patch`/`write` (facade and existing
methods) until the server that records command identity is deployed. Without
one nothing new is sent. With one (`[A-Za-z0-9._:-]{8,128}`), it is sent as
`commandId`, the receipt must echo it (`CommandIdentityUnsupportedError`
otherwise), and any transport failure once a connection was attempted, an
unknown status or a non-receipt response raises `UncertainOutcomeError`
carrying the id (errors raised before any connection attempt, such as an
unsupported URL scheme, stay plain `NoteError`): resend with the same id, or call
`note.command_status(command_id)`. A 409 about the id itself is typed:
`command_outcome_unknown` is `UncertainOutcomeError`, `command_in_progress`
is `CommandInProgressError` (wait, then query status), `command_intent_mismatch`
is `CommandIntentMismatchError` (a different intent needs a new id). `PatchReceipt` gains `outcome`
(`accepted` | `no-write`), `command_id` and `replayed`, each `None` when the
server did not send it.

```python
receipt = note.append(text="Hello", at=snapshot, mode="exact", command_id="run-42.step-7")
assert receipt.command_id == "run-42.step-7" and receipt.outcome == "accepted"
status = note.command_status("run-42.step-7")   # state, outcome, receipt
```

`tests/notes-conformance.fixtures.json` records the seven shared scenarios;
the CLI/TS track writes the same file from its implementation and the two are
diffed.
