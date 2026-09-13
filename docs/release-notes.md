# Release notes

## 0.13.0 — release candidate, 2026-09-13

**Not yet published.** Paired CLI candidate: 0.16.0. The matching Vault backend
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
