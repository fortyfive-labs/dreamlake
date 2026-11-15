# Python client sessions and transport

`DreamLakeClient`, `RemoteClient`, and remote or hybrid `Episode` instances use
one credential order: an explicit client token, then `DREAMLAKE_API_KEY`, then
the session saved by `dreamlake login`. `Episode` delegates to `RemoteClient`.
Local Episodes do not need credentials. `DreamLakeClient` supports anonymous
requests; `RemoteClient` and remote Episodes raise `NotAuthenticatedError` when
no token is available.

`DreamLakeClient(transport=...)` applies the supplied synchronous httpx transport
to all DreamLake API requests, including upload authorization, node registration,
node lookup, account identity, downloads, and Bindrs. Existing request timeouts
and HTTP error propagation are preserved. An injected transport is borrowed:
the caller must keep it open for all SDK operations and close it afterward
(for example, wrap the whole sequence in `with transport:`). Individual
`client.http()` contexts close their HTTP clients without closing that shared
transport. Without injection, each HTTP client owns and closes its connection
pools, including after failed requests. Signed storage transfers and Qdrant
use their separate service connections; they do not inherit the DreamLake token.

The Python Bindr helpers retain their project/name arguments and flat
`{id, members, ...}` return shape. Names resolve exactly through the project
Bindr listing; reads then use `/bindrs/:id?recursive=true` and collect every member page.
Membership writes send one `{nodeId}` per request. `create_bindr()` atomically
creates the Bindr with its first node; additional nodes are filed sequentially.
`BindrMembershipError` (from `dreamlake.api._client`) exposes `bindr_id`, `completed_node_ids`, and
`failed_node_id`, and chains the original HTTP error. Confirmed filings remain;
a transport failure may leave the failed filing's outcome unknown.

Node ID strings, `{nodeId: ...}`, and legacy `{type: "node", id: ...}` inputs
are accepted. Legacy Bindr-as-member inputs and `dl.bindr(bindrs=[...])` are
rejected before any request: the current API represents nesting through a
Bindr's `parent`, and its reads include child filings. Downloads recognize
canonical `{nodeId: ...}` members.

## Source visibility

Source visibility is independent of signed-in share links and editors. Private
Sources keep their current authorized audience; Unlisted Sources are readable
by link but excluded from public profiles and listings; Public Sources are
listed for visitors. Link access is read-only and covers the whole Source.

```python
from uuid import uuid4

source = client.sources.detail("team", source_id)
client.sources.set_visibility(
    "team", source_id, "unlisted",
    expected_settings_revision=source["settingsRevision"],
    idempotency_key=str(uuid4()),
)
```

A stale revision is rejected. No request is automatically retried. On a lost or
invalid acknowledgment, read `detail()` again before deciding whether to retry.
The `metadata()` method accepts the same visibility and revision arguments.
