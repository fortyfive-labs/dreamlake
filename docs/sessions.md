# Agent session registry

`DreamLakeClient().sessions` reads the shared Codex/Claude/DreamLake agent registry
through the existing account-authenticated REST API. It does not start, resume,
attach, send input, upload transcripts, or create users/services.

```python
from dreamlake import DreamLakeClient

sessions = DreamLakeClient().sessions
page = sessions.search('geyang', 'repair', search_in='title', page_size=25)
for row in page['sessions']:
    print(row['id'], row['provider'], row['runState'], row.get('attention'))
    print(sessions.reference('geyang', row['id']))
# Preserve the same filters when requesting page['nextCursor'].
```

`list(namespace, ...)` and `search(namespace, query, ...)` return one page with
`sessions`, `hasMore`, and `nextCursor`. Options are `q` (list), `search_in`
(`title` or `all`, omitted means the existing broad search), `provider`,
`machine_id`, `page_size` (1–100), `cursor`, `sort` (`recent`/`created`), and
`archived` (`exclude`/`include`/`only`). Title filtering runs before pagination.
Start a new cursor sequence when filters change. `available(owner=..., team=...,
...)` explicitly searches readable sessions across owners; these filters grant
no access. Query/permission failures are not retried against another namespace.

`get(namespace, registry_id)` returns metadata without execution authority.
The ID is the DreamLake 24-character registry ID, not a native provider UUID;
`vendorSessionId` identifies the provider conversation. Descriptors retain
server-provided host/enrollment/machine/Unix-user binding, `archiveOnly`,
`canManage`, observation timestamps, fresh `runState` and durable `attention`.
A binding is not proof that transport is connected. Silence makes live runtime
unknown; it does not prove a process stopped or resolve waiting/error attention.

`attention(namespace)` is the personal owner's durable summary: total
`needsInput`/`error` counts and at most 20 newest previews. Organization/foreign
owner access is denied. `reference(namespace, registry_id)` returns a native
memo directive `:session[namespace/id]` without a network request.

`targets(namespace)` reads authorized enrolled SSH targets; `target(namespace, registry_id)`
resolves one exact saved binding under the existing execution-read authorization.
Neither issues a ticket nor attaches/starts a process.

No host default is inferred. Multiple Unix-user enrollment under one existing
host name is not currently supported by the enrollment path. Nymph discovery
stays within its enrolled Unix user and configured provider roots.

These methods use the configured client token, remote and borrowed transport.
`SessionError` carries a sanitized `code` and HTTP `status` (or `None` for
transport failures). No PTY, GraphQL session wrapper, or MCP server is provided
by this SDK. Existing CLI/GUI terminal operations remain separate.

## Open a Thread in the browser

Use the **Threads** sidebar entry for the selected organization. The
**Sessions** entry at the bottom opens native sessions. To link to a saved Thread:

```python
from dreamlake.threads import thread_url, threads_url

threads_url("example-org")
thread_url(
    "1ad5a956-c5fa-47c0-a9dc-9d29d2612c42",
    session="saved-session",
)
```

Both functions return a URL without making API calls. Saved layouts are
available in the browser and account where they were saved. A link grants
no access and does not start a terminal.

## Saved Thread order

Use the authenticated `DreamLakeClient` to read and change your order in one
namespace. The browser registers saved Thread IDs and names. Layout contents
stay in the browser.

```python
from dreamlake.api._client import DreamLakeClient

client = DreamLakeClient()
order = client.threads.order("fortyfive")
ids = [thread["id"] for thread in order["threads"]]
client.threads.reorder("fortyfive", ids[::-1], expected_revision=order["revision"])
```

Provide each returned ID once. On HTTP 409, read the current order again before
retrying. A server that has not released Thread order returns HTTP 404.

### Personal session deletion

Use the same list preference for registered sessions and uploads. Supported native
identities include the exact Source ID, provider, native session ID, and any component
ID. A delete hides the row from the caller's active and archived lists. The saved
session, transcript and runtime remain available through their usual authorized APIs.

```python
identity = {"kind": "registered", "sessionId": session_id}
entry, = client.sessions.presentation("fortyfive", [identity])
receipt = client.sessions.delete_from_list(
    "fortyfive", identity, expected_revision=entry["revision"]
)
client.sessions.delete_from_list(
    "fortyfive", identity, expected_revision=receipt["entry"]["revision"], deleted=False
)
```
