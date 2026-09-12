# Host enrollment

The development branch adds `DreamLakeClient.hosts` for configuration validation,
no-save SSH enrollment, and host status. This is not yet a released package or
proof of a live host deployment. Use the shared host backend from issue #218;
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
Wildcard status accepts `page=` and `page_size=` (up to 100); exact-name lookup
traverses pages. The field names match CLI JSON. Explicit fields override config fields; `ssh`
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
for that exact enrollment. Grants go through SSH stdin, never command arguments
or the returned result. The library has no Node/CLI runtime dependency.

The remote machine needs Python 3, OpenSSL, a working systemd user manager, and
linger enabled by its administrator. An installed nymph is reused; otherwise the
bootstrap uses the official installer. The target needs installer/control-plane
network access. Installing a service and seeing a heartbeat do not prove a
workload runs. The process runner is the initial supported bootstrap.

## Noninteractive behavior and recovery

Library calls never prompt or exit the process. OpenSSH uses `BatchMode=yes` and
zero password prompts, so keys/agent access must already be usable. Password or
passphrase authentication that needs a prompt fails clearly. No raw-password
argument or configuration field is introduced. `save_credentials=True` is accepted
only as intent during dry-run; real saving fails before networking or host changes.

Failures raise sanitized `HostError` subclasses from `dreamlake.api.hosts`:
`HostConfigurationError`, `HostAuthorizationError`, `HostConflictError`, and
`HostNotFound`. HTTP failures expose a numeric `status` and, for enrollment, a
`request_id`, without copying response bodies or bootstrap tokens into exceptions.
Reuse the same request ID to reconcile an uncertain request; changed replay
parameters are conflicts, not permission to replace an identity. No automatic
write retry occurs. Remote configuration or readiness failure can leave an existing
host record; inspect status before deciding how to resume.

A pending result means the service was started but the backend has not confirmed
it online. It returns host/enrollment IDs and an operation ID for follow-up,
without bootstrap credentials. No rollback is implied by timeout or cancellation.

## Validation scope

Tests exercise configuration and error handling plus a real subprocess Python
consumer talking HTTP to a persistent SQLite-backed protocol fixture, with an
SSH subprocess adapter checking that grants use stdin. That fixture is not the
production host server or a real remote nymph. Real backend/control-plane/remote
host acceptance is tracked separately and must be reported before claiming
end-to-end deployment support.
