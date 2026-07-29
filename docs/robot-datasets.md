# Robot Datasets

`dreamlake.dataset` stores annotated robot-training video: raw footage goes in
once, gets transcoded so any browser can stream and scrub it, and lives next
to your pipeline's per-frame joint annotations and action segments in one
versioned DreamDB space. The web viewer renders all of it — video, skeleton
overlay, subtask captions and timeline — with no extra export step.

The intended caller is the last stage of a labeling pipeline: you already
hold the video file and two Python dicts (joints, segments); one
`add_video()` call publishes them.

## Requirements

```bash
pip install dreamdb        # the DreamDB Python SDK (Rust native extension)
brew install ffmpeg        # transcoding; any ffmpeg ≥ 5 on PATH works
```

`backend` is any URI dreamdb accepts. This phase is local-first:
`file:///path` writes a directory that a static file server makes readable to
the browser. `s3://bucket/prefix` works with your own AWS credentials.
Server-brokered credentials come later and change only this string.

## Quickstart

```python
from dreamlake.dataset import Dataset

ds = Dataset.create(backend="file:///data/datasets/wash-the-dishes")

# ... your pipeline produces `joints` and `segments` dicts per video ...

ds.add_video(
    "captures/Ceramics.mov",
    video_id="Ceramics",
    joints_pose=joints,      # dict, or a path to a JSON file
    subtasks=segments,       # dict, or a path to a JSON file
)

for row in ds.videos():
    print(row["gid"], row["video_id"], row["duration_s"])
```

Runnable version with a self-contained demo mode:
`docs/examples/09_robot_dataset.py`.

## Calling order

```
Dataset.create(backend)          once per dataset   ─┐
Dataset.open(backend)            every later session ─┴─→  ds

ds.add_video(...)                once per video, any number of times

ds.videos()                      ─┐
ds.info(video_id)                 ├─ read back, any time, any order
ds.read_joints_pose(video_id)     │
ds.read_subtasks(video_id)       ─┘
```

Rules the order implies:

- `create` refuses an existing dataset and `open` refuses a missing one —
  there is no silent get-or-create, so a typo'd path cannot fork a second
  history. If you want get-or-create, do `try open / except create` (the
  example does).
- `add_video` calls append; nothing is ever overwritten. There is no
  `remove_video` in this phase.
- Reads and writes may interleave freely; every `add_video` is atomic-ish
  (video tracks land first, then one committed row with metadata +
  annotations).

## Function reference

### `Dataset.create(backend: str) -> Dataset`

Creates an empty dataset (five tracks, ref `main`).

| in | format |
| --- | --- |
| `backend` | URI: `file:///abs/path` or `s3://bucket/prefix` |

**Returns** a `Dataset` handle. **Raises** `DatasetError` if a dataset
already exists at `backend`.

### `Dataset.open(backend: str) -> Dataset`

Opens an existing dataset. Same argument; raises `DatasetError` when nothing
is there.

### `ds.add_video(video, *, video_id=None, joints_pose=None, subtasks=None, task=None, scene=None, gid=None, preview_height=720, preview_fps=30, frag_seconds=2.0, raw=True) -> dict`

Transcodes one video into the dataset together with its annotations.

| in | format | notes |
| --- | --- | --- |
| `video` | path (str/Path) | any codec/container — it is re-encoded for playback |
| `video_id` | str | stable id; default: filename stem. Must be unique in the dataset |
| `joints_pose` | dict **or** JSON path | shape below; optional |
| `subtasks` | dict **or** JSON path | shape below; optional |
| `task` | str | default: the subtasks dict's `task` |
| `scene` | str | free-form grouping label |
| `gid` | int ≥ 0 | anchor slot; default: next free. Only pass it to reproduce an exact layout |
| `preview_height` | int | playback-track height; keep it constant within a dataset |
| `preview_fps` | float | playback-track rate; keep it constant within a dataset |
| `frag_seconds` | float 1–30 | streaming fragment duration |
| `raw` | bool | also store a lossless archival copy (best-effort, see below) |

**Returns** a summary dict:

```python
{"video_id": ..., "gid": 0, "anchor": 0,
 "video_preview": {"fragments": 34, "ingest": {...}},
 "video_raw":     {"fragments": 34, "ingest": {...}} | None,   # None = skipped
 "joints_pose":   {"annotated_frames": 1707},                  # only if given
 "subtasks":      {"segments": 8},                             # only if given
 "meta": {...}}                                                # the stored video_meta row
```

