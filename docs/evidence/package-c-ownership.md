# Package C SDK — merge preparation evidence

October 5, 2026. Integrates ready A #92
`b004e9e40eda0c0f1a6a20ea25e601c86a7ebb00` and B #91
`2ae5a18bf5bc5bc5a27ce78c9ac708ad89f165c0`.
Adds ownership resource inspection/manifest/explicitly unsupported restore and
client.sources detail, editors/add/remove, disable/enable and terminal deletion.
Writes require exact caller-owned retry keys, with no automatic write retries.
Deletion queues an operation rather than proving completion/restoration.

| Local validation | Result |
| --- | --- |
| CI-equivalent pytest, dev and dreamdb extras | 1104 passed, 75 skipped |
| Wheel and source distribution build | passed |
| Sphinx HTML docs build | passed, 43 warning messages |
| Whitespace | passed |

```bash
uv sync --frozen --extra dev --extra dreamdb
PATH="/path/to/openssl3/bin:$PATH" uv run --frozen --extra dev --extra dreamdb pytest -q tests test
uv build
uv run --frozen --extra dev --extra dreamdb sphinx-build -b html docs docs/_build/html
```

The initial collection run lacked dreamdb extras; CI-equivalent installation
resolved it. One host-bootstrap fixture failed with macOS default LibreSSL.
Installed OpenSSL 3 through process-local PATH resolved it without code or test
changes. Ownership/Source transport tests cover authorization, encoded paths,
exact keys, invalid requests, structured denial and 202/204 semantics. These
checks do not prove C ownership cutover, byte/lineage preservation or live parity.

Python API source docs are updated. Complete project lifecycle/management remains
open. C execution remains disabled pending writer/atomic/storage/destination
gates. B supports explicit source-retaining copies, reseal/recovery and serialized
final release; move inventory and customer-KMS destination reseal remain gated.
C never implicitly copies credentials. Selected-note D proof remains required.
Package publication and deployed server parity are unverified. Normal push CI
is requested; new remote CI is unobserved and unverified. No polling, merge,
deployment, live mutations, credential export or C transfer execution occurred.
