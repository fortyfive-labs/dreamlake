# Host enrollment

The source package includes `DreamLakeClient.hosts` for configuration validation,
SSH enrollment, optional post-enrollment credential saving, and host status. Credential saving is available in Python 0.12.0 on PyPI/GitHub; hosted acceptance remains pending. Use the shared host backend from issue #218;
provider provisioning belongs to #243 and optional credential saving to #241.

The same account token and server configuration used by the DreamLake client
apply. Explicit values below are optional when already configured by login.

::::{tab-set}

:::{tab-item} Python
```python
from dreamlake import DreamLakeClient

client = DreamLakeClient()  # Configured DreamLake account/session.
plan = client.hosts.plan(
    name="bos14-ctrl", prefix="fortyfive/bos14", ssh="bos14-ctrl",
)
result = client.hosts.enroll(
    name="fortyfive/bos14/bos14-ctrl", ssh="bos14-ctrl",
    request_id="my-enrollment-request-1", wait_seconds=60,
)
print(result["status"])  # online only after backend-confirmed readiness, else pending
status = client.hosts.status("fortyfive/bos14/bos14-ctrl")
group = client.hosts.status("fortyfive/bos14/*")
```

:::

:::{tab-item} CLI

```shell
dreamlake hosts enroll -p fortyfive/bos14 -n bos14-ctrl \
  --ssh bos14-ctrl --request-id my-enrollment-request-1 --wait-seconds 60
dreamlake hosts status fortyfive/bos14/bos14-ctrl
dreamlake hosts status 'fortyfive/bos14/*'
```

:::
::::

## Configuration and side effects

`plan(...)` accepts a JSON file path or mapping through `config=`, plus explicit
`name`, `prefix`, and `ssh` overrides. `ssh` can be a quoted argument string or a
mapping with `host`, `user`, `port`, `identityFile`, `jumpHost`, and `options`.
Wildcard status accepts `page=` (1–100000) and `page_size=` (1–100); exact-name lookup
traverses pages. Each name component has 1–64 letters, digits, underscores or hyphens, starting with a letter or digit. The field names match CLI JSON. Explicit fields override config fields; `ssh`
replaces the whole SSH configuration. Prefix conflicts, unknown fields, invalid
ports, remote commands, and unsupported SSH options are rejected before effects.
No shell expansion is performed. Put advanced transport setup in your own SSH config.

```python
preview = client.hosts.enroll(config="bos14-host.json", dry_run=True)
```

Dry-run reads configuration only, contacts no service or host, and reads no SSH
private key. It does not verify namespace authorization. Real enrollment first
checks account access, then invokes local OpenSSH and the bundled portable Python
bootstrap on the target. It creates a durable identity on the target, obtains a
bound grant through the host API, configures the nymph user service, and polls
for that exact enrollment with a verified matching machine identity. Invalid or expired grants are rejected before remote configuration. Grants go through SSH stdin, never command arguments
or the returned result. The library has no Node/CLI runtime dependency.

The remote machine needs Python 3, OpenSSL, a working systemd user manager, and
user lingering enabled. These prerequisites are checked before target identity creation or a grant request. If lingering is disabled, run `loginctl enable-linger` as the target account, or ask the host administrator if local policy denies it. An installed nymph is reused; otherwise the
bootstrap uses the official installer. The target needs installer/control-plane
network access. Installing a service and seeing a heartbeat do not prove a
workload runs. The process runner is the initial supported bootstrap.

## Noninteractive behavior and recovery

Library calls never prompt or exit the process. OpenSSH uses `BatchMode=yes` and
zero password prompts, so keys/agent access must already be usable. Password or
passphrase authentication that needs a prompt fails clearly. No raw-password
argument or configuration field is introduced. `save_credentials=True` saves explicitly supplied credentials only after successful enrollment; Python never prompts. Dry-run never reads credential sources.

