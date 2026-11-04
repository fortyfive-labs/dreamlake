# Agents and runnable definitions

`DreamLakeClient.runnables` creates and manages first-class runnable resources.
An agent is a runnable with `kind="agent"`; creating its definition does not
start a session. This API requires the companion runnable-content and archival
backend release. These examples describe the new SDK source; older installed
versions may not expose `client.runnables`.

## Inline instructions

```python
from dreamlake import DreamLakeClient
from dreamlake.api.runnables import RunnableCreate

client = DreamLakeClient()  # Uses DREAMLAKE_API_KEY or your saved login.
definition: RunnableCreate = {
    "name": "agents.research",
    "kind": "agent",
    "description": "Read selected notes and summarize their evidence.",
    "markdown": "Read the notes the user selects. Cite their titles and explain uncertainty.",
    "metadata": {
        "agent": {
            "runtime": "builtin-v1",
            "tools": ["dreamlake_notes_read"],
        },
    },
    "projects": ["dreamlake"],
}
agent = client.runnables.create("fortyfive", definition)
print(agent["id"])
```

A dotted `name` supplies module and qualified name. Alternatively provide
`module` and `qualname`, with an optional display `name`. Project slugs in
`projects` create actual project filings in the same transaction. Missing
projects or insufficient filing authority reject the whole create.

## Main instruction note and skills

Use existing note and folder database IDs. The main instruction note is required;
optional skills are individual notes or folders of notes. Notes must be readable
by the caller and belong to the runnable's namespace; folders must be readable
project folders in that namespace. Defining an agent grants no note access to
other people. At selection and execution, the built-in runtime reads referenced
notes with the executing caller's permissions and checks their revisions.

```python
import os
from dreamlake import DreamLakeClient

client = DreamLakeClient()
agent = client.runnables.create("fortyfive", {
    "module": "agents",
    "qualname": "writer",
    "name": "Writing assistant",
    "kind": "agent",
    "content": {
        "instructionNoteId": os.environ["INSTRUCTION_NOTE_ID"],
        "skills": [
            {"kind": "note", "id": os.environ["STYLE_NOTE_ID"]},
            {"kind": "folder", "id": os.environ["SKILLS_FOLDER_ID"]},
        ],
    },
    "metadata": {"agent": {"runtime": "builtin-v1", "tools": ["dreamlake_notes_read"]}},
    "projects": ["dreamlake"],
})
```

Choose `content` or inline `markdown`. To switch an existing inline definition
to note references, update with `markdown=None` and `content={...}`. To switch
back, send `content=None` and `markdown="..."`. Up to 20 skill references are
accepted. The built-in runtime bounds expansion to 20 folders, 200 children per
folder, 50 distinct notes and 16,000 characters of combined instructions.

## Read, edit, archive, restore and delete

```python
page = client.runnables.list("fortyfive", kind="agent", page=1, page_size=50)
agent = client.runnables.get("fortyfive", agent["id"])
updated = client.runnables.update("fortyfive", agent["id"], {
    "description": "Summarize evidence from the selected notes.",
    "expectedUpdatedAt": agent["updatedAt"],
})
archived = client.runnables.archive(
    "fortyfive", updated["id"], expected_updated_at=updated["updatedAt"],
)
all_agents = client.runnables.list("fortyfive", kind="agent", include_archived=True)
restored = client.runnables.restore(
    "fortyfive", archived["id"], expected_updated_at=archived["updatedAt"],
)
# Soft-delete only when the definition is no longer needed:
# client.runnables.delete("fortyfive", restored["id"])
```

Lists return `runnables`, `page`, `pageSize`, `total`, and `totalPages`.
`include_archived=True` includes both active and archived definitions. Archival
retains instructions, versions and project filings; unrelated edits preserve
archive state. Restore clears archival only and cannot revive a deleted
runnable. Delete removes project filings while retaining immutable version
history.

Updates leave omitted fields unchanged. `metadata`, when supplied, replaces the
metadata object; read the latest definition and preserve unrelated keys when
editing its agent spec. `expectedUpdatedAt` compares the server timestamp
atomically; a stale save raises `RunnableError` with status `409`. Preserve the
local draft, reload, reconcile and submit a new update. The SDK never retries a
mutation automatically. Server errors expose `status` and `code`, not their
response body.

Explicit project READ/WRITE grants can grant catalog reads and definition
updates through live filings. They do not grant deletion, redistribution,
version publication or execution rights. Source notes retain their own access
checks. Namespace authority still applies to creating or deleting runnables.

Existing definitions can also be filed through
`client.projects.attach(namespace, project, "runnable", agent_id)` and detached
with `client.projects.detach(...)`. Use the database ID for attachments.
