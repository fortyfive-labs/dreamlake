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

## Project/source preflight (package C draft)

`client.ownership.inspect(namespace, resource_type, resource_id)` returns the
current owner and server capabilities. `manifest(...)` requires namespace owner
and returns a bounded filing/structure inventory and source version. `resource_type`
is `project` or `source`; other types are rejected before transport. Use these
facts in the existing version 1 preview request. Filing never chooses ownership:
select resources explicitly and approve cross-owner detachments.

C execution remains gated on writer epochs, atomic cutover and storage recovery.
A preview has no byte/owner/credential side effects. Destination Connections must
be independently authorized; shared credential export/reseal remains gated on B.

`restore(...)` reports structured unsupported responses for live resources:
`PROJECT_SNAPSHOT_REQUIRED` for projects with no retained snapshot and
`SOURCE_DELETE_TERMINAL` for sources. Deleted resources can return `NOT_FOUND`.
Existing Source disable/enable is reversible; deletion is terminal. Archive is
unsupported. The proposed project retention window is not shipped recovery.
