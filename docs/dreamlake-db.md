# dreamlake.db — custom-schema data on the platform

`dreamlake.db` is the thin layer under the robot-dataset preset, exposed for
everyone whose data does not fit a preset: **define your own schema, upload
by your own structure**. It is deliberately not an abstraction — it
re-exports the DreamDB SDK verbatim and adds exactly three things:

1. **authorization** — your `dreamlake login` / `DREAMLAKE_API_KEY` identity,
2. **a default home** — the DreamLake platform bucket, one managed folder per
   dataset, temporary scoped credentials minted per session,
3. **a catalog entry** — name, schemaType and visibility in your namespace,
   which is what the web UI lists and dispatches viewers on.

You install and import only `dreamlake`. Everything storage-level
(`db.Schema`, `db.Dataset`, queries, layers, snapshots) is the DreamDB API,
forwarded unchanged — its own docs apply as-is.

## Calling order

```
$ dreamlake login                          once per machine (or DREAMLAKE_API_KEY)

schema = db.Schema()....                   describe your structure
ds = db.create("name", schema, ...)        once per dataset      ─┐
ds = db.open("name")                       every later session    ─┴─→ bare dreamdb.Dataset
ds = db.open_backend("file:///…")          escape hatch: any dreamdb backend

ds.append_many([...])                      ─┐
ds.ingest_video(...) / ingest_cmaf(...)     ├─ from here on: pure DreamDB API
ds.iter_all_batches / iter_vector / ...    ─┘

db.list() / db.delete("name")              catalog management
```

## Function reference

### `db.create(name=None, schema=None, *, backend=None, schema_type="custom", schema_json=None, visibility=None, duration_seconds=43200) -> dreamdb.Dataset`

| in | format | notes |
| --- | --- | --- |
| `name` | `[a-z0-9][a-z0-9._-]{0,63}` | required in platform mode |
| `schema` | `db.Schema()` builder | required for create |
| `backend` | dreamdb URI | given → skip the platform entirely |
| `schema_type` | free string, e.g. `"my-lab.frames/v1"` | the dispatch key: stored in the catalog AND stamped into the space meta. Viewers select by it |
| `visibility` | `"private"` (default) / `"public"` | catalog listing visibility |
| `duration_seconds` | 900–43200 | credential lifetime for this handle |

**Returns a bare `dreamdb.Dataset`** — not a wrapper. A `dreamlake_lease`
attribute carries `{backend_url, expiration, namespace, name, prefix}`.

**Raises** `db.DatasetExistsError` (409 → use `db.open`),
`NotAuthenticatedError` (run `dreamlake login`), `db.PlatformError` with the
server's message on other failures.

### `db.open(name=None, *, backend=None, schema=None, duration_seconds=43200) -> dreamdb.Dataset`

Opens by platform name (catalog lookup → credentials → open) or by backend
URI. `schema=None` recovers the schema stored in the dataset itself.
**Raises** `db.DatasetNotFoundError` (→ use `db.create`).

### `db.open_backend(backend, schema=None)` — alias of `db.open(backend=...)`.

### `db.list(schema_type=None) -> list[dict]`

Your namespace's datasets: `[{name, schemaType, visibility, createdAt,
updatedAt}]`, optionally filtered.

### `db.delete(name, purge=False) -> None`

Soft-deletes the catalog entry; `purge=True` also deletes the stored objects.

### Everything else: the DreamDB API, re-exported

`db.Schema` (with `add_image/add_video/add_audio/add_embedding/add_scalar_*`),
and on the returned dataset: `append_many` (rows are dicts, reserved
`"_anchor"` key = int nanosecond timestamp), `ingest_video`/`ingest_cmaf`
(browser-streamable video), `iter_all_batches`/`iter_stream` (bulk reads),
`query_scalar`/`iter_scalar`/`distinct_values` (value lookups),
`iter_vector` (ANN), `snapshot`/`branch`/`history` (versioning), layer APIs
(retrofit columns onto existing rows). Refer to the dreamdb package docs for
each — nothing is renamed or wrapped.

## Worked example: a custom store

```python
import numpy as np
import dreamlake.db as db

# 1. Your structure. Every field optional so the schema can grow later.
schema = (db.Schema()
    .add_image("frame", mime="jpeg", required=False)
    .add_scalar_categorical("label", required=False)
    .add_scalar_float("score", required=False)
    .add_embedding("vec", dim=512, required=False))

# 2. One call: catalog row + managed folder + credentials + creation.
ds = db.create("my-experiments", schema, schema_type="my-lab.frames/v1")

# 3. Upload rows in your own shape. `_anchor` is the primary key (int ns);
#    omit it and one is assigned. All rows in ONE call must carry the same
#    field set — split differing shapes into separate calls.
ds.append_many([
    {"_anchor": 1_000_000_000, "frame": jpeg_bytes,
     "label": "cat", "score": 0.93, "vec": embedding.astype(np.float32)},
])

# 4. Query with the bare DreamDB API.
ds.query_scalar("label", "==", "cat")            # -> anchors
ds.iter_vector("vec", query_vec, top_k=10)       # -> ANN batches
for batch in ds.iter_all_batches(fields=["frame", "label"]):
    ...
```

Input formats `append_many` enforces per field class: `bytes` for
image/audio/video, `str` for text/categorical/string scalars, `int`/`float`/
`bool` for the numeric scalars, `list[float]` or numpy for embeddings
(dimension checked against the schema).

## How authorization works underneath

```
dreamlake login ──(device code, browser)──► vuer-auth ──► POST /auth/exchange
      └─► long-lived DreamLake token, stored in the OS keychain

db.create("name") ──Bearer token──► dreamlake-server
      ├── membership check on your namespace
      ├── catalog row  (datasets/<namespace>/<name> is the managed folder)
      └── temporary S3 credentials, scoped to that folder, ~12 h
              └─► the SDK places them in the environment and opens the
                  dataset directly against the bucket — data never flows
                  through the API server
```

Resolution order for the token: `DREAMLAKE_API_KEY` env var, then the stored
login. Server URL: `DREAMLAKE_REMOTE` env var, then the saved config, then
the production default.

## Constraints inherited from the engine

- **Credentials are read once per handle.** A handle works for its lease
  (~12 h); for longer jobs call `db.open` again and continue.
- **Backend URIs** are `file:///abs/path` or `https://…` object-store URLs —
  there is no `s3://` scheme.
- **One `append_many` call = one field shape.** Mixed-shape batches are
  rejected; make one call per shape.
- **Embedding fields must be declared at create time**; text/BM25 indexes
  are built once over a full corpus. Plan the schema before the first row —
  scalar/image/video fields, by contrast, can be added at any time.
- `list_refs()`/`gc()` don't operate on platform datasets (prefix-scoped
  folders); the platform's `db.delete(purge=True)` handles cleanup instead.
