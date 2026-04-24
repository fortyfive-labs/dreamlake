# DreamLake Python SDK — Design Document

> Living document. Updated as design evolves.

## Object

### Video

#### Properties

```python
video.id             # "v-BV1bW411n7fY9x01"
video.uri            # "bss://localhost:10234/videos/69e7264a"
video.fps            # 30.0
video.st             # 0.0
video.et             # 60.0
video.duration       # 60.0
video.frames    # 1800
video.width          # 1920
video.height         # 1080
```

#### Import

```python
import dreamlake as dl

video = dl.load_video("v-BV1bW411n7fY9x01")

# Also accepts BSS URI
video = dl.load_video("bss://localhost:10234/videos/69e7264a")
```

#### Indexing

`float` → time (seconds). `int` → frame number. Always returns `Video` (lazy).

```python
video[0]              # single-frame Video (lazy)
video[42]             # frame 42 → Video
video[10.0:20.0]      # time slice → Video
video[300:600]        # frame slice → Video

video[0].image        # → PIL Image (triggers download)
video[0].numpy()      # → numpy (H, W, 3)
```

Indexing on a sliced Video is **relative** to the slice:

```python
clip = video[10.0:20.0]
clip[0]               # first frame of clip (at t=10.0), not frame 0 of video
clip[2.0:5.0]         # → Video(st=12.0, et=15.0)
```

#### Chunking

```python
clip = video[1.2:2.4]

# Split into 200ms segments
chunks = clip.chunk(0.200)                     # → [Video, Video, ...]

# Snap to nearest keyframe boundaries
chunks = clip.chunk(0.200, by_iframe=True)     # → [Video, Video, ...]
```

Returns a `VideoArray` — supports fancy indexing and batch tensor conversion.

```python
clips.tensor().shape          # (6, 240, 360, 3) — all chunks
clips[:, 0].tensor().shape    # (6, 240, 360, 3) — first frame of each chunk
clips[0].tensor().shape       # (240, 360, 3) — single chunk
clips[0:3].tensor().shape     # (3, 240, 360, 3) — first 3 chunks

# Feed directly to model
emb_vec = img_enc(clips[:, 0].tensor().to('cuda'))
```

#### Data Access

```python
video.thumbnail       # middle frame → PIL Image
video.iterator()        # iterator of frames
video.numpy()         # → numpy (N, H, W, 3)
video.tensor()        # → torch (N, C, H, W)
```

#### Slicing returns Video

A sliced video is still a Video — same interface, narrower range.

```python
clip = video[10.0:20.0]

clip.st               # 10.0
clip.et               # 20.0
clip.duration         # 10.0
clip.fps              # 30.0
clip.thumbnail        # middle frame
clip[0]               # first frame of this range (lazy)
clip[0].image         # PIL Image (download)
clip[2.0:5.0]         # sub-slice → Video(st=12.0, et=15.0)
```

#### Repr

```python
>>> video
Video("bss://...69e7264a", fps=30.0, duration=60.0, 1920x1080)

>>> video[10.0:20.0]
Video("bss://...69e7264a", st=10.0, et=20.0, duration=10.0)
```

### TextTrack

#### Properties

```python
track.id             # "tt-Kx9mP2qR4tWn8vZy"
track.prefix         # "/2026/04/run-042/captions/llava"
track.space          # "robotics@alice"
track.count          # number of entries
```

#### Import

```python
track = dl.text_track(
    prefix="/2026/04/run-042/captions/llava",
    space="robotics@alice",
)
```

#### Add Entry

```python
# From a video clip — st, et, sf, ef inferred from source
clip = video[10.0:20.0]
track.add("A robot arm reaches for a cup", source=clip)

# From raw timestamps
track.add("A robot arm reaches for a cup", st=10.0, et=20.0)

# With frame range
track.add("Gripper closes", st=20.0, et=22.0, sf=600, ef=660)
```

#### Flush

```python
track.flush()    # writes JSONL → uploads to BSS → triggers Lambda split
```

Entries are buffered in memory until `.flush()`.

#### Example

```python
import dreamlake as dl
from my_model import describe

video = dl.load_video("v-BV1bW411n7fY9x01")

# Create a caption track
track = dl.text_track(
    prefix="/2026/04/run-042/captions/scene-description",
    space="robotics@alice",
)

# Caption every 2s segment
chunks = video.chunk(2.0)
for chunk in chunks:
    caption = describe(chunk[0].image)
    track.add(caption, source=chunk)

# Upload
track.flush()
```

#### Repr

```python
>>> track
TextTrack("/2026/04/run-042/captions/scene-description", space="robotics@alice", count=30)
```

### VectorIndex

#### Properties

```python
index.name           # "my-experiment"
index.count          # number of vectors
index.dim            # vector dimension (set on first add)
```

