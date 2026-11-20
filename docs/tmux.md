# Named tmux sessions

`DreamLakeClient.tmux` exposes the same named-terminal contract as
`dl tmux new NAME --host HOST` and the command palette's
`/tmux new NAME --host HOST`. It requires a capable backend, terminal gateway
and enrolled Nymph. An unsupported deployment fails explicitly; a local SDK
upgrade does not establish that the host has the required protocol.

```python
from uuid import uuid4
from dreamlake import DreamLakeClient
from dreamlake.api import TmuxReference, parse_tmux_reference

client = DreamLakeClient(dl_url="https://api.dreamlake.ai", token=token)
enrollment = "aaaaaaaaaaaaaaaaaaaaaaaa"
request_id = str(uuid4())  # Persist before creating, so an uncertain write is recoverable.
created = client.tmux.create(enrollment, request_id=request_id, name="work")
operation = client.tmux.operation(enrollment, request_id, expected_name="work")

reference = parse_tmux_reference(
    ":tmux[aaaaaaaaaaaaaaaaaaaaaaaa/bbbbbbbbbbbbbbbb/work]"
)
resolved = client.tmux.resolve(reference)
inventory = client.tmux.list("geyang", host_id, enrollment)
```

`create` submits one `POST /terminal-references/:enrollment/tmux/sessions` with
`{requestId, name?}`. Omit `name` to request a fresh generated name. Explicit new
names contain 1–64 letters, digits, underscores or hyphens. An existing name is
a conflict; this operation never attaches, replaces a process, or sends input
into another session. The operation returns `pending`, `succeeded` or `failed`.
A pending receipt does not confirm successful creation.

If a response is lost, reuse the same request UUID and original arguments.
`operation` reads `GET /terminal-references/:enrollment/tmux/operations/:requestId`
and never resubmits creation. Its optional `expected_name` validates that the
receipt still describes your submitted name; use `None` for an omitted name.
`requestedName` preserves the original input, while a successful result's
`reference.session` is the actual server-issued name, including any required
connection-grant ownership prefix. Use the returned `directive` rather than
reconstructing that prefix. No SDK method retries a request automatically.

A durable reference is `:tmux[ENROLLMENT/SERVER/PERCENT_ENCODED_EXACT_NAME]`.
Enrollment IDs are 24 lowercase hexadecimal characters; server IDs are 16.
`TmuxReference(enrollment, server, session).directive()` percent-encodes the name
once and does not disclose a socket path. Existing reference names preserve
literal Unicode text up to 256 characters, without control characters. Old
namespace/machine/user handles and numeric tmux session IDs are not references.
Ephemeral browser shell tabs do not receive persistent tmux references.

`resolve` reads cached metadata and verifies the returned enrollment, server and
exact name. `list` reads the enrolled host's cached multiplexer snapshot. Neither
operation triggers a new scan, creates a terminal or attaches. An unavailable,
stale or unsupported reference stays unavailable; there is no default-socket or
plain-shell fallback. Saved agent conversation references use `:session[...]`
and remain distinct from live named tmux terminals.

## Attach-only gateway ticket

Use a separately configured client for the terminal gateway and pass its allowed
web origin explicitly. No gateway is inferred from a custom API environment.

```python
gateway = DreamLakeClient(dl_url="https://term.dreamlake.ai", token=token)
if resolved["status"] == "resolved":
    ticket = gateway.tmux.attach_ticket(
        resolved["target"]["namespace"], reference,
        origin="https://dreamlake.ai", cols=100, rows=30,
    )
```

`attach_ticket` sends a typed `tmux-reference` launch and an exact enrollment
selector. The gateway reauthorizes and resolves the owned server privately.
It attaches only to the exact existing name and cannot create a missing session.
The SDK returns a short-lived ticket for the terminal stream; it does not start
a local interactive terminal or print that credential. A successful creation
receipt already contains its authorized target and reference, so an explicit
attachment can use that receipt without waiting for the next metadata poll.

In notes and chat, `:tmux[new work --host geyang/dev/worker]` is an explicit
creation action with the same action grammar as the CLI and command palette.
Rendering or previewing it does not execute it. Quoting groups literal tokens;
no shell expansion, substitution or command execution is performed by parsing.
