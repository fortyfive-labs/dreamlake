# Note diffs

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
result = note.patch(my_diff, if_match=ref)
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
result = note.patch(unified_diff, if_match=doc.etag)
```

The CLI equivalent is:

```bash
dreamlake notes patch "$NOTE_ID" --file change.patch --if-match '"<etag>"'
```

Remote writes reject stale revisions. A local patch is committed only when
`doc.save()` succeeds; a rejected save retains the draft for inspection.
