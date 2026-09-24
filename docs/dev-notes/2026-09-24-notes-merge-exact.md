# 2026-09-24 — Notes Python MERGE/EXACT parity

Tracking: https://github.com/dreamlake-ai/dreamlake-workspace/issues/706

Python 0.20.0 separates the original RTC baseline from an optional EXACT check.
`read_snapshot()` is additive so the existing editable `Doc` and its content
ETag are not silently converted to an RTC revision. Default `patch` requires
`base_revision` and returns a structured `PatchReceipt`; it neither reads a
newer baseline nor sends a cached legacy ETag. EXACT is per request through
`exact=True`; explicit `if_match` remains an exact-mode compatibility alias.

The legacy patch contract is explicit `legacy=True` and keeps `PatchResult(str)`
with ETag and size metadata. New receipts expose content hash and RTC revision
as separate fields, and `to_dict()` matches CLI/API camel-case JSON. Source hash,
Note identity, mode, baseline, and returned revision are validated before a
successful receipt or cache advance. Errors retain draft/baseline files and
cached legacy state. There is no automatic HTTP replay after an ambiguous ack.

Publishing uses the repository's GitHub `publish-production` workflow and PyPI
OIDC from the reviewed merged commit. Source tests, isolated built-package
readback, docs build, publication, and live integration are separate gates.
