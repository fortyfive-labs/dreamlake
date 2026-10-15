# DreamLake Python SDK — Design Document

> Living document. Updated as design evolves.

> The `Video` object and `dl.load_video` were removed when BSS was retired:
> both read their metadata and chunk playlists from BSS routes (`/videos/:id/
> metadata`, `/videos/:id/stream/:hash.m3u8`) that BSS itself deleted when it
> unified everything into `/files`, so they answered 404 well before the
> retirement.

## Object

### TextTrack

#### Properties

```python
track.id             # "tt-Kx9mP2qR4tWn8vZy"
track.prefix         # "/2026/04/run-042/captions/llava"
track.project        # "robotics@alice"
track.count          # number of entries
```

#### Import

```python
track = dl.text_track(
    prefix="/2026/04/run-042/captions/llava",
    project="robotics@alice",
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

# Create a caption track
track = dl.text_track(
    prefix="/2026/04/run-042/captions/scene-description",
    project="robotics@alice",
)

# Caption each segment. Segments come from your own pipeline: the SDK's
# Video object was removed with BSS.
for chunk in my_segments:
    caption = describe(chunk[0].image)
    track.add(caption, source=chunk)

# Upload
track.flush()
```

#### Repr

```python
>>> track
TextTrack("/2026/04/run-042/captions/scene-description", project="robotics@alice", count=30)
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
# A resource id encodes its type; `decode_resource_id` reads it back.
# "v-BV1bW411n7fY9x01"  → ("video", "<objectId>")
# "tt-3fHj7kLm0pQs5uXw" → ("text-track", "<objectId>")
```

## Function


### dl.upload

```python
dl.upload("./video.mp4", project="robotics@alice", prefix="/2026/04/run-042/camera/front")
dl.upload("./captions.jsonl", project="robotics@alice", prefix="/2026/04/run-042/captions/llava")
dl.upload("./audio.wav", project="robotics@alice", prefix="/2026/04/run-042/mic/front")
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
dl.upload("./data.jsonl", project="...", prefix="...", type="caption-track")
```

### dl.text_track

```python
track = dl.text_track(
    prefix="/2026/04/run-042/captions/llava",
    project="robotics@alice",
)
```

Creates a `TextTrack`. Entries buffered until `.flush()`.

### dl.vec_index

```python
index = dl.vec_index("my-experiment")    # → VectorIndex
```

Creates or connects to a named `VectorIndex`. Lazy — collection created on first `.add()`.

## Prefix Context

Avoid repeating `project=` and `prefix=` on every call.

```python
with dl.Prefix(project="robotics@alice", prefix="/2026/04/run-042"):
    dl.upload("./video.mp4", path="/camera/front")
    track = dl.text_track(path="/captions/llava")
    index = dl.vec_index("my-experiment")
```

All paths inside the block are relative to the prefix. Nestable:

```python
with dl.Prefix(project="robotics@alice", prefix="/2026/04"):
    with dl.Prefix(prefix="run-042"):
        dl.upload("./video.mp4", path="/camera/front")
        # resolves to: /2026/04/run-042/camera/front
```

## Path Resolution

- **Relative** paths are appended to the current prefix
- **Absolute** paths (starting with `/`) are resolved from the project root, ignoring prefix

```python
with dl.Prefix(project="robotics@alice", prefix="/2026/04/run-042"):
    dl.upload("./a.mp4", path="camera/front")     # → /2026/04/run-042/camera/front
    dl.upload("./b.mp4", path="/shared/ref.mp4")   # → /shared/ref.mp4 (absolute)
```

## Example

```python
import dreamlake as dl
from my_model import enc_image, describe

with dl.Prefix(project="robotics@alice", prefix="/2026/04/run-042"):
    # Upload
    dl.upload("./video.mp4", path="camera/front")

    # Caption track
    track = dl.text_track(path="captions/scene-description")
    for chunk in my_segments:
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
