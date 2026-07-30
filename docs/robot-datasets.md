# Robot Datasets

`dreamlake.dataset` stores annotated robot-training episodes: each episode's
camera footage goes in once, gets transcoded so any browser can stream and
scrub it, and lives next to your pipeline's per-frame joint annotations and
action segments in one versioned dataset. An episode holds one or more
cameras (head, wrist, …) on a shared clock, so multi-view playback is
time-aligned by construction. On top of that, episodes can be embedded and
searched with natural language ("hands shaping clay on a pottery wheel" →
the pottery episode at 12s).

The intended caller is the last stage of a labeling pipeline: you already
hold the video file(s) and two Python dicts (joints, segments); one
`add_episode()` call publishes them.

## Install & authorize

```bash
pip install "dreamlake[search]"   # [search] adds CLIP/BGE for embed/search;
                                  # everything else works without it
pip install dreamdb               # the storage engine (separate package)
brew install ffmpeg               # transcoding + frame sampling

dreamlake login                   # once, interactive browser authorization
# or, for CI / scripts:
export DREAMLAKE_API_KEY=<token>
```

`dreamlake login` runs a device-code flow: it opens a browser page, you
approve, and a long-lived token is stored on this machine (OS keychain when
available). Every platform call the SDK makes carries that token; the server
checks your namespace membership and mints temporary, prefix-scoped S3
credentials — you never handle bucket keys.

## Quickstart

```python
from dreamlake.dataset import Dataset

ds = Dataset.create("wash-the-dishes")     # lives in the DreamLake bucket
# ds = Dataset.create(backend="file:///data/x")   # or anywhere dreamdb writes
# The playback encoding profile (preview_height=720, preview_fps=30,
# frag_seconds=2.0) is chosen HERE, once, for the dataset's lifetime.

# ... your pipeline produces `joints` and `segments` dicts per episode ...
epo = ds.add_episode("captures/Ceramics.mov", episode_id="Ceramics",
                     joints_pose=joints, subtasks=segments,
                     meta={"task": "wash the dishes"})
print(epo.report)                          # the ingest summary

# multi-camera: a dict of camera name -> file, all on the episode's clock
ds.add_episode({"head": "ep2_head.mov", "wrist": "ep2_wrist.mov"},
               episode_id="ep2", joints_pose=joints2, subtasks=segments2,
               meta={"task": "wash the dishes"})

ds.embed_episodes()                        # make it searchable (CLIP + BGE)
ds.search("hands rinsing a bowl")
```

`add_episode` returns an `Episode` handle — the per-episode surface. Later
sessions get it back with `ds.episode("Ceramics")`, and everything episode-
scoped (reads, `revise`, `add_cameras`, user tracks, single-episode `embed`)
lives on it.

Runnable version with a self-contained demo mode:
`docs/examples/09_robot_dataset.py` (`--demo`, `--platform NAME`).

## Calling order

```
Dataset.create("name")               once per dataset; fixes the encoding  ─┐
Dataset.open("name")                 every later session                    ─┴─→  ds
        (or backend="file:///…" / "https://…" on both)

ds.add_episode(...)                  once per episode      ──→  epo (Episode)
ds.episode(id) / ds.episodes()       the handle(s), any later session

epo.add_cameras(...)                 late cameras (video is add-only)
epo.revise(...)                      revised annotations / labels, atomic

ds.embed_episodes() / epo.embed()    any time after add_episode  ─┐  either path;
epo.add_search_vectors(...)          (bring your own vectors)    ─┘  vectors are
                                                                     searchable
                                                                     immediately
ds.search(...)                       ─┐
ds.episodes() / epo.info()            ├─ read back, any time, any order
epo.read_joints_pose() / epo.read_subtasks() ─┘
```

Rules the order implies:

- `create` refuses an existing dataset/name (409 on the platform) and `open`
  refuses a missing one — no silent get-or-create. Want it? `try open /
  except create` (the example does).
