# Note diffs

## MERGE and EXACT patches (Python 0.20.0 candidate)

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
verified = note.read_snapshot(if_match=receipt.revision)
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

This contract requires the matching deployed API. The package release, docs
publication, and fresh public installation are separate release gates.

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
