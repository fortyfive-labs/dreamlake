# Ownership v1

`DreamLakeClient.ownership` exposes `capabilities(namespace)`,
`preview(namespace, request)`, `create(namespace, request)`,
`list(namespace, direction="incoming")`, `status(namespace, operation_id)`,
`accept`, `reject`, and `cancel`. Decisions use the destination namespace for
accept/reject and the source namespace for cancel. All namespace IDs are opaque;
creator history and filing relationships confer no transfer authority.

SDK 0.30.0 adds `client.projects.transfer_plan` and `client.projects.move` for a
whole Project and all its owned Notes. With a compatible backend it also includes
ordinary Project files, images and videos; episode content remains unsupported. Prefer that convenience workflow over a
hand-built partial manifest. It requires an enabled whole-Project server adapter;
check plan `executable`, preview blockers and current capabilities. Installing
the SDK does not enable backend writer, storage or RTC capabilities. Standalone
Note and Source ownership transfer remain unsupported by this workflow.

```python
from dreamlake import DreamLakeClient

client = DreamLakeClient()
incoming = client.ownership.list("acme", "incoming")
# Review the server's redacted policy/summary before accepting a request.
# client.ownership.accept("acme", incoming["operations"][0]["id"])
```

The server must support API version 1 and the requested resource adapter must be
enabled. Preview/create take the exact
versioned manifest request, including a caller-owned idempotency key. Creation
also requires the reviewed preview hash. The SDK never reconstructs permissions
or automatically retries writes. Reuse the exact request and key after an
ambiguous failure; do not regenerate a key. `OwnershipError` carries the server
code and HTTP status without retaining source content or error bodies.

After `STALE_PREVIEW`, obtain another preview. Lost authority must be resolved by
a current owner. Preparing cancellation stays fenced through cleanup. A
post-commit recovery rolls forward; reversal requires a new authorized transfer.
The source owner's preview includes the complete review inventory. Recipient
inbox/status returns the root resource, accepted policy, summary counts,
preview hash and operation state; it does not return the full manifest or
access/filing rows, bodies or secrets. Counts do not constitute a complete
access inventory. Verify the installed SDK and deployed server separately.

## Resource inspection and Source boundaries

`client.ownership.inspect(namespace, resource_type, resource_id)` returns the
current owner and server capabilities. `manifest(...)` requires namespace owner
and returns a bounded filing/structure inventory and source version. `resource_type`
is `project` or `source`; other types are rejected before transport. Use these
facts in the existing version 1 preview request. Filing never chooses ownership.
For a whole-Project move, `projects.move` uses the dedicated complete server
plan, includes every supported attached Note and direct file/image/video Node,
and rejects partial inventories. The deployed server must advertise media-file
support independently of the installed SDK.
It defaults to private resources, old access revoked and external Note filings
detached. Access or external-filing retention requires explicit opt-in in the
reviewed request.

Source execution remains independently gated; the whole-Project Notes adapter
does not enable Source transfer or migrate existing Source bindings.
A preview has no byte/owner/credential side effects. Destination Connections must
be independently authorized. Separately authorized source-retaining credential
copies support destination reseal and recovery; credential moves still require
consumer inventory and customer-KMS destination reseal remains gated. Project transfer never
implicitly copies credentials or changes the original Connection.

`restore(...)` reports structured unsupported responses for live resources:
`PROJECT_SNAPSHOT_REQUIRED` for projects with no retained snapshot and
`SOURCE_DELETE_TERMINAL` for sources. Deleted resources can return `NOT_FOUND`.
Existing Source disable/enable is reversible; deletion is terminal. Archive is
unsupported. The proposed project retention window is not shipped recovery.