#### Import

```python
index = dl.vec_index("my-experiment")
```

Lazy — collection created on first `.add()`.

#### Add

```python
clip = video[10.0:20.0]

index.add(
    vector=my_encoder(clip[0]),
    caption=my_vlm(clip[0]),
    source=clip,
)
```

`source` captures provenance automatically — videoId, st, et, fs, fe.

#### Search

```python
results = index.search("robot picking up cup", limit=10)
for r in results:
    r.score
    r.caption
    r.video              # → Video object (playable)
```

#### Repr

```python
>>> index
VectorIndex("my-experiment", count=1800, dim=768)
```

## Resource ID

Every resource has a globally unique ID: `{type_prefix}-{16 base62 chars}`.

| Prefix | Type |
|--------|------|
| `v-` | Video |
| `a-` | Audio |
| `i-` | Image |
| `lt-` | LabelTrack |
| `tt-` | TextTrack |

Derived dynamically from the underlying ObjectId. Not stored separately.

```python
video.id              # "v-BV1bW411n7fY9x01"
dl.load_video("v-BV1bW411n7fY9x01")
dl.load("v-BV1bW411n7fY9x01")       # generic — parses prefix
dl.load("tt-3fHj7kLm0pQs5uXw")      # → TextTrack
```

## Function

### dl.load_video

```python
video = dl.load_video("v-BV1bW411n7fY9x01")

# Also accepts BSS URI
video = dl.load_video("bss://localhost:10234/videos/69e7264a")
```

Returns a `Video`. Accepts resource ID or `bss://`, `file://`, `s3://` URI.

### dl.upload

```python
dl.upload("./video.mp4", space="robotics@alice", prefix="/2026/04/run-042/camera/front")
dl.upload("./captions.jsonl", space="robotics@alice", prefix="/2026/04/run-042/captions/llava")
dl.upload("./audio.wav", space="robotics@alice", prefix="/2026/04/run-042/mic/front")
```

Type auto-detected from extension. Chunked multipart upload with progress bar. Triggers Lambda processing (HLS split, etc.) after upload.

| Extension | Type |
|-----------|------|
| `.mp4` `.mkv` | video |
| `.wav` `.mp3` `.aac` | audio |
| `.jpg` `.png` `.webp` | image |
| `.jsonl` | label-track |
| `.vtt` `.srt` | text-track |

Override with `type=`:

```python
dl.upload("./data.jsonl", space="...", prefix="...", type="caption-track")
```

### dl.text_track

```python
track = dl.text_track(
    prefix="/2026/04/run-042/captions/llava",
    space="robotics@alice",
)
```

Creates a `TextTrack`. Entries buffered until `.flush()`.

### dl.vec_index

```python
index = dl.vec_index("my-experiment")    # → VectorIndex
```

Creates or connects to a named `VectorIndex`. Lazy — collection created on first `.add()`.

## Prefix Context

Avoid repeating `space=` and `prefix=` on every call.

```python
with dl.Prefix(space="robotics@alice", prefix="/2026/04/run-042"):
    dl.upload("./video.mp4", path="/camera/front")
    track = dl.text_track(path="/captions/llava")
    index = dl.vec_index("my-experiment")
```

All paths inside the block are relative to the prefix. Nestable:

```python
with dl.Prefix(space="robotics@alice", prefix="/2026/04"):
    with dl.Prefix(prefix="run-042"):
        dl.upload("./video.mp4", path="/camera/front")
        # resolves to: /2026/04/run-042/camera/front
```

## Path Resolution

- **Relative** paths are appended to the current prefix
- **Absolute** paths (starting with `/`) are resolved from the space root, ignoring prefix

```python
with dl.Prefix(space="robotics@alice", prefix="/2026/04/run-042"):
    dl.upload("./a.mp4", path="camera/front")     # → /2026/04/run-042/camera/front
    dl.upload("./b.mp4", path="/shared/ref.mp4")   # → /shared/ref.mp4 (absolute)
```

## Example

```python
import dreamlake as dl
from my_model import enc_image, describe

video = dl.load_video("v-BV1bW411n7fY9x01")

with dl.Prefix(space="robotics@alice", prefix="/2026/04/run-042"):
    # Upload
    dl.upload("./video.mp4", path="camera/front")

    # Caption track
    track = dl.text_track(path="captions/scene-description")
    chunks = video.chunk(2.0)
    for chunk in chunks:
        track.add(describe(chunk[0].image), source=chunk)
    track.flush()

    # Vector index
    index = dl.vec_index("my-experiment")
    for chunk in chunks:
        index.add(
            vector=enc_image(chunk[0]),
            caption=describe(chunk[0]),
            source=chunk,
        )
