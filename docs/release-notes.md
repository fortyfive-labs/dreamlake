# Release notes

## 0.16.0 — 2026-09-15

**Published to PyPI.** Release [#57](https://github.com/fortyfive-labs/dreamlake/pull/57) includes reviewed Python #50/#51/#55/#56; tag `v0.16.0` pins source `ce428dc`. Public PyPI distributions and GitHub release assets match the hashes below. The older password-only candidate #52 was superseded.

- `client.runs.submit(setup=..., allow_vault_delivery=True, ...)` submits an explicitly reviewed, pinned repository and selected credential mappings. Repeating the exact request ID recovers uncertain submissions. Private output is discarded; Python never prompts or implicitly reads a setup file. [Paired CLI/Python guide](runs.md).
- `client.runs.capabilities(namespace)` reports authenticated server support and limits, separately from fresh host readiness. Unknown response fields are excluded and repository origins are validated. The CLI counterpart is `dreamlake runs capabilities`.
- `client.vault.verify_host_password` verifies a saved binding with a private one-shot password channel. Reservation/recovery methods preserve the immutable replacement and provide owner-only pending snapshot reads. [Password guide](vault-passwords.md).
- KMS migration acknowledges retained-record schema 2, including password snapshots. The compatible backend must be deployed before those operations; older clients remain safely gated. [KMS guide](vault-kms.md).

Private execution requires matching backend configuration and a verified worker. Nymph #45 and optional private-API CA support #47 are merged. [Ordinary-daemon acceptance #492](https://github.com/dreamlake-ai/dreamlake-workspace/pull/492) records real bos14 success, cancellation and expired-permit refusal with fresh published CLI 0.20.0/Python 0.16.0 and an unpublished post-47 daemon. That fixture uses isolated API/control-plane services and a synthetic encryption provider; hosted activation and shared-worker deployment remain separate. Remote password mutation and rollback resolution remain unfinished.

### Validation

Python 3.12 source suite: 667 passed, 60 skipped. A fresh wheel environment outside the source checkout passed all 63 run/capability/password tests. Sphinx HTML builds successfully; existing documentation warnings remain. Fresh sdist and no-cache PyPI installations passed version/API checks. These checks do not establish hosted acceptance.

Public artifact SHA256:

- Wheel: `910a4491868a9e1f2e4f34d9b7b226e2402e05b83177de02ab13af9eab148c2d`
- Source distribution: `471e5d88051952a3dfaaab917e74e72b84092f87a356302b4763d78ca6a91810`

[Release and assets](https://github.com/fortyfive-labs/dreamlake/releases/tag/v0.16.0) · [CLI/Python command guide](https://docs.dreamlake.ai/hosts/private-runs/).

## 0.15.0 — 2026-09-13

**Published to PyPI.** Reviewed release PR #48 merged as `b698d8f`; tag `v0.15.0` points to that exact source. Public wheel/sdist downloads match reviewed SHA256 hashes. A fresh installation from the public PyPI index reports 0.15.0 and passes all 55 installed-package rotation tests. This excludes unmerged password-probe work; published-package remote and hosted rotation acceptance remain separate.

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
