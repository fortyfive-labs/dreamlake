# Package C SDK — local validation

October 5, 2026. Integrates A #92 `ab360695` and B #91 `a561b18`.
Adds ownership resource inspection/manifest/explicit unsupported restore, and
`client.sources` detail, editors/add/remove, disable/enable and terminal deletion.
Writes require caller-supplied exact retry keys; no automatic write retry occurs.
Deleting queues an operation, not completion or restoration.

| Validation | Result |
| --- | --- |
| `uv run --with pytest pytest -q tests/test_ownership.py tests/test_sources.py` | 6 passed |
| `git diff --check` | passed |

Mock-transport tests cover auth, encoded namespace paths, exact preview/key
retries, unsupported direction/type/key rejection, structured denial without raw
private messages, Source lifecycle action syntax, editor mutations and 202/204
responses. These are client transport checks, not real transfers/byte preservation.
Python API source docs were updated; package publication and live server parity
are unverified. Complete project management/lifecycle SDK coverage remains open.

C execution is disabled pending atomic/writer/storage/destination gates. B
credential export/reseal and D RTC/checkpoint/media gates are not implemented by
these clients. See the companion workspace PR for exact blockers. No production
deploy, CI polling, live mutations or secret reads occurred.
