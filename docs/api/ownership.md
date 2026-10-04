# Ownership v1 (unreleased)

`DreamLakeClient.ownership` exposes `capabilities(namespace)`,
`preview(namespace, request)`, `create(namespace, request)`,
`list(namespace, direction="incoming")`, `status(namespace, operation_id)`,
`accept`, `reject`, and `cancel`. Decisions use the destination namespace for
accept/reject and the source namespace for cancel. All namespace IDs are opaque;
creator history and filing relationships confer no transfer authority.

```python
from dreamlake.api._client import DreamLakeClient

client = DreamLakeClient()
incoming = client.ownership.list("acme", "incoming")
# Review the server's redacted manifest summary before accepting a request.
# client.ownership.accept("acme", incoming["operations"][0]["id"])
```

The server must support API version 1. Resource adapters remain gated until their
writer, storage and collaboration checks pass. Preview/create take the exact
versioned manifest request, including a caller-owned idempotency key. Creation
also requires the reviewed preview hash. The SDK never reconstructs permissions
or automatically retries writes. Reuse the exact request and key after an
ambiguous failure; do not regenerate a key. `OwnershipError` carries the server
code and HTTP status without retaining source content or error bodies.

After `STALE_PREVIEW`, obtain another preview. Lost authority must be resolved by
a current owner. Preparing cancellation stays fenced through cleanup. A
post-commit recovery rolls forward; reversal requires a new authorized transfer.
This draft does not prove deployed server or installed SDK availability.