- **The encoding profile is a dataset-lifetime property.**
  `preview_height`/`preview_fps`/`frag_seconds` are chosen at `create`, once,
  and stored in the space meta — every clip on one camera track must share
  one init segment, so they cannot vary per episode and `add_episode` takes
  no encoding parameters. `open`'s encoding kwargs VERIFY, never set: pass
  the values your script assumes and `open` errors on mismatch (so the
  `try open / except create` idiom cannot silently eat a config edit).
- `ds.add_episode` creates; the `Episode` handle extends. Video fragments
  are never replaced — a camera can only be *added* to an episode
  (`epo.add_cameras`). Annotations and labels CAN be revised
  (`epo.revise`): storage is append-only, so a revision is a new version
  inside the episode's revision window and readers resolve to the newest
  (history stays addressable; see the schema contract for the anchor rule).
- **There is no index-build step.** Vector indexes are maintained on append —
  `search()` works the moment `embed`/`add_search_vectors` returns, and
  finds whatever exists so far.
- Platform credentials live ~12 h per handle; for longer sessions call
  `Dataset.open(name)` again.

## Function reference

### `Dataset.create(name=None, *, backend=None, visibility=None, preview_height=720, preview_fps=30.0, frag_seconds=2.0) -> Dataset`

| in | format | notes |
| --- | --- | --- |
| `name` | `[a-z0-9][a-z0-9._-]{0,63}` | platform mode: catalog row + platform bucket. Needs auth |
| `backend` | `file:///abs/path` or `https://…` S3 URL | self-hosted mode; `name` unused |
| `visibility` | `"private"` (default) / `"public"` | platform only; public episode meta omits absolute source paths |
| `preview_height` | int, default 720 | playback encode height (aspect preserved) |
| `preview_fps` | float, default 30.0 | playback frame rate (sources are resampled) |
| `frag_seconds` | 1.0–30.0, default 2.0 | CMAF fragment length |

The three encoding kwargs form the dataset's **encoding profile** — chosen
here, once, for the dataset's lifetime, and stored in the space meta under
`dreamdb.dataset.encoding`. Every ingest reads it; no per-episode call takes
encoding parameters. `ds.encoding` returns it (read-only).

**Raises** `DatasetError`: name and backend both missing; name already exists
(→ use `open`); a space already exists at `backend`; `frag_seconds` out of
range.

The dataset's schemaType (`video.annotation/v1`) is written to the platform
catalog **and** into the space itself, so viewers and `open()` can dispatch
on it even over a bare `file://` backend.

### `Dataset.open(name=None, *, backend=None, preview_height=None, preview_fps=None, frag_seconds=None) -> Dataset`

Same addressing arguments. The optional encoding kwargs **verify, never
set**: pass the values your script assumes and `open` raises on mismatch
with the stored profile; omitted kwargs are not checked. **Raises**
`DatasetError` when the dataset is missing, on an encoding mismatch, and
when the target space carries a *different* schemaType (including layouts
from older SDK iterations — read those with `dreamlake.db`, or re-ingest
the sources).

### `ds.add_episode(videos, *, episode_id=None, joints_pose=None, subtasks=None, meta=None, gid=None, raw=False) -> Episode`

`videos` is one path (a single camera, stored under the default name
`main`) or a dict of camera name → path. Camera names are `[a-z0-9_-]`, no
`__` (it separates the track prefix); each camera becomes its own set of
tracks — `video_preview__{camera}` / `video_raw__{camera}` /
`joints_pose__{camera}` — created on first use. All cameras share the
episode's one-hour slot, so multi-view playback is aligned with zero
bookkeeping.

**Joints bind per camera.** 2D keypoints live in one camera's pixel space,
so `joints_pose=` accepts either a bare doc (binds to the PRIMARY camera —
`main` when present, else the first in the dict) or a dict of camera name →
doc to annotate several views. Each doc is stored in that camera's
`joints_pose__{camera}` track — which is exactly how the viewer knows which
video to overlay it on: presence is binding, no pointer field. `subtasks`
is episode-level (segments live on the shared clock, camera-independent).

