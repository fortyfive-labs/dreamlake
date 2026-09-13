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
