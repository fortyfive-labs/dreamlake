# Python 0.17.0 release candidate

Review-only, unpublished. Source `09d2af5b` is released `v0.16.2` plus the reviewed tree-only change and version/release notes. All 80 packaged runtime files in both wheel and sdist match source; existing runtime changes from the baseline are limited to the tree method in vault.py, plus the new vault_tree.py. Notes runtime is byte-identical to the published baseline. Full tests: 671 passed, 60 existing optional skips. Fresh wheel and sdist installs each passed real compiled-CLI HTTP/Mongo/PTY parity and installed schema tests.

Do not merge this candidate into the old release branch or main. Publish only after exact archive review from the new release branch/tag. The backend PR576 route is merged but not deployed at preparation time; client publication alone cannot make it available. No docs site/ref move.