**Labels are explicit.** `meta=` accepts exactly the contract keys —
`{"task": ..., "scene": ...}` — and nothing is inferred (a `task` key inside
the subtasks doc is NOT copied to the episode meta). Everything else in the
episode's meta row is SDK-assembled from probing; custom per-episode data
belongs on an `x_` user track, not in meta.

**Returns** the `Episode` handle, with the ingest report on `.report`:
`{episode_id, gid, anchor, cameras: {camera: {preview: {fragments},
raw: {...}|None}}, joints_pose?: {camera: {annotated_frames}},
subtasks?: {segments}, meta}`.

Pre-transcode refusals (all `DatasetError`, all before any encoding time is
spent): any camera ≥ 3600 s; duplicate `episode_id` (→
`ds.episode(id).add_cameras()`/`revise()`); occupied `gid`; invalid camera
name; unknown `meta` key; annotation `src_fps` disagreeing with its camera
by >10 accumulated frames; **aspect ratio differing from that camera's
existing track** (aspect is per camera — head 4:3 and wrist 16:9 coexist
fine). Warns and continues: 1–10 frames of fps drift; archival track skipped
on a codec-config mismatch (lossless copies cannot be normalized — per
camera; only with `raw=True`, which is off by default since it roughly
doubles the upload).

### `ds.episodes() -> list[Episode]` / `ds.episode(episode_id) -> Episode`

Every episode as a handle, ordered by slot — one cheap column read
(`[e.meta for e in ds.episodes()]` recovers plain dicts) — and the lookup
for one (by id, or by slot number as a string; `DatasetError` when absent).
All per-episode verbs live on the handle; see the Episode reference below.

### `ds.cameras() -> list[str]` / `ds.tracks() -> list[TrackInfo]`

The dataset's cameras (union over episodes, `main` first, then
alphabetical — the viewer's order) and its track catalog: the fixed
episode-level tracks, each camera's namespace triple, and user tracks
declared through `add_track`. Each `TrackInfo` carries
`name`/`kind`/`role`/`camera`/`preset`. Columns added through the bare
`ds.db` handle are outside the contract and not listed.

### `ds.add_track(name, kind, *, mime=None) -> None`

Declare a user track in the reserved `x_` namespace. `kind` is the dreamdb
vocabulary, verbatim: `"image"`/`"video"` (with `mime=`; JSON documents are
`kind="image", mime="json"`) or a scalar (`"scalar_float"`, `"scalar_int"`,
`"scalar_bool"`, `"scalar_string"`, `"scalar_categorical"`,
`"scalar_timestamp"`). Names are enforced to `^x_[a-z0-9_]+$` with no `__`
(the camera separator) — the namespace the preset promises never to claim.
`"embedding"` is refused: DreamDB only accepts embedding fields at space
creation. Declaring an existing track is idempotent success. Write and read
through the Episode handle (`set_track`/`append_track`/`get_track`/
`read_track`); the web viewer does not render user tracks.

### `ds.embed_episodes(*, camera=None, fps=1.0, source_dir=None, batch_size=32) -> dict`

The all-episode sweep: `ds.episode(id).embed(...)` for EVERY episode (one
episode is the handle's `embed`). Each episode samples frames at `fps` from
one camera's SOURCE file (primary unless `camera=`), encodes them with CLIP,
encodes each subtask segment's text with BGE, and uploads all vectors.
Episodes commit one at a time — interrupt and re-run freely, uploads
deduplicate. `source_dir=` locates moved source files by joining with each
camera's recorded `source_rel`.

| in | format |
| --- | --- |
| `camera` | which camera's frames to embed (default: each episode's primary) |
| `fps` | frame sampling rate (1.0 = one frame per second) |
| `source_dir` | directory to resolve `source_rel` against, when the sources moved |
| `batch_size` | frames per CLIP batch |

