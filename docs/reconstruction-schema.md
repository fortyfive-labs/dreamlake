# Reconstruction extension for `video.annotation` — v1

3D hand–object reconstruction stored **on a `video.annotation` episode**, as a
third annotation modality alongside `subtasks` (action segments) and
`joints_pose__<cam>` (2D keypoints). It is **additive and optional** — existing
`video.annotation/v1`+`/v2` datasets are unaffected, and a viewer renders the 3D
only when the fields are present ("presence is binding", exactly like
`joints_pose__<cam>`). **No schemaType bump.**

The three modalities are siblings on one episode — none nests under another:

```
episode
├── video_preview__<cam>      the source video (projection backdrop)
├── episode_meta              task/scene + per-camera {width,height,fps}
├── subtasks                  ── modality: action segments
├── joints_pose__<cam>        ── modality: 2D keypoints
└── recon_mesh                ┐
    recon_pose__<cam>         │ ── modality: 3D reconstruction
    recon_hands__<cam>        │    (richer data → a few sibling fields,
    recon_gravity__<cam>      │     all at the same level as the above)
    recon_camera__<cam>       ┘
```

## Space meta

| key | value |
|-----|-------|
| `dreamdb.dataset.recon` | `"v1"` — marks the dataset carries reconstruction; version for compat (dreamdb requires the `dreamdb.` prefix on meta keys) |

## Fields

One blob per episode — every field is read in a single fetch and indexed by
frame in memory (never per-frame items). All fields are JSON blobs
(`kind=image, mime=json`) **except `recon_hands__<cam>`**, which is a compact
**binary** blob (`kind=image, mime=bin` — magic `RHB1`): its per-frame vertex
arrays dominate the payload, so storing them as JSON decimal text was ~10x
larger and forced a `JSON.parse` of ~10^6 numbers on the reader. The SDK
encodes/decodes it transparently — the `payload` column below is the doc shape
`add_episode` accepts and `read_recon_hands` returns, not the on-disk bytes.
See **Binary encoding** below.

**Camera-independent (episode-level)**

| field | payload |
|-------|---------|
| `recon_mesh` | `{ "<object>": { "obj": "<.obj text>", "scale": <float> } }` |

**Per-camera (`__<cam>`, mirroring `joints_pose__<cam>`)** — the reconstruction is
expressed in one camera's frame, so its per-frame data is camera-scoped.

| field | payload |
|-------|---------|
| `recon_pose__<cam>` | `{ "frames": { "<f>": { "<object>": { "t": [x,y,z], "q": [w,x,y,z] } } } }` |
| `recon_hands__<cam>` | `{ "faces": { "left": [[a,b,c],…], "right": [[a,b,c],…] }, "frames": { "<f>": { "left": { "verts": [[x,y,z],…], "joints": [[x,y,z],…] }, "right": {…} } } }` |
| `recon_gravity__<cam>` | `{ "vec3d": [x, y, z] }` |
| `recon_camera__<cam>` | `{ "fx": …, "fy": …, "cx": …, "cy": …, "width": …, "height": … }` |

**Reused from `video.annotation` (not re-stored)**

- Projection backdrop video → `video_preview__<cam>`.
- fps / resolution → `episode_meta.cameras[<cam>]`.

## Conventions (the contract)

- All 3D lives in the camera's **OpenCV frame** (x-right / y-down / z-forward),
  in **metres**. There is no world frame.
- `t` is translation; `q` is a quaternion **wxyz**. `scale` is a uniform scale on
  the mesh vertices.
- Hands: `verts`/`joints` are 3D points in the camera frame; `joints[0]` is the
  wrist. **Sparse** — a missing side or frame is an absent key (no `valid` array).
- Camera: pinhole intrinsics at the **video resolution**; principal point is
  centred (`width == 2*cx`, `height == 2*cy`). No extrinsics in v1.
- Frame indices are **0-based** and aligned with the video (frame `f` ↔ time
  `f / fps`).
- **Colour is not stored** — the viewer assigns a palette by object order.

## Binary encoding (`recon_hands__<cam>`)

