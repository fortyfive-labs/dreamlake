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

## Legacy ETag compatibility

`read()` still returns the editable `Doc`; `Doc.save()`, whole-body writes, and
section writes retain their previous ETag behavior. For old unified-diff callers,
select `patch(..., legacy=True)` explicitly. That path continues to return
`PatchResult(str)` with `.etag` and `.size_bytes`, and retains the previous cached
ETag precondition. A legacy content ETag must not be passed as a v2 RTC baseline.

## Compare against your own last read or edit

Every body read returns `doc.etag`, a quoted SHA-256 hash of the complete text.
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
