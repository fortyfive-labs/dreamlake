# Vault operations

**Python 0.13.0 and CLI 0.16.0 are published.** These APIs
require the matching Vault backend. See the [runtime note](https://docs.dreamlake.ai/dev/notes/vault-runtime/)
for hosted availability; package checks do not establish deployment.

## Host key rotation (unreleased)

See [Rotate one host SSH key](vault-key-rotation.md) for paired CLI/Python commands,
explicit target/jump transport, interruption recovery and cleanup. The matching
backend and client release are required; current published versions above do not
include this new rotation orchestration.

## HOTP ownership and recovery

```python
client.vault.import_entries(
    source="pass-otp", prefix="alice/otp", store="/absolute/password-store",
    select=["login"],
)
client.vault.activate_otp("alice/otp/login", counter_owner="dreamlake", if_match=1)

# Or explicitly take ownership during a new import.
client.vault.import_entries(
    source="pass-otp", prefix="alice/otp", store="/absolute/password-store",
    select=["another-login"], hotp_owner="dreamlake",
)
```

Stop other generators before taking counter ownership. Inactive is the import
default. For pass-otp the stored counter means last generated; activation advances
the stored value to next unused without modifying pass. Initial activation
supports SHA1 and at least a 128-bit seed. Other algorithms remain inactive.
No counter is advanced in the background, and generic replacement cannot roll
an active counter back.

```python
code = client.vault.otp(
    "alice/otp/login", request_file="/private/requests/login-request-001.json"
)
```

After any uncertain response, reuse that same file, including after restarting
the process. A new issuance needs a different file. The file stores only immutable
account/origin/entry/revision/request metadata, never a seed, code, counter or token.
It is exclusively created mode 0600; file and directory fsync must succeed before
issuance, including reuse. Use a private POSIX directory on a filesystem/hardware
that honors fsync; unsupported durability fails closed. External deletion or
filesystems ignoring fsync cannot provide the same guarantee. Python never prompts.

Encrypted issuance receipts remain recoverable for 24 hours. That is a recovery
deadline, not HOTP code expiry. Expired or missing receipts leave an ambiguous
outcome, but the original tuple cannot advance again. Do not substitute a fresh
intent to retry. Scoped retrieval keys cannot generate OTP codes.

```shell
dreamlake vault import --pass-otp -p alice/otp \
  --store /absolute/password-store --select login
dreamlake vault otp -n alice/otp/login --activate \
  --counter-owner dreamlake --if-match 1
dreamlake vault otp -n alice/otp/login \
  --request-file /private/requests/login-request-001.json
```

## Metadata pages

```python
entries = client.vault.list(prefix="alice/remote")
retired_too = client.vault.list(prefix="alice/remote", include_deleted=True)
page = client.vault.list_page(prefix="alice/remote", limit=50)
while page["nextCursor"] is not None:
    page = client.vault.list_page(
        prefix="alice/remote", limit=50, cursor=page["nextCursor"]
    )
```

`list()` follows bounded 100-entry pages and returns the complete metadata list.
`list_page()` allows 1–200 entries and returns `{entries, nextCursor}`. Keep the
account, prefix and retired filter unchanged. Listing does not decrypt payloads;
expired metadata stays visible and retired inclusion is owner-only. Traversal is
not a snapshot: new names before the current boundary need a fresh listing,
while retired/deleted entries may disappear. Changes to client authority during
`list()` abort before the next request or return, without a partial result.
Legacy HTTP requests without page parameters remain unbounded during migration.

```shell
dreamlake vault -p alice/remote list
dreamlake vault -p alice/remote list --limit 50
dreamlake vault -p alice/remote list --limit 50 --cursor "$cursor"
```

## Release a host credential reference

```python
client.vault.unbind_host_credential(
    binding_id=binding_id, entry_id=entry_id, entry_revision=1,
)
```

```shell
dreamlake vault unbind --binding-id "$binding_id" \
  --entry-id "$entry_id" --entry-revision 1
```

Preserve exact metadata from the saving/binding receipt. Owner cleanup remains
available after host deletion. Repeated release preserves the original timestamp.
This releases a retention reference; it does not revoke remote SSH access, remove
an active secret, or bypass the retired entry's retention deadline.
