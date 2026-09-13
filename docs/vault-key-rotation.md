# Rotate one host SSH key

**Under review, unreleased.** Requires the matching host-credential cleanup backend. This is client-owned SSH transport; DreamLake does not connect on your behalf. [Vault #241](https://github.com/dreamlake-ai/dreamlake-workspace/issues/241) tracks release and live acceptance separately. Password rotation remains required work; this guide covers private keys only.

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

Repeat exactly the same invocation to resume after interruption. CLI and Python share the metadata journal format, so either may resume it using the same account, API origin, binding, destination and replacement entry. The client saves the replacement, adds its public key while preserving the old key and unrelated authorization bytes/options/modes, verifies a fresh replacement-only connection, conditionally switches the binding, removes the exact old authorization, verifies old-key denial and fresh replacement success, then confirms cleanup. A failed save/probe does not remove old authorization. A lost response retains the operation and stable request IDs.

The generated private keys live in separate owned0600 files in `.rotation-<operationId>` beside the metadata-only operation file. They are removed after confirmed completion. Preserve these files and `<operation-file>.records` while an outcome is uncertain. A crash before publishing the manifest can leave a private candidate directory: inspect ownership and whether any manifest references it before explicitly deleting it. There is no automatic TTL for pending remote access or pending retention references.

A jump-host key is a separate rotation: select its binding and use the bastion itself as the explicit target, without a jump profile unless that bastion requires another hop. Saved target and jump keys are never conflated. Ambient SSH config execution, ProxyCommand, LocalCommand, Match/Include, agent fallback, encrypted private-key prompting and unsupported/ambiguous authorized_keys constraints are rejected. Python library calls never prompt. The old shared vault entry is not automatically retired; retire it separately only after confirming every consumer has moved.

## Metadata recovery

```python
binding = client.vault.host_credential(target_binding_id)
operation = client.vault.host_credential_operation(operation_id)
```

The backend confirmation is an explicit client attestation with public fingerprints, pinned remote identity and verification timestamps. It is not server-proven SSH authentication. It releases completed historical retention references, while the active binding continues retaining the replacement. Entry retirement and its existing deletion deadline still govern ciphertext collection.