**Returns** `{episode_id: {"frame_vecs": N, "subtask_vecs": M}}`. **Raises**
`DatasetError` when the `[search]` extra is missing. Warns and skips frame
vectors when an episode's source file cannot be found (subtask vectors
still upload).

### `ds.search(query, top_k=10, kind="both") -> list[dict]`

| in | format |
| --- | --- |
| `query` | natural language, English (the models are English CLIP/BGE) |
| `kind` | `"frames"` (visual), `"subtasks"` (labels), `"both"` (fused) |

**Returns**, sorted by fused score:

```python
[{"episode_id": "713488", "time_sec": 464.5, "score": 0.0164,
  "source": "subtask",                     # or "frame"
  "subtask": "screw shelf to brackets"},   # present on subtask hits
 ...]
```

First call loads the encoder models (a few seconds); warm queries are
sub-second. Recall is exact at small scale (an automatic exact-scan fallback
kicks in when the ANN index under-returns) and ANN-served as the dataset
grows.

### `ds.db` / `ds.encoding`

`ds.db` is the live `dreamdb.Dataset` handle under the preset — the escape
hatch for anything the preset does not wrap (absolute-anchor appends,
columnar bulk reads, branching); the preset's layout invariants are yours
to respect on it. `ds.encoding` is the dataset's playback encoding profile,
read-only.

## Episode reference

`Episode` is the per-episode handle — obtained from `ds.add_episode(...)`,
`ds.episode(id)` or `ds.episodes()`, never constructed directly. Its
identity triple is immutable forever (slots are never reused, episodes
never deleted), so a handle cannot dangle; the meta snapshot it carries is
a read convenience, and every write method re-reads the meta row at call
time — a stale handle can never clobber a newer revision.

### Identity & snapshot

| attribute | meaning |
| --- | --- |
| `epo.episode_id` | the stable, human-meaningful id |
| `epo.gid` | the slot number (positional) |
| `epo.anchor` | the slot's base anchor, ns |
| `epo.report` | ingest report — populated only on the handle `add_episode` returns |
| `epo.meta` | the episode_meta row content, as of the last fetch/refresh |
| `epo.cameras` | `{camera: {width, height, fps, duration_s, codec, source_rel, source_uri?}}` |
| `epo.task` / `epo.scene` | the contract labels, or `None` |
| `epo.duration_s` | longest camera's duration |

`epo.refresh()` re-reads the meta row (returns self); `epo.info()` returns
fresh meta plus annotation summaries (joint counts per camera, segment
count) — enough to verify an ingest without opening a browser.

### `epo.read_joints_pose(camera=None)` / `epo.read_subtasks()`

Byte-exact annotation read-back. `read_joints_pose` defaults to the primary
camera; name another `camera=` to read its document. Both return `None`
when absent (an unannotated camera is normal, not an error).

### `epo.add_cameras(videos, *, joints_pose=None, raw=False) -> dict`

Add late-arriving cameras. Add-only: a camera's fragments occupy its slot
and cannot be replaced (a camera the episode already has is refused — use a
new camera name or a new episode). The dict form adds N cameras with one
meta rewrite; `joints_pose` binds like in `add_episode`.

### `epo.revise(*, joints_pose=None, subtasks=None, meta=None) -> dict`

Revise annotations and/or the `task`/`scene` labels — ONE committed row for
everything passed (atomic). Storage is append-only: a revision is a new
version inside the episode's revision window; readers resolve to the newest
and the DreamDB timeline keeps history. `meta` accepts only the contract
keys, and there is no inference — a label exists iff you wrote it. Raises
when nothing is passed, or when the passed values match what is stored.

### User tracks: `epo.set_track` / `append_track` / `get_track` / `read_track`

The write/read surface for tracks declared with `ds.add_track` (the `x_`
namespace):

