# Verify a saved SSH password

**Implemented for review; unreleased.** Saving a password during enrollment does not establish that it works. This command reads one explicitly selected password binding and verifies it through a fresh password-only SSH connection. It does not change passwords, retire entries or authorize backend SSH. [Design and remaining rotation work](https://github.com/dreamlake-ai/dreamlake-workspace/blob/main/docs/pages/dev/plans/host-password-rotation/%2BPage.mdx).

```shell
dreamlake vault verify-password --binding-id "$password_binding_id" \
  --ssh 'worker@worker.example' \
  --known-hosts "$HOME/.ssh/known_hosts"
```

```python
result = client.vault.verify_host_password(
    binding_id=password_binding_id,
    ssh={
        "host": "worker.example", "user": "worker", "port": 22,
        "knownHostsFile": str(known_hosts),
    },
)
assert result["status"] == "verified"
```

Results contain `status`, `method="ssh_password"`, `observedAt`, and the exact binding/entry IDs and revision. Successful authentication also returns the remote account's UID, username, home and machine ID. Explicit password denial returns `status="denied"` (CLI exit 1). Network failures, unexpected prompts, changed entries/bindings, alternate authentication or unconfirmed evidence fail with a redacted error. Neither result contains the password or a password hash.

The binding must contain an explicit `user@host` matching the supplied target before the client reads or delivers the secret. A stored port must also match; when enrollment omitted the port, the caller supplies it explicitly. Alias-only bindings are rejected by this adapter. Results report `target` and `verificationScope="credential_at_explicit_endpoint"`: this verifies password acceptance at that destination, not a cryptographic assertion that it is the server's `HostId` or enrollment. Trusted SSH host keys remain required.

A denial means authentication was refused; it cannot diagnose an incorrect secret versus account or PAM policy. The [SSH failure message](https://www.rfc-editor.org/rfc/rfc4252.html#section-5.1) does not carry that distinction. Future rotation must verify replacement success again after old-password denial before confirming cleanup.

Use the same target/jump separation as key rotation. A target behind a jump adds `--jump-ssh`, `--jump-identity` and optional `--jump-known-hosts`; Python adds a `jump` mapping with `host`, `user`, `port`, `knownHostsFile`, `identityFile`. The jump uses its separately selected private key. Verify the jump's own password by selecting that password binding and addressing the jump directly. Simultaneous password-only hops and keyboard-interactive/MFA are not supported by this slice.

The client uses trusted host keys and isolated config. A private one-shot askpass channel carries the selected password to OpenSSH; there is no password command argument, environment value or plaintext journal. Temporary authentication logs stay local and are removed. The first authentication adapter accepts 1–512 UTF-8 bytes without NUL, CR or LF; it rejects unsupported input without trimming it. Generic vault string storage still preserves its original bytes. Library calls never prompt.

A verification makes one authentication attempt; do not repeatedly retry denial against an account with a lockout policy. It describes the observed login, not future availability, existing-session revocation or the safety of changing a password. The later rotation flow requires separately verified recovery access and a pre-mutation reservation; it is not implemented here.

## Reusable acceptance

`test/test_host_password.py` exercises real private askpass subprocesses, pinned HTTP reads and fail-closed authentication evidence with a simulated SSH peer. `scripts/test_password_probe_native.py` repeats transport cases through a compiled candidate and the actual npm launcher template; it does not claim a registry release.

```shell
python scripts/test_password_probe_native.py \
  --native /absolute/candidate/dreamlake \
  --cli-dir /absolute/dreamlake-cli
```

`scripts/test_host_password_remote.py` requires an explicitly authorized Linux admin SSH endpoint with sudo, local-account tooling, OpenSSH, remote Python with pidfd support, and a dedicated test window. It creates marked synthetic users and a private loopback SSH daemon, tests native/npm stdin saving and native/npm/Python positive/denial checks, then verifies owned cleanup. The source fixture uses an isolated local HTTP/native Mongo backend; it does not prove hosted KMS or live Nymph enrollment.

```shell
mkdir -m 700 "$HOME/password-acceptance"
python scripts/test_host_password_remote.py \
  --work-dir "$HOME/password-acceptance" \
  --server-dir /absolute/dreamlake-workspace/dreamlake-server \
  --cli-dir /absolute/dreamlake-cli \
  --native /absolute/candidate/dreamlake \
  --admin-config /absolute/authorized-admin-config \
  --admin-host dedicated-test-host
```

Use a fresh private directory. If cleanup is unconfirmed, retain its protected state and backend for recovery; do not blindly delete it or create another fixture with the same identity.

## Recorded candidate acceptance

[Sanitized receipt](vault-password-acceptance.json) records native/npm stdin saving and all three clients performing real direct-jump and target-through-jump password success/denial checks. Independent cleanup verified both users/homes absent, original daemon gone, listener closed and private fixture directory removed. Password rotation remains unfinished.

## Reserve and recover a password rotation

**Candidate API, not released.** Requires [backend #370](https://github.com/dreamlake-ai/dreamlake-workspace/pull/370). These low-level calls reserve metadata and recover exact encrypted revisions. They never change a remote password. The privileged rotation adapter and explicit rollback-resolution API remain required before the complete flow ships.

The replacement must be a dedicated, non-expiring string entry with no active binding, pending operation or access-key scope. Reservation freezes that replacement while allowing the shared old entry to change. The intent records exact old/new entry IDs and revisions, observed account identity (`user`, `uid`, `home`, `machineId`, `homeDevice`, `homeInode`) and a separately verified recovery key fingerprint/time. No password or private key belongs in intent/proof JSON. Never fabricate authentication evidence to finish an operation.

```shell
# intent.json is private metadata produced by the reviewed preparation flow.
dreamlake vault password-rotation reserve --binding-id "$binding_id" \
  --operation-id "$operation_id" --input "$intent_file"
dreamlake vault password-rotation show --operation-id "$operation_id"
# Cancel only before a remote mutation was durably started.
dreamlake vault password-rotation cancel --operation-id "$operation_id"
```

```python
operation = client.vault.reserve_host_password_rotation(
    binding_id=binding_id, operation_id=operation_id, intent=intent,
)
operation = client.vault.host_password_rotation(operation_id)
operation = client.vault.cancel_host_password_rotation(operation_id)
```

The paired `start`/`start_host_password_rotation` durably marks possible mutation before remote work; `confirm`/`confirm_host_password_rotation(..., proof=...)` accepts exact client attestation afterward. Confirmation requires new-password success, old-password refusal, new-password success again, and recovery-key success for the same account. It switches only to the exact live replacement revision. The server records client attestation, not server-proven SSH.

For explicit recovery, `vault password-rotation read --operation-id ID --slot old` emits only the selected string (no added newline); `--to-json` explicitly includes its IDs/revision and value. Python `read_host_password_rotation(operation_id, slot="old")` returns the selected string without prompting or printing. `slot="new"` selects the replacement snapshot. This owner-only pending-operation read can survive original-entry expiry or deletion; ordinary entry reads retain normal expiry rules. Keep returned strings out of logs and command arguments.

An uncertain request is not proof of failure: retain the same operation ID/intent, inspect metadata or replay exactly. Never restart under a new ID or automatically rerun SSH. A deleted/recreated host cannot complete the original operation. Pending recovery has no TTL; verified terminal snapshots are retained for 30 days, then collected unless explicitly referenced by another recovery operation. Later normal rotation/unbinding does not retain obsolete snapshots forever.

The reusable [CLI/Python HTTP runner](../scripts/test_password_reservation_clients.py) starts an isolated native Mongo fixture and crosses clients for reserve/replay/read/start/confirm, ownership denial and write freezing. Its attestations are synthetic: it does not test SSH password mutation or cloud KMS.

KMS migration retains password recovery snapshots too. These candidate clients display `passwordSnapshotCount`, `totalRetainedRecordCount`, and the supported record schema/kinds, and send schema-2 acknowledgment on migration start/resume. Deploy the compatible backend first. Older clients can inspect status but cannot advance an affected migration; reuse the original operation ID after upgrading.