**Raises** `DatasetError` before any transcoding when: the video is ≥ 3600 s
(each video owns a one-hour timeline slot); `video_id` already exists; the
requested `gid` is taken; the annotation's `src_fps` disagrees with the
video's by more than 10 frames of accumulated drift (they describe different
videos); or the video's **aspect ratio** differs from the dataset's (all
videos on the shared playback track must encode to one frame size — keep one
dataset per camera geometry).

**Warns** (`warnings.warn`) and continues when: fps drift is 1–10 frames, or
the archival track is skipped because this video's codec configuration
differs from the clips already stored (a lossless track cannot be
normalized; playback and annotations are unaffected).

### `ds.videos() -> list[dict]`

The dataset's catalog, one dict per video, sorted by slot. This is a single
column read — cheap at any dataset size.

```python
[{"gid": 0, "anchor": 0, "video_id": "Ceramics", "source_uri": ...,
  "task": "ceramics", "src_fps": 29.987, "width": 1920, "height": 1080,
  "total_frames": 2024, "duration_s": 67.495}, ...]
```

`gid` is the positional slot (an implementation detail of the timeline
layout); `video_id` is the stable name. Key your own bookkeeping off
`video_id`.

### `ds.info(video_id: str) -> dict`

One video's catalog row plus annotation summaries
(`{"joints_pose": {"annotated_frames": N}, "subtasks": {"segments": N,
"ends_at_sec": S}}` — keys present only when the annotation is). Accepts a
`video_id` or a slot number as a string. **Raises** `DatasetError` for an
unknown video, listing the ids that do exist.

### `ds.read_joints_pose(video_id) -> dict | None` / `ds.read_subtasks(video_id) -> dict | None`

The stored annotation documents, byte-exact as uploaded. `None` when that
video has no such annotation (normal, not an error).

## Annotation formats

Both are wire-compatible with the web viewer's overlay renderers — upload
these shapes and the skeleton/captions draw with no conversion.

### `joints_pose`

```jsonc
{
  "width": 1920,              // REQUIRED: pixel space of the coordinates —
  "height": 1080,             //   the ORIGINAL video's size, upright
  "src_fps": 29.987,          // REQUIRED: true frame rate the detector saw;
                              //   frame k renders at k / src_fps seconds
  "total_frames": 2024,       // optional
  "joint_order": ["wrist", "thumb_cmc", ...],   // names, index-aligned
  "bones": [[0, 1], [1, 2], ...],               // skeleton edges
  "frames": {                 // REQUIRED, SPARSE: only annotated frames
    "0": [                    // key = 0-based ORIGINAL-video frame index
      {"is_right": 1,                          // 0 left / 1 right (coloring)
       "det_conf": 0.9,                        // 0..1
       "bbox": [x1, y1, x2, y2],               // optional
       "keypoints_2d": [[x, y], ...]}          // REQUIRED, matches joint_order
    ]
  }
}
```

The skeleton is self-describing: `joint_order`/`bones` make the same track
carry a 21-joint hand, a full body, or a manipulator arm.

### `subtasks`

```jsonc
{
  "task": "wash the dishes",       // becomes the video's default task label
  "labeled_subtasks": [            // REQUIRED; gaps between segments are fine
    {"start_sec": 0.0,             // seconds on the video's own clock
     "end_sec": 2.5,               // exclusive
     "subtask": "pick up plate"}   // caption + timeline block label
  ]
}
```

## Visualizing

```bash
npx http-server /data/datasets/wash-the-dishes -p 8791 --cors
# with the dreamlake-ai app running (pnpm dev):
open 'http://localhost:3000/dataset-debug?space=http://localhost:8791/refs/main'
```

`--cors` matters only for browsers. The TypeScript CLI reads the same
datasets (`dreamlake dataset ls|info --backend file:///...`) and its
`dataset add-video` writes them interchangeably with this SDK — same schema,
same layout, verified byte-compatible.

## Constraints worth knowing up front

- **≤ 3600 s per video.** Each video owns a one-hour slot on the dataset's
  timeline; `add_video` refuses longer files rather than corrupt the layout.
- **One aspect ratio per dataset.** All videos share one playback track and
  it holds exactly one frame geometry.
- **The archival track is best-effort on mixed sources.** A lossless copy
  keeps the source's codec configuration, and the track only holds one — so
  footage from different rigs keeps playback but not a shared archive.
- **Append-only.** Datasets version like git: adding never rewrites, and the
  underlying store keeps every published state addressable.