| call | meaning |
| --- | --- |
| `epo.set_track(name, value, t_sec=0.0)` | one value at `t_sec` (default 0 — the one-value-per-episode pattern). Same `(track, time)` again = revision: readers see the newest, history kept. Dicts/lists store as JSON |
| `epo.append_track(name, [(t_sec, value), ...])` | batch write, one commit — the right shape for scalar time series within an episode |
| `epo.get_track(name, t_sec=0.0)` | the value at exactly `t_sec`, or `None` |
| `epo.read_track(name, start_sec=0.0, end_sec=None)` | all `(t_sec, value)` in the window, sorted (whole episode by default) |

Timestamps are seconds on the episode's own clock, bounds-checked to its
slot. For high-rate series and cross-episode bulk reads, drop to `ds.db`
with `epo.anchor_at` (see "Beyond the preset").

### `epo.anchor_at(t_sec) -> int`

The absolute anchor (ns) of second `t_sec` on this episode's clock,
bounds-checked to the slot — the bridge to `ds.db`.

### `epo.embed(*, camera=None, fps=1.0, video_path=None, source_dir=None, batch_size=32) -> dict`

Encode + upload this episode's search vectors (the single-episode spelling
of `embed_episodes`). Frames are sampled from one camera's SOURCE file —
resolved `video_path=` override → `source_dir=`/`source_rel` join →
recorded `source_uri` — and a file whose duration disagrees with the
recorded camera duration is refused (wrong file). Returns
`{"frame_vecs": N, "subtask_vecs": M}`.

### `epo.add_search_vectors(*, frame_vecs=None, subtask_vecs=None) -> dict`

The pure-upload half, for pipelines that run their own encoders (see
`dreamlake.encoders` for the matching models — vectors must be CLIP-space
512-d for frames and BGE-space 384-d for subtask text, L2-normalized).

| in | format |
| --- | --- |
| `frame_vecs` | `[(t_sec: float, vec512)]` — time on the episode's own clock |
| `subtask_vecs` | `[(t_sec, vec384, label: str)]` — anchored at segment start; `label` is shown on hits |

## The schema contract

The preset's field set is **fixed per schemaType** and owned by the SDK.
Three rules govern its evolution, shared by the Python SDK, the TypeScript
CLI and the web viewer:

1. **Within one schemaType, evolution is only ever addition.** New SDK
   releases may add tracks (all fields are optional by construction); they
   never rename, retype or remove one.
2. **There is no minor version number — track presence IS the version
   signal.** A reader renders/uses the tracks it finds and skips the ones it
   doesn't know; a writer writes the tracks it has data for. Presence-based
   behavior is more robust than a version stamp: data written by an older
   SDK and data half-written by a newer one get the same, correct treatment.
   The controlled camera namespace (`video_preview__*`, `video_raw__*`,
   `joints_pose__*`) works the same way — viewers discover cameras and
   their overlays by enumerating fields, no registration step and no
   pointer fields.
3. **schemaType bumps only when layout SEMANTICS change** — the meaning of
   a slot, the structure of `episode_meta`, anything a tolerant reader
   cannot absorb. Tools branch on schemaType (or refuse clearly), never
   guess.

### The revision-anchor rule

DreamDB resolves same-anchor duplicates by **content hash, not write
order** — "last writer wins" does not exist at the engine level, and a
tombstone kills an anchor permanently. So a revision of an episode-level
value is never re-appended at the same anchor: it is written at the **next
free nanosecond** after the newest existing version — `base_anchor + 1`,
`+ 2`, … — inside a revision window of `REVISION_WINDOW_NS = 1024` ns, and
every reader resolves a value to the **highest anchor within
`[t, t + 1024)`**. 1024 ns is invisible at any media time scale (a 240 fps
frame is 4.2 million ns) and bounds each value to 1024 revisions — exhaust
the window and the SDK tells you to re-ingest as a new episode. This is a
cross-SDK contract: the Python SDK, the TypeScript CLI and the web viewer
all resolve the same way.

### Space-meta keys