Failures raise sanitized `HostError` subclasses from `dreamlake.api.hosts`:
`HostConfigurationError`, `HostAuthorizationError`, `HostConflictError`, and
`HostNotFound`. HTTP failures expose a numeric `status` and, for enrollment, a
`request_id`, without copying response bodies or bootstrap tokens into exceptions.
`operation_id` and `retry_at` preserve valid API recovery fields; `code` carries
the server's fixed error code (for example `grantee_not_member`) when present. If `retry_at` is
present, wait until that time before starting a new request. Request IDs are
1–128 letters, digits, underscores, periods, colons or hyphens, beginning with a
letter or digit. Redirects are refused.
Reuse the same request ID to reconcile an uncertain request; changed replay
parameters are conflicts, not permission to replace an identity. No automatic
write retry occurs. Remote configuration or readiness failure can leave an existing
host record; inspect status before deciding how to resume.

A pending result means the service was started but the backend has not confirmed
it online. It returns host/enrollment IDs and an operation ID for follow-up,
without bootstrap credentials. No rollback is implied by timeout or cancellation.

## Validation scope

Tests cover configuration, authorization, redacted recovery fields, grant expiry,
verified machine identity and a subprocess client against a persistent protocol
fixture. The full suite passed 385 tests with 60 skipped; wheel and docs builds
passed.

A Python subprocess also passed real SSH/systemd acceptance on `bos14-ctrl` using
an isolated local main API, persistent MongoDB, control plane and a source-built
Linux nymph. It verified online status, stable replay, mode-0600 files and signed
reconnect after removing the bootstrap token. A control-plane process request
then returned the expected stdout as the target Unix user. The test-owned service,
files, database fixtures and tunnel were removed. This does not establish
production deployment, installer-release compatibility or Slurm/GPU execution.


## Save selected target and jump credentials

Python 0.12.0 requires the matching host-binding backend; package publication is verified and hosted acceptance remains pending. Existing enrollment stays successful if saving is declined,
missing input, cancelled or unavailable. Inspect both `enrolled` and
`credentials.status`. No remote access is installed/rotated/revoked by saving.

```python
from getpass import getpass
from dreamlake import DreamLakeClient
from dreamlake.host_credentials import HostCredential

host_client = DreamLakeClient()
result = host_client.hosts.enroll(
    "fortyfive/bos14/bos14-ctrl", ssh="-J jump bos14-ctrl",
    request_id="bos14-enrollment-001", save_credentials=True,
    credentials=[
        HostCredential("target", "bos14-ctrl", "ge/bos14/login", "password",
                       password=getpass("Password to save: ")),
        HostCredential("jump", "jump", "ge/jump/key", "private_key",
                       key_file="/secure/jump-key"),
    ],
)
assert result["enrolled"]
print(result["credentials"])  # Redacted outcome, never credential values.
```

Only the caller prompts. Target/jump/key selection is explicit, never inferred
from SSH configuration. `HostCredential` hides source fields in repr; do not
serialize it. Passwords/private keys are not enrollment JSON fields. Private-key
files must be owner-only regular files; encrypted keys retain their original
bytes and require their existing passphrase on use.

`unknown` entry writes include `requestId` for receipt reconciliation. A confirmed
save with unconfirmed binding is `saved_unbound`, retaining exact `binding`
arguments for metadata-only retry with the same account (no SSH or secret reread):

```python
from dreamlake.vault import Vault

with host_client.http() as http:
    vault = Vault(http)
    entry = result["credentials"]["entries"][0]
    if entry["status"] == "unknown":
        receipt = vault.write_status(request_id=entry["requestId"])
    elif entry["status"] == "saved_unbound":
        binding = vault.bind_host_credential(**entry["binding"])
    bindings = vault.host_credentials(
        host_id=result["host"]["id"], enrollment_id=result["enrollment"]["id"],
    )
```

Retry only the same binding arguments; a different entry ID/revision in an
existing slot conflicts. Listing never returns plaintext values. It distinguishes
current, changed, retired, expired, missing and released references; current data
does not prove SSH authentication. Personal vault ownership is independent of
the host namespace. Saving grants no backend SSH delegation.

