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
