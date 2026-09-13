# Rotate one host SSH key

**Python 0.15.0 release candidate; source merged, publication pending.** Requires the matching host-credential cleanup backend. This is client-owned SSH transport; DreamLake does not connect on your behalf. [Vault #241](https://github.com/dreamlake-ai/dreamlake-workspace/issues/241) tracks release and live acceptance separately. Password rotation remains required work; this guide covers private keys only.

Start with an enrolled SSH-accessible machine and an existing private-key binding. Select the target or jump binding explicitly. The current saved key must authenticate and occur exactly once in that account's `~/.ssh/authorized_keys`. Remote Python 3 and local OpenSSH are required. Use trusted existing known_hosts, an explicit host/user/port, and an owned private local operation directory.

```shell
mkdir -m 700 "$HOME/vault-rotation"
dreamlake vault rotate-key --binding-id "$target_binding_id" \
  --operation-file "$HOME/vault-rotation/target.json" \
  --new-entry alice/remote/target-20260913 \
  --ssh 'worker@worker.example' --known-hosts "$HOME/.ssh/known_hosts" \
  --jump-ssh 'jump@bastion.example' \
  --jump-identity "$HOME/.ssh/bastion" \
  --jump-known-hosts "$HOME/.ssh/known_hosts"
```

```python
from pathlib import Path

operation = client.vault.rotate_host_key(
    binding_id=target_binding_id,
    operation_file=Path.home() / "vault-rotation/target.json",
    new_entry="alice/remote/target-20260913",
    ssh={
        "host": "worker.example", "user": "worker", "port": 22,
        "knownHostsFile": str(Path.home() / ".ssh/known_hosts"),
        "jump": {
            "host": "bastion.example", "user": "jump", "port": 22,
            "knownHostsFile": str(Path.home() / ".ssh/known_hosts"),
            "identityFile": str(Path.home() / ".ssh/bastion"),
        },
    },
)
assert operation["phase"] == "cleanup_confirmed"
```

Repeat exactly the same invocation to resume after interruption. CLI and Python share the metadata journal format, so either may resume it using the same account, API origin, binding, destination and replacement entry. The client saves the replacement, adds its public key while preserving the old key and unrelated authorization bytes/options/modes, verifies a fresh replacement-only connection, conditionally switches the binding, removes the exact old authorization, verifies old-key denial and fresh replacement success, then confirms cleanup. Denial requires the parent SSH client's private authentication log; remote command stderr or a jump host's authentication cannot serve as proof. These bounded temporary logs are deleted after each probe. A failed save/probe does not remove old authorization. A lost response retains the operation and stable request IDs.

The generated private keys live in separate owned 0600 files in `.rotation-<operationId>` beside the metadata-only operation file. They are removed after confirmed completion. Preserve these files and `<operation-file>.records` while an outcome is uncertain. A crash before publishing the manifest can leave a private candidate directory: inspect ownership and whether any manifest references it before explicitly deleting it. There is no automatic TTL for pending remote access or pending retention references.

A jump-host key is a separate rotation: select its binding and use the bastion itself as the explicit target, without a jump profile unless that bastion requires another hop. Saved target and jump keys are never conflated. Ambient SSH config execution, ProxyCommand, LocalCommand, Match/Include, agent fallback, encrypted private-key prompting and unsupported/ambiguous authorized_keys constraints are rejected. Python library calls never prompt. The old shared vault entry is not automatically retired; retire it separately only after confirming every consumer has moved.

## Metadata recovery

```python
binding = client.vault.host_credential(target_binding_id)
operation = client.vault.host_credential_operation(operation_id)
```

The backend confirmation is an explicit client attestation with public fingerprints, pinned remote identity and verification timestamps. It is not server-proven SSH authentication. It releases completed historical retention references, while the active binding continues retaining the replacement. Entry retirement and its existing deletion deadline still govern ciphertext collection.

## Repeat the acceptance tests

The HTTP test starts its own native Mongo/HTTP fixture and uses an explicitly
simulated SSH adapter. It validates both candidate clients and shared recovery
contracts, and does not prove real SSH authentication.

```shell
python scripts/test_host_key_rotation_http.py \
  --server-dir /absolute/dreamlake-workspace/dreamlake-server \
  --cli-dir /absolute/dreamlake-cli
```

The real remote runner creates two randomly marked disposable Linux users on
an explicitly authorized existing SSH host. It requires passwordless sudo for
user creation/removal, trusted existing SSH host keys, local OpenSSH and the
candidate CLI/Python source checkouts. It tests target rotation through a jump
host and direct jump rotation with both clients, exact unrelated authorization
bytes/modes, restored fresh access and old-key denial. It removes only its marked
users, then releases fixture bindings/retires entries and shuts down its owned
database. If remote removal is unconfirmed, it preserves the backend, private
fixture authentication, keys and state for recovery instead of releasing references.

```shell
mkdir -m 700 "$HOME/vault-rotation-acceptance"
python scripts/test_host_key_rotation_remote.py \
  --server-dir /absolute/dreamlake-workspace/dreamlake-server \
  --cli-dir /absolute/dreamlake-cli \
  --admin-config /absolute/authorized-admin-ssh-config \
  --admin-host dedicated-test-host \
  --work-dir "$HOME/vault-rotation-acceptance"
```

Use a fresh owned private directory for each run. These source-client/native-Mongo
fixtures do not establish published-package, hosted-KMS, enrollment or password
rotation acceptance. Inspect the protected `state.json` after failure; do not
reuse its path for a new operation or delete its recovery files prematurely.

## Recorded candidate acceptance

[Sanitized evidence](vault-key-rotation-acceptance.json) records the exact source hashes and ten passing groups from real target/jump SSH: both CLI and Python completed each role, freshly restored replacement keys worked, old keys failed authentication, unrelated authorization bytes/options/modes remained intact, and authenticated forced-command denial forgery was rejected. Both marked users and homes were independently confirmed absent after cleanup. The source fixture does not prove published clients, hosted KMS, live enrollment or password rotation.