`recon_hands` is stored as a self-describing little-endian binary blob, not
JSON. The doc shape in **Fields** is what the SDK accepts and returns;
`encode_hands_binary` / `decode_hands_binary` (in `_schema.py`) convert to and
from these bytes on write / read. Layout:

```
magic "RHB1" | version u8 | quant u8 (1=uint16) | nSides u8
bbox_min f32[3] | bbox_max f32[3]                 # clip-wide, per-axis
per side:  sideId u8 (0=left,1=right) | nverts u16 | nFaces u32
           faces u32[nFaces*3]                    # topology, once
           nFrames u32
           per frame:  frameIdx u32
                       verts  u16[nverts*3]        # quantized (see below)
                       njoints u16 | joints f32[njoints*3]
```

- **Verts** are quantized to `uint16` per axis over the clip's bounding box:
  `u = round((v - bbox_min) / (bbox_max - bbox_min) * 65535)`; decode inverts
  it. Step ≈ `bbox_span / 65535` — sub-mm for a hand, visually lossless.
- **Joints** stay `f32` (only ~21 per hand — no size pressure). **Faces** are
  the constant MANO topology, stored once per side.
- Sparsity is carried by `frameIdx` (only annotated frames appear), matching
  the JSON form's absent-key convention.
- Result: ~10x smaller than the JSON text (e.g. 15 MB → ~1.5 MB per episode)
  and no `JSON.parse` of ~10^6 numbers on the reader — it fills typed arrays
  directly. Web viewer decoder mirrors this in `reconAnnotationSource.ts`.

## SDK

Reconstruction is a peer modality to `subtasks` / `joints_pose`: five flat
`recon_*` arguments on `add_episode` (at creation) and `revise` (later), one per
stored track. `recon_mesh` is episode-level; the other four are per-camera and
follow the `joints_pose` rule — a bare doc binds to the primary camera, or pass
`{camera: doc}`. Each is independent and optional; at least one must be present.

```python
# ① together with the episode (one atomic row: video + subtasks + joints + recon)
epo = ds.add_episode(
    video, subtasks=..., joints_pose=...,
    recon_mesh={name: obj_text},                       # → recon_mesh (episode-level)
    #   or {name: {"obj": obj_text, "scale": <float>}}   (bare string ⇒ scale 1.0)
    recon_pose={frame: {name: {"t": [...], "q": [...]}}},  # → recon_pose__<cam>
    recon_camera={"fx","fy","cx","cy"},                # → recon_camera__<cam> (intrinsics)
    #   width/height optional — default to round(2*cx) / round(2*cy)
    recon_hands={"faces": {...}, "frames": {...}},     # → recon_hands__<cam> (optional)
    recon_gravity=[x, y, z],                           # → recon_gravity__<cam> (optional)
)

# ② or add/revise pieces later; {camera: doc} targets a named camera
ds.episode(id).revise(recon_mesh={...})                          # only mesh now
ds.episode(id).revise(recon_pose={"left": ...}, recon_camera={"left": ...})  # a camera later

# read back — one method per piece (default = primary camera):
#   epo.read_recon_mesh()                 → {object: {obj, scale}} | None
#   epo.read_recon_pose(camera=None)      → {"frames": {...}} | None
#   epo.read_recon_camera(camera=None)    → {"fx","fy","cx","cy","width","height"} | None
#   epo.read_recon_hands(camera=None)     → {"faces","frames"} | None
#   epo.read_recon_gravity(camera=None)   → {"vec3d": [...]} | None
# any recon write also stamps the space meta `dreamdb.dataset.recon = "v1"`.
```

`recon_camera` is the pinhole **intrinsics** (its track is `recon_camera__<cam>`).
`recon_pose` distinguishes a bare per-frame doc from a `{camera: doc}` map by
whether the top-level keys are frame indices or camera names — so don't name a
camera a bare number.

## Frontend

`VideoAnnotationViewer` detects the `recon_*` fields and renders the 3D scene
(mesh at per-frame pose, MANO hands, source-video projection) as an added
section, reusing the reconstruction3d components. Absent fields → no 3D, the
annotation view is unchanged.
