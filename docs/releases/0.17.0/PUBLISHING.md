# Guarded frozen Python publication

The separate publisher script pins the two reviewed archives from build source
09d2af5b397f66b1f42c254a80134c77d44b43d5. Do not rebuild from current main or
merge PR67 into its old release base. This script makes no Git/tag changes.

```shell
python3 scripts/publish_frozen_tree.py validate --root /absolute/frozen-candidate
python3 scripts/publish_frozen_tree.py reconcile --root /absolute/frozen-candidate
```

Reconcile is read-only and checks public PyPI metadata and actual downloaded file
SHA256/size. Missing files are not proof an uncertain prior upload failed. Preserve
any existing intent; never bypass it by choosing a new journal to retry.

After root review of the exact release gate under existing authorization:

```shell
python3 scripts/publish_frozen_tree.py publish --root /absolute/frozen-candidate --pypirc /absolute/protected/pypirc --journal /owned/durable/python017-intent.jsonl
```

The journal directory must be private and durable. The exclusive 0600 journal and
its parent directory are fsynced before network mutation. Each archive has its own
attempting/verified record. Any exception or uncertain readback stops; reconcile
before deciding the next action. No automatic retry or inferred success is used.

The credential loader rejects symlinks, foreign ownership and multiple hard links.
`--secure-pypirc` permits an owned regular single-link file to be tightened to 0600
before parsing. The authorized local check changed `/Users/ge/.pypirc` from 0644
to 0600 on 2026-09-15, preserving inode; token format passed, upload permission
was not tested. No credential is logged or put in argv. It exists transiently in
the child environment. Raw child output is discarded.

`uv publish` runs one exact temporary archive with no config, cache, attestations,
keyring or trusted-publishing discovery, and UV_HTTP_RETRIES=0. Temporary archive
custody is removed on normal completion/error; abrupt termination may require
inspection of the exact owned temporary directory. No shared HOME/config override.

Validation: six tests passed including a real installed-uv HTTP test against an
owned loopback endpoint returning500, which observed exactly one POST. The token
was synthetic and the wheel was the frozen public candidate; no PyPI upload ran.
Both local archive hashes/sizes validated. This is publisher transport testing,
not a successful release or token-permission proof.

```shell
PYPI_PUBLISH_TEST_WHEEL=/absolute/frozen-candidate/dist/dreamlake-0.17.0-py3-none-any.whl python3 -m unittest discover -s scripts -p test_publish_frozen_tree.py -v
```

Retry setting: https://docs.astral.sh/uv/reference/environment/#uv_http_retries