Three keys in the space meta are part of the contract (mirrored in the TS
CLI's `schema.ts`):

| key | holds |
| --- | --- |
| `dreamdb.dataset.encoding` | the encoding profile JSON (`preview_height`/`preview_fps`/`frag_seconds`), written at `create`; absent = the defaults (older spaces) |
| `dreamdb.dataset.public` | `"1"` when created with `visibility="public"` — episode meta then omits absolute source paths |
| `dreamdb.dataset.user_tracks` | JSON `{track_name: kind}` registry of `x_` tracks declared via `add_track` |

### The `episode_meta` row

One JSON row per episode (one track rather than many scalars, so listing a
dataset is a single column read):

```jsonc
{
  "episode_id": "Ceramics",          // stable id — key your pipeline off this, not gid
  "task": "throw a ceramic bowl",    // only if passed via meta=
  "scene": "studio-3",               // ditto
  "primary_camera": "head",
  "src_fps": 29.987,                 // primary camera's rate; its joints doc's value wins over probe
  "duration_s": 214.6,               // longest camera
  "cameras": {
    "head": {
      "width": 1920, "height": 1080, "fps": 29.987,
      "duration_s": 214.6, "codec": "hevc",
      "source_rel": "captures/Ceramics.mov",  // last two source path components — joins with embed's source_dir=
      "source_uri": "/data/captures/Ceramics.mov"  // absolute; omitted on public datasets
    }
  }
}
```

There is **no top-level `width`/`height`/`total_frames`** — every camera's
geometry lives under `cameras`, and `primary_camera` names the default one.
Joint annotations bind to cameras by track name (`joints_pose__{camera}`),
not through this meta.

### Preset naming rules

As more preset schemas join `video.annotation/v1`, the structure is
class-per-schemaType:

- **One schemaType = one preset class.** Schema-specific methods live only
  on their preset's classes — the class boundary, not a naming prefix, is
  what tells you which methods belong to which schema. `epo.read_joints_pose`
  needs no qualifier because the episode already belongs to a
  video-annotation dataset.
- **Method names are verb + the schema's own nouns, and the nouns are its
  track names**: `read_joints_pose` ↔ `joints_pose__*`, `read_subtasks` ↔
  `subtasks`, `add_episode` ↔ the layout's row unit. A future preset brings
  its own vocabulary (`add_trip`, `read_lane_labels`, …), self-consistent
  within its own class.
- **Planned layering** (deferred until a second preset actually exists):
  a shared base (create/open/`.db` + schemaType dispatch), an episode-slot
  layer (slot math, camera namespace, `episodes()`/`info()`) for
  video-shaped presets, and the per-schema leaf class — with modules under
  `dreamlake.datasets.<preset>` and today's `dreamlake.dataset.Dataset`
  kept as an alias.
- **No string-driven generic accessors** (`read_annotation("joints_pose")`)
  — named methods carry the validation, docs and discoverability that make
  a preset worth having; generic access is what `ds.db` is for.

### Beyond the preset

Need columns the preset doesn't define (IMU, gripper state, reward, …)?
Three sanctioned paths, in order of preference:

- **User tracks (`x_` namespace)** — the preset's own extension surface.
  Declare once with `ds.add_track("x_reward", "scalar_float")` (dreamdb
  kinds, verbatim; `^x_[a-z0-9_]+$`, no `__` — the namespace the preset
  promises never to claim, so future SDK releases cannot collide), then
  write and read through the Episode handle:

  ```python
  ds.add_track("x_reward", "scalar_float")
  epo.set_track("x_reward", 0.75)                     # one value per episode
  epo.append_track("x_reward", [(1.0, 0.1), (2.0, 0.2)])  # a time series
  epo.read_track("x_reward")                          # [(t_sec, value), ...]
  ```

  The viewer does not render user tracks; they are still first-class data
  for training loaders, on the episode's clock, with the same
  revision-window versioning as everything else.
- **High-rate series through `ds.db`**: for hundreds of samples per second,
  skip the per-item revision bookkeeping and append absolute-anchor rows on
  the live dreamdb handle — `epo.anchor_at` is the bridge that keeps them
  on the episode's clock:

  ```python
  ds.add_track("x_gripper", "scalar_float")   # declare through the preset
  ds.db.append_many([
      {"_anchor": epo.anchor_at(t), "x_gripper": v} for t, v in series
  ])
  ```

  The preset's layout invariants (slot bounds, the `x_` namespace) are
  yours to respect on this handle; cross-episode bulk reads are
  `ds.db.iter_all_batches`.
- **Fully custom**: build your own schema with `dreamlake.db` (`db.create`
  accepts any `dreamdb.Schema`, including embedding fields, which only
  exist at creation) — your layout, your schemaType, your viewer story.

If a signal is broadly useful, propose it for the preset instead — a
standardized track gets a name, a shape, and a renderer for everyone.

## Annotation formats

Wire-compatible with the web viewer's overlay renderers — upload these
shapes and the skeleton/captions draw with no conversion. Joint coordinates
live in the pixel space of the camera whose `joints_pose__{camera}` track
holds the document.

### `joints_pose`

```jsonc
{
  "width": 1920, "height": 1080,   // REQUIRED: pixel space (original video, upright)
  "src_fps": 29.987,               // REQUIRED: true rate; frame k renders at k/src_fps
  "total_frames": 2024,            // optional
  "joint_order": ["wrist", ...],   // names, index-aligned
  "bones": [[0, 1], ...],          // skeleton edges
  "frames": {                      // REQUIRED, SPARSE: only annotated frames
    "0": [{"is_right": 1, "det_conf": 0.9,
           "bbox": [x1, y1, x2, y2],            // optional
           "keypoints_2d": [[x, y], ...]}]      // REQUIRED
  }
}
```

`joint_order`/`bones` make the track self-describing: a 21-joint hand, a
full body, or a manipulator arm all fit.

### `subtasks`

```jsonc
{
  "task": "wash the dishes",           // optional, NOT copied to episode meta — pass meta={"task": ...}
  "labeled_subtasks": [                // REQUIRED; gaps are fine
    {"start_sec": 0.0, "end_sec": 2.5, "subtask": "pick up plate"}
  ]
}
```

## Visualizing

Platform datasets appear in your namespace's catalog (the web dataset list
dispatches its viewer on schemaType). For a local dataset, serve the
directory and open the debug viewer:

