# Host enrollment

The source package includes `DreamLakeClient.hosts` for configuration validation,
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
argument or configuration field is introduced. `save_credentials=True` is accepted
only as intent during dry-run; real saving fails before networking or host changes.

Failures raise sanitized `HostError` subclasses from `dreamlake.api.hosts`:
`HostConfigurationError`, `HostAuthorizationError`, `HostConflictError`, and
`HostNotFound`. HTTP failures expose a numeric `status` and, for enrollment, a
`request_id`, without copying response bodies or bootstrap tokens into exceptions.
`operation_id` and `retry_at` preserve valid API recovery fields. If `retry_at` is
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