Reusable isolated acceptance runners:

```shell
uv run python scripts/test_host_credentials_http.py /secure/fixture.json --cli /path/to/cli
uv run python scripts/test_host_credentials_ssh.py /secure/fixture.json
```

The HTTP runner verifies paired source clients against a native-Mongo backend.
The SSH runner requires authorized Linux sshd prerequisites, uses two owned
loopback sshd processes and synthetic keys, verifies fresh vault-restored two-hop
access plus independent target/jump denial, and stops its processes afterward.
The enclosing isolated fixture owns binding/database cleanup; this is not a
production credential test or nymph enrollment claim.

### Paired CLI 0.15.0 candidate

```shell
# Masked password re-entry after successful enrollment; target/jump stay separate.
dreamlake hosts enroll -n fortyfive/bos14/bos14-ctrl --ssh '-J jump bos14-ctrl' \
  --save-credentials --target-password ge/bos14/login \
  --jump-key jump=ge/jump/key=/secure/jump-key
# Automation: explicit consent, protected descriptor, no password argument.
dreamlake hosts enroll -n fortyfive/bos14/bos14-ctrl --ssh bos14-ctrl \
  --save-credentials --target-password ge/bos14/login \
  --password-fd ge/bos14/login=3 --json 3</secure/target-password
# Use the exact identifiers from a saved_unbound outcome.
dreamlake vault bind --host-id HOST_ID --enrollment-id ENROLLMENT_ID \
  --entry-id ENTRY_ID --entry-revision 1 --role target --endpoint bos14-ctrl \
  --kind password
```

Without explicit consent, interactive saving defaults to N. `--no-save-credentials`
declines; `--quiet` suppresses progress only. Python never prompts implicitly.
Interrupting an entry write preserves `unknown` and its request ID; interrupting
a binding preserves `saved_unbound` and exact recovery arguments. A missing write
receipt does not establish failure; do not blindly resubmit with a fresh ID.

Enrolled hosts use a persistent user service with `runtime.keep_alive_s = -1`, so an idle host remains available instead of exiting after five minutes. Re-enrolling the same host with the fixed client updates the generated configuration. Nymph’s default idle policy for other launch modes is unchanged. This fix is unreleased; deployed hosts require the updated client and reconfiguration.

Re-enrollment regenerates the service configuration. Restore any reviewed custom settings, including `private_tracked`, after re-enrollment and verify a fresh signed capability poll before private submission. Re-enrollment does not preserve custom configuration.

## User-qualified host names and rename

Use `namespace/group/host/user` to keep the machine and Unix account separate.
The last component must match the daemon's Unix user. Legacy three-part names remain valid.

```python
client.hosts.rename("fortyfive", host_id,
    name="fortyfive/bos14/bos14-ctrl/geyang",
    expected_name="fortyfive/bos14/bos14-ctrl-geyang")
```

Rename preserves the existing enrollment and keys. On an uncertain response, retry the same inputs.

## Share connection access with a member or a team

Python 0.54.0 or later for people; team grants need Python 0.56.0 and a
server with team Host grants. The exact owner of a Host in an organization
namespace can let one named member, or one team, connect (terminal/SSH relay) to
one enrollment. The grant covers only that enrollment's Unix account: everyone it
reaches acts with that account's existing OS permissions, as the same Unix user,
and no OS user is created. It confers no Host management, rename/move/lifecycle,
metrics, checks, saved sessions or credentials. Personal namespaces cannot share
hosts.

