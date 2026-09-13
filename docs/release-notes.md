# Release notes

## 0.15.0 — release candidate, 2026-09-13

**Not yet published.** Prepared from reviewed main `bbe4e03` (Python #44), excluding unmerged password-probe work.

Adds `Vault.rotate_host_key`, account-owned binding lookup and exact cleanup confirmation. A shared CLI/Python immutable journal resumes interrupted saves, selected-key verification, conditional binding replacement and exact old authorization cleanup. Target and jump credentials are separate, no calls prompt, and shared old entries are not automatically retired. [Commands and recovery guide](vault-key-rotation.md). [Real candidate SSH acceptance](vault-key-rotation-acceptance.json) passed both clients for target and jump rotation with independent cleanup. Published clients, hosted KMS and enrollment acceptance remain separate; password rotation remains required follow-up.

### Release preparation evidence

Python 3.12.12: full suite 584 passed, 60 skipped; installed-wheel rotation suite 55 passed outside the source checkout; wheel and sdist fresh installations report 0.15.0 and expose `Vault.rotate_host_key`. Sphinx HTML build passed with 38 warnings. The first full run encountered an unrelated concurrent-file duplicate-ID assertion (583 passed, 60 skipped); its isolated four-test file and the subsequent full run passed. No unrelated runtime changes were made. Live source acceptance remains the linked four CLI/Python target/jump flows with cleanup; this release preparation does not establish published-package or hosted rotation acceptance.

## 0.14.0 — 2026-09-13

**Published to PyPI.** Wheel and sdist hashes match the reviewed artifacts; a fresh registry installation reports 0.14.0. Paired CLI 0.17.0 publication is pending. This release contains only merged changes.

- Personal owners can inspect trusted prefix KMS policies, preview retained records,
  activate empty prefixes, and explicitly start/resume populated-prefix migrations.
  Immutable request IDs recover uncertain outcomes; explicit prefixes are checked
  before resuming. The SDK never prompts or advances migration in the background.
- Conditional host-binding replacement and owner-only operation recovery retain
  both credential identities until explicit cleanup. This metadata API does not
  install or revoke remote SSH keys; remote rotation candidates are excluded.

Migration requires backend [#359](https://github.com/dreamlake-ai/dreamlake-workspace/pull/359).
The operator migration flag defaults to false and must remain disabled until all
writers enforce policy epochs. Installing this package does not enable migration,
configure customer grants, or prove hosted acceptance. [Paired KMS guide](vault-kms.md).

## 0.13.0 — 2026-09-13

**Published to PyPI.** Wheel and sdist downloads match the reviewed SHA256 hashes; a fresh registry installation reports 0.13.0. Paired CLI release: 0.16.0. The matching Vault backend
is required; local package validation is separate from hosted deployment.

- HOTP registrations import inactive by default. Explicit `hotp_owner="dreamlake"`
  or `activate_otp` takes counter ownership; `otp(request_file=...)` preserves one
  private immutable intent for issuance/recovery. It never prompts or modifies pass.
- `list()` preserves complete metadata results through bounded pages; `list_page()`
  exposes continuation and owner-only retired inclusion. Account/config changes
  during traversal abort without returning a partial result.
- `unbind_host_credential()` releases exact personal retention references, including
  after host deletion, without claiming remote revocation or deleting active secrets.

[Examples and recovery limits](vault-operations.md). Source:
[HOTP #38](https://github.com/fortyfive-labs/dreamlake/pull/38),
[pagination #39](https://github.com/fortyfive-labs/dreamlake/pull/39), and
[unbind #36](https://github.com/fortyfive-labs/dreamlake/pull/36).

## 0.12.0 — 2026-09-13

Post-enrollment saving accepts explicit target/jump `HostCredential` selections,
keeps passwords in memory and selected keys in protected files, and never prompts
inside the library. Saving failures preserve the successful enrollment result.
Interrupted writes retain unknown outcomes and durable request IDs; interrupted
bindings preserve metadata-only recovery arguments.

[Paired CLI/Python usage](hosts.md) targets CLI 0.15.0 / Python 0.12.0. Source is
merged in [Python #34](https://github.com/fortyfive-labs/dreamlake/pull/34),
[CLI #47](https://github.com/dreamlake-ai/dreamlake-cli/pull/47), and
[backend #336](https://github.com/dreamlake-ai/dreamlake-workspace/pull/336).
Published on PyPI and GitHub; wheel/sdist downloads match the release artifacts. A fresh PyPI installation passed version/API/redaction checks. Hosted acceptance remains pending. The earlier bos14 snapshot is
separate evidence; see the [Vault Dev Note](https://docs.dreamlake.ai/dev/notes/vault-runtime/).

## Unreleased — populated-prefix KMS migration

`client.vault.kms.migrate`, bounded `resume`, and shared `status` pair with CLI commands. Preview counts retained entry and recovery records; response metadata strips secret/ciphertext fields. Unknown outcomes preserve the immutable operation ID. No hosted or package release is claimed.
