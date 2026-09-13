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
OTP upload and code generation are not implemented: omitting `dry_run=True`
fails before upload. Ordinary `source="pass"` is unsupported. SSH options cannot
be mixed with pass-OTP options.

The existing `client.vault.pass_store.sync(store=..., otp=True, dry_run=True)`
remains a compatibility alias for preview. New examples use `import_entries`;
future `sync` is reserved for tracked reconciliation rather than one-way import.
