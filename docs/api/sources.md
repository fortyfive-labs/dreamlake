# Source management (draft SDK)

`client.sources` provides `detail`, `editors`, `add_editor`, `remove_editor`,
`disable`, `enable`, and terminal `delete` against existing Source v1 routes.
All methods take namespace slug and source ID. Editor mutations also take
subject type (`USER`, `TEAM`, `SERVICE_ACCOUNT`) and subject ID; the server
rejects unsupported or ineligible subjects. No method infers authority from
project filing or creator history. Editors grant EDIT only; management requires
current namespace-derived Source ADMIN.

Writes require an explicit `idempotency_key`. Reuse the exact key/arguments for
an explicit retry after an uncertain result; the SDK does not auto-retry or
create a new key. Disable/enable is reversible even when external storage cannot
be browsed. Delete queues terminal managed cleanup and returns a 202 operation
handle; it does not delete external buckets or shared Connections and does not
mean cleanup completed. Restore-after-delete and archive are unsupported.

Use `client.ownership.inspect`/`manifest` to inspect ownership and preflight
constraints. Ownership transfer execution remains gated in package C.


## Custom static visualization

`client.sources.publish_visualization(namespace, source_id, directory,
entry="index.html")` publishes a static build into the Source and returns its
Visualize URL. It uses a new directory beneath `.dreamlake/visualization/`, then
conditionally updates the managed `.dreamrc` without replacing other YAML keys.
It creates no Artifact resource or independent permissions.

```python
from dreamlake import DreamLakeClient

client = DreamLakeClient()
result = client.sources.publish_visualization("my-team", "SOURCE_ID", "./dist")
print(result["url"])
```

Build with relative asset URLs. See the canonical
[custom Source visualization guide](https://docs.dreamlake.ai/sources/custom-visualization)
for limits, external configuration handling, browser data access and permissions.
