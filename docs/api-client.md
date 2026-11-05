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
and HTTP error propagation are preserved. Signed storage transfers and Qdrant
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
