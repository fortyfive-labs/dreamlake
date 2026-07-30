# Robot Datasets

`dreamlake.dataset` stores annotated robot-training video: raw footage goes in
once, gets transcoded so any browser can stream and scrub it, and lives next
to your pipeline's per-frame joint annotations and action segments in one
versioned dataset. On top of that, videos can be embedded and searched with
natural language ("hands shaping clay on a pottery wheel" → the pottery
video at 12s).

The intended caller is the last stage of a labeling pipeline: you already
hold the video file and two Python dicts (joints, segments); one
`add_video()` call publishes them.

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

# ... your pipeline produces `joints` and `segments` dicts per video ...
ds.add_video("captures/Ceramics.mov", video_id="Ceramics",
             joints_pose=joints, subtasks=segments)

ds.embed_videos()                          # make it searchable (CLIP + BGE)
ds.search("hands rinsing a bowl")
```

Runnable version with a self-contained demo mode:
`docs/examples/09_robot_dataset.py` (`--demo`, `--platform NAME`).

## Calling order

```
Dataset.create("name")               once per dataset      ─┐
Dataset.open("name")                 every later session    ─┴─→  ds
        (or backend="file:///…" / "https://…" on both)

ds.add_video(...)                    once per video

ds.embed_videos(...)                 any time after add_video   ─┐  either path;
ds.add_search_vectors(...)           (bring your own vectors)   ─┘  vectors are
                                                                    searchable
                                                                    immediately
ds.search(...)                       ─┐
ds.videos() / ds.info(id)             ├─ read back, any time, any order
ds.read_joints_pose(id) / read_subtasks(id) ─┘
```

Rules the order implies:

- `create` refuses an existing dataset/name (409 on the platform) and `open`
  refuses a missing one — no silent get-or-create. Want it? `try open /
  except create` (the example does).
- `add_video` appends; nothing is overwritten. Search vectors are also
  append-only and deduplicate by content.
- **There is no index-build step.** Vector indexes are maintained on append —
  `search()` works the moment `embed_videos`/`add_search_vectors` returns,
  and finds whatever exists so far.
- Platform credentials live ~12 h per handle; for longer sessions call
  `Dataset.open(name)` again.

## Function reference

### `Dataset.create(name=None, *, backend=None, visibility=None) -> Dataset`

| in | format | notes |
| --- | --- | --- |
| `name` | `[a-z0-9][a-z0-9._-]{0,63}` | platform mode: catalog row + platform bucket. Needs auth |
| `backend` | `file:///abs/path` or `https://…` S3 URL | self-hosted mode; `name` unused |
| `visibility` | `"private"` (default) / `"public"` | platform only |

**Raises** `DatasetError`: name and backend both missing; name already exists
(→ use `open`); a space already exists at `backend`.

The dataset's schemaType (`robot.video/v2`) is written to the platform
catalog **and** into the space itself, so viewers and `open()` can dispatch
on it even over a bare `file://` backend.

### `Dataset.open(name=None, *, backend=None) -> Dataset`

Same arguments. **Raises** `DatasetError` when missing, and when the target
space carries a *different* schemaType (a custom `dreamlake.db` store is not
interpretable as a robot dataset).

### `ds.add_video(video, *, video_id=None, joints_pose=None, subtasks=None, task=None, scene=None, gid=None, preview_height=720, preview_fps=30, frag_seconds=2.0, raw=True) -> dict`

Unchanged from v1 — see the table below for the annotation dict shapes.
Returns `{video_id, gid, anchor, video_preview: {fragments}, video_raw:
{...}|None, joints_pose?: {annotated_frames}, subtasks?: {segments}, meta}`.

Pre-transcode refusals (all `DatasetError`, all before any encoding time is
spent): video ≥ 3600 s; duplicate `video_id`; occupied `gid`; annotation
`src_fps` disagreeing with the video by >10 accumulated frames; **aspect
ratio differing from the dataset's** (one dataset, one camera geometry).
Warns and continues: 1–10 frames of fps drift; archival track skipped on a
codec-config mismatch (lossless copies cannot be normalized).

### `ds.embed_videos(video_id=None, *, fps=1.0, video_path=None, batch_size=32) -> dict`

Encode + upload in one step: samples frames at `fps` from the SOURCE file
(found via the `source_uri` recorded at ingest; pass `video_path=` if the
dataset changed machines), encodes them with CLIP, encodes each subtask
segment's text with BGE, uploads all vectors. One video per commit —
interrupt and re-run freely, uploads deduplicate.

| in | format |
| --- | --- |
| `video_id` | one video, or `None` = every video |
| `fps` | frame sampling rate (1.0 = one frame per second) |
| `video_path` | source file override (only with a specific `video_id`) |

**Returns** `{video_id: {"frame_vecs": N, "subtask_vecs": M}}`. **Raises**
`DatasetError` when the `[search]` extra is missing. Warns and skips frame
vectors when the source file cannot be found (subtask vectors still upload).

### `ds.add_search_vectors(video_id, *, frame_vecs=None, subtask_vecs=None) -> dict`

The pure-upload half, for pipelines that run their own encoders (see
`dreamlake.encoders` for the matching models — vectors must be CLIP-space
512-d for frames and BGE-space 384-d for subtask text, L2-normalized).

| in | format |
| --- | --- |
| `frame_vecs` | `[(t_sec: float, vec512)]` — time on the video's own clock |
| `subtask_vecs` | `[(t_sec, vec384, label: str)]` — anchored at segment start; `label` is shown on hits |

### `ds.search(query, top_k=10, kind="both") -> list[dict]`

| in | format |
| --- | --- |
| `query` | natural language, English (the models are English CLIP/BGE) |
| `kind` | `"frames"` (visual), `"subtasks"` (labels), `"both"` (fused) |

**Returns**, sorted by fused score:

```python
[{"video_id": "713488", "time_sec": 464.5, "score": 0.0164,
  "source": "subtask",                     # or "frame"
  "subtask": "screw shelf to brackets"},   # present on subtask hits
 ...]
```

First call loads the encoder models (a few seconds); warm queries are
sub-second. Recall is exact at small scale (an automatic exact-scan fallback
kicks in when the ANN index under-returns) and ANN-served as the dataset
grows.

### `ds.videos() / ds.info(video_id) / ds.read_joints_pose(video_id) / ds.read_subtasks(video_id)`

Unchanged from v1: catalog listing (one cheap column read), per-video
summary, and byte-exact annotation read-back.

## Annotation formats

Wire-compatible with the web viewer's overlay renderers — upload these
shapes and the skeleton/captions draw with no conversion.

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
  "task": "wash the dishes",           // becomes the video's default task label
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
(`dreamlake dataset ls|info|add-video`) — same schema, verified
byte-compatible.

## Constraints worth knowing up front

- **≤ 3600 s per video** (one-hour timeline slot per video).
- **One aspect ratio per dataset** (shared playback track, one frame
  geometry).
- **Archival track is best-effort on mixed sources** (lossless copies keep
  their codec config; the track holds exactly one).
- **Search fields exist only on v2 datasets.** Datasets created before v2
  must be re-created to become searchable (embedding fields cannot be
  retrofitted).
- **Append-only.** Nothing is rewritten; every published state stays
  addressable.
