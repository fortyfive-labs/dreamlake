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

All JSON blobs (`kind=image, mime=json`), one blob per episode — every field is
read in a single fetch and indexed by frame in memory (never per-frame items).

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

## SDK

```python
epo.set_reconstruction(
    meshes={name: obj_text},                          # → recon_mesh (episode-level)
    #   or {name: {"obj": obj_text, "scale": <float>}}   (bare string ⇒ scale 1.0)
    poses={frame: {name: {"t": [...], "q": [...]}}},   # → recon_pose__<cam>
    intrinsics={"fx","fy","cx","cy"},                  # → recon_camera__<cam>
    #   width/height optional — default to round(2*cx) / round(2*cy)
    hands={"faces": {...}, "frames": {...}},           # → recon_hands__<cam> (optional)
    gravity=[x, y, z],                                 # → recon_gravity__<cam> (optional)
    camera="main",                                     # camera NAME (default: primary)
)
# read back: epo.read_reconstruction(camera=None)
#   → {"camera", "meshes", "poses", "camera_intrinsics", "hands"?, "gravity"?} | None
# also stamps the space meta `dreamdb.dataset.recon = "v1"`.
```

## Frontend

`VideoAnnotationViewer` detects the `recon_*` fields and renders the 3D scene
(mesh at per-frame pose, MANO hands, source-video projection) as an added
section, reusing the reconstruction3d components. Absent fields → no 3D, the
annotation view is unchanged.
