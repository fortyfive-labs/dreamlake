# Release notes

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