```bash
npx http-server /data/datasets/wash-the-dishes -p 8791 --cors
open 'http://localhost:3000/dataset-debug?space=http://localhost:8791/refs/main'
```

The TypeScript CLI reads and writes the same datasets
(`dreamlake dataset ls|info`) — same schema contract
(`dreamlake-cli src/cli/dataset/schema.ts`; change one, change both).

## Constraints worth knowing up front

- **≤ 3600 s per camera clip** (one-hour timeline slot per episode; every
  camera shares it).
- **The encoding profile is fixed at `create`** — `preview_height`/
  `preview_fps`/`frag_seconds` live in the space meta for the dataset's
  lifetime; `open`'s encoding kwargs verify against it, and no per-episode
  call takes encoding parameters.
- **One aspect ratio per camera track** — not per dataset. Each camera's
  playback track shares one init segment; different cameras are different
  tracks, so mixed geometries coexist as long as each camera stays
  consistent with itself.
- **Video fragments are add-only** — a camera cannot be re-uploaded into an
  episode (`epo.add_cameras` only adds new ones); annotations and labels
  CAN be revised (`epo.revise`), with history kept and a budget of 1024
  revisions per value (the revision window).
- **`meta=` is a whitelist** — only `task` and `scene`; custom per-episode
  data goes on `x_` user tracks.
- **Archival is opt-in (`raw=True`) and best-effort on mixed sources**
  (lossless copies keep their codec config; each camera track holds exactly
  one).
- **Search fields are declared at creation.** Embedding tracks cannot be
  retrofitted — datasets from older layouts must be re-created to become
  searchable.
- **Append-only.** Nothing is rewritten; every published state stays
  addressable.
