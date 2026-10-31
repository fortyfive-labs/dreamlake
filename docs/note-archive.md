# Archive notes and folders

Archival hides completed work from active listings while retaining its body,
attachments, folder placement and bindr membership. It is independent of tags
and soft deletion. Available in Python SDK 0.33.0 with the archive-capable server.

```python
import dreamlake as dl

note = dl.note("my-namespace/design-doc")
archived = note.archive()
print(archived.archived_at)  # server timestamp
print(note.metadata().archived_at)  # reading does not restore it
note.unarchive()

# Active notes by default; True returns active AND archived notes.
notes = dl.list_notes("my-namespace", include_archived=True)
matches = dl.search_notes("design", namespace="my-namespace", include_archived=True)
```

`archive()`, `unarchive()` and `metadata()` return a `NoteRef`. Its nullable
`archived_at` field is also populated in list/search results. A missing field
from an older server is treated as `None`. Opening an archived note by slug or
ID works without restoring it. Archive operations preserve any cached body,
original revision and local editing draft.

Archiving uses a separate metadata PATCH with only `archived: true` or
`archived: false`. It does not send an edit or a body revision. The server first
flushes pending note content; an unsafe flush raises `NoteBusy` (HTTP 409/503).
Permission failures raise `NoteReadOnly`, missing/inaccessible notes raise
`NoteNotFound`. These operations do not automatically retry a failed request.

## Folders, project resources and bindrs

```python
from dreamlake.api._client import DreamLakeClient

client = DreamLakeClient()  # uses the saved CLI login
client.archive_folder(folder_node_id)
client.unarchive_folder(folder_node_id)

children = client.get_node_children(folder_node_id, include_archived=True)
roots = client.get_node_children(namespace="my-namespace", project="my-project",
                                 include_archived=True)
resources = client.projects.resources("my-namespace", "my-project",
                                      include_archived=True)
members = client.projects.resources("my-namespace", "my-project",
                                    bindr=bindr_id, include_archived=True)
```

Folder mutations return the server's node dictionary and raise
`httpx.HTTPStatusError` on rejection, matching other node helpers. Only folders
can be archived through `/nodes/:id`; notes use `Note.archive()` even when they
have a project mount. Project resource requests use `ProjectAccessError` for
server rejection. Listing methods retain server pagination metadata and raw
`archivedAt` fields. Filters apply before pagination and counts.

`get_node_children(..., search="design", recursive=True, after=cursor)` supports
recursive folder search. Keep `include_archived` and the search scope unchanged
when continuing a cursor. `get_node_descendants()`, `grep_notes()` and
`shared_with_me()` also accept `include_archived=False`. Use `True` to include
archived items. Grep cursors are bound to the archive filter just like recursive
folder search cursors. Direct note metadata reads remain available when archived.


## Editing restores the folder path

A successful saved content or metadata change restores the edited item and its
live archived ancestor folders, including note placement paths. Explicit
unarchive does the same. Reads, unchanged saves and failed writes do not restore
anything. Independently archived siblings stay archived; soft-deleted items
stay deleted. These rules are enforced by the server, so Python body editing,
the standalone CLI and the web editor share the same behavior. The Python SDK
has a synchronous API; no separate async archive interface is introduced.
