# Import selected SSH credentials

`RemoteClient.vault.import_entries` performs a one-way import into the currently
authenticated vault. It never prompts. Use an explicit destination prefix and
selection; profiles, private keys and jump hosts are separate selections.
This API is available in source on this branch, not evidence of a package release.

```python
from pathlib import Path
from dreamlake import RemoteClient

client = RemoteClient("https://your-dreamlake.example", api_key=your_access_key)
config = Path.home() / ".ssh/config"
preview = client.vault.import_entries(
    source="ssh", prefix="alice/remote", config=config, dry_run=True,
)
# Inspect preview["discovered"] and preview["warnings"]. No private keys were read.
report = client.vault.import_entries(
    source="ssh", prefix="alice/remote", config=config,
    select=["profile:bos14", "key:bos14:1"], retry=1,
)
for entry in report["entries"]:
    print(entry["id"], entry["name"], entry["status"])
```

The corresponding CLI operation is:

```shell
dreamlake vault import --ssh -p alice/remote \
  --select profile:bos14 --select key:bos14:1 --json
```

A profile becomes `alice/remote/ssh/profiles/bos14`; the selected first key becomes
`alice/remote/ssh/keys/bos14-1`. Profiles contain explicit HostName, User, Port,
ProxyJump and IdentityFile references. The importer does not execute SSH, follow
Include, evaluate Match, resolve wildcards/default inheritance, or run commands
from configuration. It copies neither jump-host credentials nor key contents
unless independently selected. Symlinks, hardlinks, devices, permissive key modes,
oversized files and unsupported key envelopes are rejected. Hardware-backed keys
remain references; an imported profile is not a promise of usable connectivity.

`select=[]` means no upload; omitting selection is allowed only for preview.
The explicit Python invocation is consent for the specified items. Private keys
are read once after destination/selection validation, prepared before writes,
and transmitted only in HTTPS request bodies (HTTP is permitted for loopback
fixtures). Values never appear in returned reports or exception messages. Python
strings cannot guarantee secure memory erasure; treat the importing process as
trusted. Do not log HTTP request bodies using a custom transport or debugger.

## Conflicts and uncertain responses

Imports create only by default. Existing equal values are reconciled without
another write. Replacement requires a revision for that selected ID:

```python
report = client.vault.import_entries(
    source="ssh", prefix="alice/remote", config=config,
    select=["profile:bos14"], if_match={"profile:bos14": 7}, retry=1,
)
```

The revision stays pinned; retries never fetch a new revision and overwrite it.
Reports distinguish `success`, `failure`, and `unknown`. A timeout or malformed
write acknowledgement can follow a committed write. Within the same call,
`retry=0..3` reconciles unknown results by reading the destination but never
replays those writes. A matching value proves the current state, not ownership
of the earlier write. No persistent operation receipt exists: do not blindly
start a new import after an unknown result. Review the destination and decide
whether another explicitly authorized operation is appropriate. Successful
entries are retained; there is no rollback or source deletion.

## Preview pass OTPs

```python
preview = client.vault.import_entries(
    source="pass-otp", prefix="alice", store="/canonical/pass-store",
    gpg_home="/canonical/gnupg", dry_run=True,
)
assert preview["uploaded"] is False
```

This remains a redacted, read-only preview. It decrypts records from the explicit
store locally without interactive pinentry, finds OTP records, and reports
mapping/error status without exposing seeds or adjacent ordinary passwords.
Selected TOTP upload and owner-only generation are available from Python 0.10.0 with the matching OTP backend. HOTP support is included in the 0.13.0 release candidate; its publication and hosted acceptance are separate. Ordinary `source="pass"`
is unsupported; `config`, `if_match` and `retry` remain SSH-only.

The existing `client.vault.pass_store.sync(store=..., otp=True, dry_run=True)`
remains a compatibility alias for preview. New examples use `import_entries`;
future `sync` is reserved for tracked reconciliation rather than one-way import.


## Selected TOTP import and use

```python
result = client.vault.import_entries(
    source="pass-otp", prefix="alice/otp", store="/canonical/test-store",
    gpg_home="/canonical/test-keyring", select=["login"],
)
code = client.vault.otp("login", prefix="alice/otp")
code_json = client.vault.otp("login", prefix="alice/otp", to_json=True)
```

```shell
dreamlake vault import --pass-otp -p alice/otp --store /canonical/test-store --gpg-home /canonical/test-keyring --select login
dreamlake vault otp -p alice/otp -n login
dreamlake vault otp -p alice/otp -n login --to-json
```

Only selected files are decrypted during apply; paths are relative to the store
without `.gpg`, with canonical slash-separated names. Selection is explicit
consent; Python never prompts. The complete batch is validated before upload,
and encrypted source bytes are rechecked before writes. Adjacent passwords never
upload. HOTP registrations default to inactive; `hotp_owner="dreamlake"` explicitly opts into counter authority. See [Vault operations](vault-operations.md) for activation, counter normalization and durable recovery. There is no persisted preview/apply plan.

Imports create only. `conflict`, `denied`, `source-changed`, and `unknown` stop
the batch, retaining earlier successes. `unknown` means the write may have
committed; there is no automatic retry or rollback. Inspect metadata using
`show`, and use explicit `get` only for secure comparison before deciding whether
to retry. Identified-write recovery exists separately; source import batch outcomes still require explicit reconciliation.

For TOTP, `otp` returns a secret code string, or JSON containing only `code` and
`validUntil` with `to_json=True`; do not log either. Expired responses are
rejected locally. The server clock drives generation and payload expiry caps
validity. Scoped keys are denied. Explicit `get` reveals the registration JSON
string and seed; it does not generate a code. Target authentication services
still enforce one-time acceptance. Staging and production need separate gates.

The cross-client real GPG/HTTP/Mongo acceptance runner lives in the matching
server checkout at `dreamlake-server/scripts/test-vault-otp-clients.py`; its
`src/vault/OTP.md` documents prerequisites and reproduction commands. This slice
tracks [workspace issue #241](https://github.com/dreamlake-ai/dreamlake-workspace/issues/241).


### Python TOTP destination binding

Selected TOTP uploads freeze each request's URL, headers (including bearer
Authorization and applicable cookies), body, timeout and transport route before
final source revalidation. Dispatch uses an isolated httpx client with redirects
and environment authentication disabled. Source-client cookie updates (including
Set-Cookie), auth changes, hooks, base URL changes and replacement of the Vault
client cannot redirect a prepared upload or change its account. Earlier detected
connection changes still abort the import. Custom client auth objects and any
request/response hooks are rejected before reading/decrypting selections: use a
fixed Authorization header. Arbitrary auth callables cannot be pinned safely.
Transport/TLS/proxy configuration is retained; caller-provided transports remain
trusted and must not rewrite destinations or credentials. The caller owns their
lifecycle and must keep them open during import. This uses httpx's private route
selection method, covered by dispatch regressions; recheck on httpx upgrades.
CLI already pins remote/token; no CLI dispatch API change is required for F1.