```python
hosts = client.hosts
detail = hosts.status("fortyfive/bos14/bos14-ctrl")
host_id = detail["host"]["id"]
enrollment_id = detail["enrollments"][0]["id"]  # Choose the exact Unix account.

grant = hosts.grant_access("fortyfive", host_id,
                           enrollment_id=enrollment_id, grantee="alice")["grant"]
print(grant["unixUser"], grant["grantee"]["username"])

team_grant = hosts.grant_access("fortyfive", host_id,
                                enrollment_id=enrollment_id, team="robotics")["teamGrant"]
print(team_grant["unixUser"], team_grant["team"]["slug"])

listing = hosts.access_grants("fortyfive", host_id)
for g in listing["grants"]:
    print(g["id"], g["state"], g["unixUser"], g["grantee"]["username"],
          g.get("pendingConnections"), g.get("pendingUntil"))
for g in listing["teamGrants"]:
    print(g["id"], g["state"], g["unixUser"], g["team"]["slug"])
print([team["slug"] for team in listing["teams"]])  # teams that can be granted

result = hosts.revoke_access("fortyfive", host_id, team_grant["id"])
print(result.get("stillAuthorizedConnections"))  # kept open by another grant
if result["unconfirmedConnections"]:
    # Not confirmed closed: revoke again to retry, or they end at this time.
    print(result["unconfirmedConnections"], result["unconfirmedUntil"])
```

All three calls are owner-only and require an explicit enrollment ID.
`grant_access` takes exactly one of `grantee`, a DreamLake username (a leading
`@` is accepted) who must currently be a member of the namespace, or `team`, the
slug of a live team in the namespace's organization. Supplying both or neither
raises `HostConfigurationError` before any request. Refusals raise `HostError`
subclasses whose `code` names the reason, such as `host_access_grant_org_only`,
`grantee_not_found`, `grantee_is_owner`, `grantee_not_member`, `team_not_found`
or `enrollment_not_found`.

A team grant reaches the team's **current and future direct members** while they
remain members; parent and child teams are not followed. Team maintainers
therefore decide who can connect, and adding someone to the team adds them to the
organization. Access is the union of a person's own grant and their teams'
grants for that enrollment: revoking one grant, or leaving one team, never
closes a session that another current grant still authorizes; such sessions are
counted in `stillAuthorizedConnections`. When a member is removed, leaves the
organization, or the team is deleted, only their team-path sessions are closed:
browser terminals end at their next lease (about 3 s); `dl ssh` sessions close
when the control plane is reachable and otherwise end at their deadline
(at most 30 min after opening, or 30 min 10 s after the last renewal). Renewal is
refused once access is gone.

Granting the same enrollment to the same person or team returns the existing
active grant. After an uncertain grant response, reconcile with
`access_grants(...)` rather than repeating the call: a repeated grant also
re-activates one that was revoked in the meantime, so grant again only as a fresh,
explicit decision. Every connection rechecks the grant, the owner's and the
connecting user's current access, so a listed `active` grant may still be inert
(for example after the member leaves the organization). Revocation denies new
connections immediately and asks the control plane to close open ones;
`closedConnections` counts confirmed closes only. A revoked grant stays listed
with `state: "revoked"`, `pendingConnections` and `pendingUntil` while
connections remain unconfirmed. Responses that do not match the requested Host,
enrollment, grantee, team or grant raise `HostError` instead of being reported as
success; no call retries a write.

`access_grants` requests the full listing (`?grantees=all`). A server without
team grants cannot report who else can connect, so it raises `HostError` with
code `host_team_grants_unsupported` instead of returning an incomplete list; a
team grant sent to such a server is refused with HTTP 400 and grants nothing.
Earlier SDKs, which list people only, receive HTTP 409
`host_access_grant_client_upgrade_required` from a team-capable server once the
Host has team grants; upgrade the SDK to list them.

A grantee's `status(...)` shows only the granted enrollments and a
`host["access"]` marker with `kind: "grant"` and the grant IDs. With team
grants, each entry also carries `via` (`"user"` or `"team"`) and, for teams,
`team`.

## Hosts in Projects

Filing places a Host in a Project for organization members to find; it is
**metadata only** and never grants connection access. See
[Project grants and attachments](api/projects.md#hosts-in-projects).
