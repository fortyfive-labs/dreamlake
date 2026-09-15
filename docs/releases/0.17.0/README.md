# Python 0.17.0 release candidate

PyPI publication verified on 2026-09-15; GitHub v0.17.0 is published and both attached archive hashes verified. The candidate details below are historical build evidence. Source `09d2af5b` is released `v0.16.2` plus the reviewed tree-only change and version/release notes. All 80 packaged runtime files in both wheel and sdist match source; existing runtime changes from the baseline are limited to the tree method in vault.py, plus the new vault_tree.py. Notes runtime is byte-identical to the published baseline. Full tests: 671 passed, 60 existing optional skips. Fresh wheel and sdist installs each passed real compiled-CLI HTTP/Mongo/PTY parity and installed schema tests.

Historical preparation constraints: this tree-only candidate was not merged into the old release branch or main. At preparation, backend PR576 was merged but not deployed. The subsequent public installed-SDK production tree check is recorded separately below. No docs site/ref move occurred.

Reproduce archive and source comparisons using the existing `dist/` artifacts, with Git available on PATH:

```shell
python3 scripts/verify_tree_release.py
```

This checks exact archive hashes/sizes, every packaged runtime file against the pinned build commit, the complete runtime difference from v0.16.2, and both changed files against reviewed PR66. It rejects extra runtime files and does not rebuild archives.


[Public publication receipt](publication.json) records both archive hashes and the fresh installed-SDK production metadata check. Verified annotated v0.17.0 tag target: `a1450d425a2520e42cc72cf0789aa1f28302d122`; its src/, pyproject.toml and uv.lock bytes exactly match build source `09d2af5b`. The tag remains on that reviewed release source; this documentation update does not move it.
