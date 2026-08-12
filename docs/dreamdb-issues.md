# DreamDB engine issues found while building `video.annotation/v1`

Two storage-engine behaviors discovered empirically (dreamdb from this
repo's `.venv`, July 2026). Both have SDK-side mitigations in
`dreamlake.annotation`; both deserve engine fixes. File these upstream.

## 1. Same-anchor duplicate resolution is content-ordered, not last-writer-wins

**Expected**: re-appending a value at an existing anchor reads back as the
newest write (LWW).
**Actual**: resolution is independent of write order — swapping the write
order of two distinct payloads returns the SAME winner, i.e. the engine
picks by content (hash order). Small payloads often *appear* LWW by luck.

Repro (scalar; blobs behave the same):

```python
import dreamdb
s = dreamdb.Schema(); s.add_scalar_string("m", required=False)
ds = dreamdb.Dataset.create("main", s, "file:///tmp/x1")
A = '{"episode_id":"e0","pad":"...~250 bytes...","scene":"kitchen"}'
B = '{"episode_id":"e0","pad":"...~250 bytes...","scene":"bench"}'
ds.append_many([{"_anchor": 0, "m": A}])
ds.append_many([{"_anchor": 0, "m": B}])
# reads "kitchen" — and ALSO reads "kitchen" if B is written first.
```

Also note: `delete([anchor])` tombstones the anchor permanently — a
subsequent append at that anchor is suppressed too — so delete+re-append is
not a usable revision primitive either.

**SDK mitigation**: revisions are written at `anchor+1, +2, …` within
`REVISION_WINDOW_NS = 1024`; every reader resolves to the highest anchor in
the window. Engine fix would let the window collapse back to true LWW.

## 2. Manifest keeps a stale empty track segment (duplicate same-name entries)

After the interleave *dynamic schema evolution (`add_video`/`add_image`) +
`ingest_cmaf` + a subsequent multi-field blob append*, the published
manifest lists a field twice: the empty declaration-time segment (all empty
tracks share one content address) plus the data segment. Reads are correct
(readers merge segments); the manifest accumulates clutter and confuses
track listings.

Repro: create a space, `ds.add_video("cam", ...)` + `ds.add_image("j", ...)`
dynamically, `ingest_cmaf("cam", ...)`, then
`append_many([{"_anchor": 0, "j": b"{}", "declared_blob": b"{}"}])` →
manifest holds 2 entries for `j` and for `declared_blob`. Declaring the
fields before the ingest does NOT avoid it; declaring them at create does.
Scalar fields are unaffected (full-track rewrite keeps one entry).

## 3 (minor). Scalar append amplification

Each scalar-field append rewrites the whole scalar track (verified: N
same-field appends leave one manifest entry). Per-episode meta rows make
bulk ingest O(N²) bytes on that track. Mitigations in SDK (skip unchanged
meta rewrites); engine-side bucketing / hot-shard fold would remove the
amplification.
