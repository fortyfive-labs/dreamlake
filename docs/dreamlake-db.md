# dreamlake.db — the storage engine, platform-free

`dreamlake.db` is the bottom layer of the stack: **DreamDB, re-exported
verbatim, bound to nothing**. It never talks to the DreamLake platform — no
login, no catalog, no credential brokering. You bring a backend URI and a
schema; it gives you a bare `dreamdb.Dataset`.

The platform experience (managed bucket, catalog entry, web visualization)
belongs to the **presets** built on top — `dreamlake.dataset` — which
register the dataset in your namespace and broker short-lived scoped
credentials internally, then operate on exactly the same kind of plain
dreamdb handle this module hands out. The layering:

```
dreamlake.dataset (presets)   fixed schemas + platform binding + viewers
        │ uses
dreamlake.db (this module)    dreamdb verbatim + create/open on a backend
        │ is
dreamdb                       the storage engine
```

Use `dreamlake.db` when your data does not fit a preset: define your own
schema, upload by your own structure, host it on your own storage. Custom
data is not visualizable in the DreamLake UI — that is what presets are for.

## Calling order

```
schema = db.Schema()....                    describe your structure
ds = db.create(schema, backend="…")         once per dataset      ─┐
ds = db.open(backend="…")                   every later session    ─┴─→ bare dreamdb.Dataset

ds.append_many([...])                       ─┐
ds.ingest_video(...) / ingest_cmaf(...)      ├─ from here on: pure DreamDB API
ds.iter_all_batches / iter_vector / ...     ─┘
```

## Function reference

### `db.create(schema, *, backend, schema_type="custom", title=None) -> dreamdb.Dataset`

| in | format | notes |
| --- | --- | --- |
| `schema` | `db.Schema()` builder | required |
| `backend` | `file:///abs/path` or `https://…` object-store URL | **required** — this layer has no default home |
| `schema_type` | free string, e.g. `"my-lab.frames/v1"` | stamped into the space meta so readers can dispatch on it |
| `title` | free string | optional human-readable name, stamped into meta |

The ref name is always `"main"` — a fixed contract shared with the
TypeScript CLI and the web viewer.

### `db.open(backend, *, schema=None) -> dreamdb.Dataset`

Opens the space at `backend`. `schema=None` recovers the schema persisted
in the dataset itself; an explicit schema must match the persisted one.

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

# 2. Create on YOUR backend — a directory, or S3 with your credentials
#    in the environment.
ds = db.create(schema, backend="file:///data/my-experiments",
               schema_type="my-lab.frames/v1")

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

## Where the platform flow lives now

Platform datasets are created through presets — `Dataset.create("name")` in
`dreamlake.dataset` — which internally: authenticate with your
`dreamlake login` / `DREAMLAKE_API_KEY` identity, register the catalog row
in your namespace (what the web UI lists and dispatches viewers on), obtain
temporary S3 credentials scoped to that dataset's folder (~12 h), and open
the space directly against the bucket. Data never flows through the API
server. See `docs/robot-datasets.md`.

## Constraints inherited from the engine

- **Backend URIs** are `file:///abs/path` or `https://…` object-store URLs —
  there is no `s3://` scheme.
- **One `append_many` call = one field shape.** Mixed-shape batches are
  rejected; make one call per shape.
- **Embedding fields must be declared at create time**; text/BM25 indexes
  are built once over a full corpus. Plan the schema before the first row —
  scalar/image/video fields, by contrast, can be added at any time
  (evolution by addition).
