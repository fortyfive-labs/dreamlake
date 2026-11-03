# Infrastructure registration moves

`DreamLakeClient().infra` reads stack metadata and moves registrations through the
same reviewed API used by the CLI. It does not create, destroy, or alter cloud
resources. Use Terraform for infrastructure changes. A move preserves the stack
UUID, Terraform state backend URL, version history, and state contents.

```python
import json
import os
from pathlib import Path
from dreamlake import DreamLakeClient

infra = DreamLakeClient().infra  # uses the saved DreamLake login
stack_id = "11111111-1111-4111-8111-111111111111"
review = infra.preview_move(
    stack_id,
    destination_namespace="acme",
    destination_project="infrastructure-archive",
    # Optional destination_name and destination_path change metadata placement.
)
fd = os.open("stack-move-review.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
with os.fdopen(fd, "w") as output:
    json.dump(review, output, indent=2)
```

Review `stack`, `source`, `destination`, `request`, `executable`, and `blockers`.
For cross-project moves, an omitted destination path preserves the current path;
the server includes the source and destination paths in the saved request.
Explicit `destination_path="archive/examples/example"` chooses a different path.
Destination collisions and Terraform locks block execution. The server checks
source/destination authority, state version/digest, and reviewed source placement
again at execution.

```python
review = json.loads(Path("stack-move-review.json").read_text())
receipt = infra.execute_move(stack_id, review, confirm=True)
assert receipt["stack"]["id"] == stack_id
assert receipt["stack"]["version"] == review["stack"]["version"]
```

Execution sends the exact saved request and idempotency key, without automatic
retries or a fresh preview. On `InfrastructureError.outcome == "unknown"`, inspect
`infra.status(stack_id)` and reconcile before retrying the same saved review.
The error retains `.stack_id` and `.operation_id`; server reconciliation 409s
also carry an unknown outcome. A normal stale-review 409 is rejected.
Never replace the registration or create a second Terraform state backend.
Stale reviews require a new preview; blocked reviews cannot execute.

This is the reviewed registration move API (`/move/preview`, `/move`). The separate
recipient-acceptance transfer lifecycle is not enabled by these methods.
