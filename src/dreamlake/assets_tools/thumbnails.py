"""Offscreen MJCF thumbnail rendering with gallery-style auto-framing.

A port of the mujoco_menagerie ``generate_gallery.py`` approach:

* white gradient skybox + strong headlight, model lights deleted
  (unless ``keep_lights``) so every thumbnail shares one look;
* pose from the ``gallery_thumbnail`` keyframe if the model has one
  (or an injected ``qpos``), else keyframe 0, else the reset pose;
* camera auto-placed to frame the posed AABB of the visible geoms
  (``geom_group != 3``) from a caller-chosen azimuth/elevation, with
  ``AUTO_FOVY``/``AUTO_PADDING`` margins -- every AABB corner is kept
  inside the perspective frustum;
* transparent background via a segmentation-render alpha mask
  (chroma-keying the white skybox would eat white robot parts);
* output through :func:`save_thumbnail`: rendered at 2x the requested
  size (1280 for the default 640), LANCZOS-downscaled, and saved as
  lossy WebP (alpha preserved) -- 640px WebP thumbnails run tens of KB
  where 512px PNGs ran 200-400KB, and library grids hold hundreds of
  them.

``mujoco`` is an optional dependency (the ``compose`` extra):
:func:`render_thumbnail` warns and returns ``False`` without it, and on
any compile/render failure -- one bad model must never crash a whole
library import.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

AUTO_FOVY = 45
#: padding around the projected model AABB; 1.0 = model touches the
#: frame edge, >1 leaves margin
AUTO_PADDING = 1.08

DEFAULT_AZIMUTH = 70.0
DEFAULT_ELEVATION = 25.0

#: max(width, height) of a saved thumbnail; smaller inputs stay as-is
THUMBNAIL_MAX_DIM = 640
#: lossy WebP settings shared by every thumbnail writer: quality 82 at
#: the slowest/best encoding effort keeps grid thumbnails small
WEBP_QUALITY = 82
WEBP_METHOD = 6

#: (azimuth_deg, elevation_deg) per asset category. Azimuth is measured
#: from +X around +Z. Arms and end-effectors in Menagerie typically
#: mount facing +Y (view from ~70 deg = front-right); legged robots
#: face +X (view from ~-30 deg).
VIEW_ANGLES: dict[str, tuple[float, float]] = {
    "arm": (70, 25),
    "dual_arm": (70, 25),
    # End-effectors look bad from the side -- fingers extend roughly
    # horizontally, so a high elevation looks down at the digit spread.
    "end_effector": (45, 55),
    "mobile_manipulator": (15, 25),
    "mobile_base": (-30, 35),
    "quadruped": (-30, 25),
    "biped": (-30, 25),
    "humanoid": (15, 25),
    "drone": (110, 30),
    "biomechanical": (110, 25),
    "misc": (80, 25),
    "object": (DEFAULT_AZIMUTH, DEFAULT_ELEVATION),
}

_CORNER_SIGNS = np.array(
    np.meshgrid([-1, 1], [-1, 1], [-1, 1])).T.reshape(-1, 3)


def mujoco_available() -> bool:
    """Whether the optional ``mujoco`` dependency is importable."""
    try:
        import mujoco  # noqa: F401
    except ImportError:
        return False
    return True


def view_angles(category: str | None) -> tuple[float, float]:
    """The (azimuth, elevation) for a category, defaulting sensibly."""
    if category and category in VIEW_ANGLES:
        return VIEW_ANGLES[category]
    return (DEFAULT_AZIMUTH, DEFAULT_ELEVATION)


def save_thumbnail(
    image: Image.Image,
    out_path: str | Path,
    max_dim: int = THUMBNAIL_MAX_DIM,
) -> None:
    """Downscale ``image`` and save it as WebP at ``out_path``.

    The one thumbnail writer: offscreen renders and shipped upstream
    previews both funnel through here so every library thumbnail comes
    out the same shape. Resizes so ``max(width, height) == max_dim``
    (never upscales) with LANCZOS through premultiplied alpha
    (``RGBa``), so the black of fully-transparent pixels cannot bleed
    dark fringes into edges; saves lossy WebP (:data:`WEBP_QUALITY`,
    ``method=6``) with the alpha channel preserved. Creates parent
    directories.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA")
    scale = max_dim / max(image.size)
    if scale < 1.0:
        new_size = (
            max(1, round(image.width * scale)),
            max(1, round(image.height * scale)),
        )
        if image.mode == "RGBA":
            image = image.convert("RGBa").resize(
                new_size, Image.Resampling.LANCZOS).convert("RGBA")
        else:
            image = image.resize(new_size, Image.Resampling.LANCZOS)
    image.save(
        out_path, format="WEBP", quality=WEBP_QUALITY, method=WEBP_METHOD)


def _parse_floats(value: Any) -> list[float]:
    if isinstance(value, str):
        return [float(t) for t in value.split()]
    return [float(t) for t in value]


def _posed_bounds(mujoco, model, data):
    """World-frame AABB of visible geoms in the forward-evaluated pose.

    Plane geoms are excluded from FRAMING (they still render): their
    AABB is effectively infinite, so a scene entry point with a floor
    plane would push the auto-camera out beyond the clipping planes and
    produce a blank render.
    """
    plane = model.geom_type == mujoco.mjtGeom.mjGEOM_PLANE
    visible = np.where((model.geom_group != 3) & ~plane)[0]
    if visible.size == 0:  # collision-only model: frame everything
        visible = np.where(~plane)[0]
    if visible.size == 0:  # nothing but planes: frame them anyway
        visible = np.arange(model.ngeom)
    if visible.size == 0:
        raise ValueError("model has no geoms to frame")
    aabb = model.geom_aabb[visible]  # (n, 6): center_xyz + halfsize_xyz
    c_local = aabb[:, :3]
    h_local = aabb[:, 3:]
    corners_local = (
        c_local[:, None, :] + h_local[:, None, :] * _CORNER_SIGNS
    )  # (n, 8, 3)
    rot = data.geom_xmat[visible].reshape(-1, 3, 3)
    trans = data.geom_xpos[visible]
    corners_world = (
        np.einsum("nij,nkj->nki", rot, corners_local) + trans[:, None, :]
    )
    pts = corners_world.reshape(-1, 3)
    return pts.min(axis=0), pts.max(axis=0)


def _auto_camera(lo, hi, azimuth, elevation, fovy, padding) -> dict:
    """Frame the AABB tightly from the given viewing direction."""
    az = math.radians(azimuth)
    el = math.radians(elevation)
    z_cam = np.array(
        [math.cos(el) * math.cos(az), math.cos(el) * math.sin(az),
         math.sin(el)]
    )
    x_cam = np.cross([0.0, 0.0, 1.0], z_cam)
    x_cam /= np.linalg.norm(x_cam)
    y_cam = np.cross(z_cam, x_cam)
    # Pick the smallest distance along z_cam such that all 8 AABB
    # corners fall inside the perspective frustum (corners closer to
    # the camera need more margin -- they project larger).
    center = (lo + hi) / 2
    corners = np.stack(np.meshgrid(*zip(lo, hi))).reshape(3, -1).T - center
    half_fov = math.radians(fovy / 2)
    depth = corners @ z_cam
    dist_x = (depth + np.abs(corners @ x_cam) / math.tan(half_fov)).max()
    dist_y = (depth + np.abs(corners @ y_cam) / math.tan(half_fov)).max()
    dist = max(dist_x, dist_y) * padding
    pos = center + z_cam * dist
    return {
        "pos": pos.tolist(),
        "xyaxes": x_cam.tolist() + y_cam.tolist(),
        "fovy": fovy,
    }


def _apply_gallery_settings(mujoco, spec, size: int) -> None:
    """White-skybox look + offscreen buffer sized for the render."""
    spec.visual.quality.shadowsize = 8192
    spec.visual.headlight.diffuse = [0.6, 0.6, 0.6]
    spec.visual.headlight.ambient = [0.3, 0.3, 0.3]
    spec.visual.headlight.specular = [0.2, 0.2, 0.2]
    spec.visual.global_.offwidth = max(size, spec.visual.global_.offwidth)
    spec.visual.global_.offheight = max(size, spec.visual.global_.offheight)
    spec.add_texture(
        name="gallery_skybox",
        type=mujoco.mjtTexture.mjTEXTURE_SKYBOX,
        builtin=mujoco.mjtBuiltin.mjBUILTIN_GRADIENT,
        height=512,
        width=512,
        rgb1=[1, 1, 1],
        rgb2=[1, 1, 1],
    )


GALLERY_KEYFRAME = "gallery_thumbnail"


def _reset_pose(mujoco, model, data) -> None:
    """gallery_thumbnail keyframe if present, else keyframe 0, else reset."""
    key_id = mujoco.mj_name2id(
        model, mujoco.mjtObj.mjOBJ_KEY, GALLERY_KEYFRAME)
    if key_id >= 0:
        mujoco.mj_resetDataKeyframe(model, data, key_id)
    elif model.nkey > 0:
        mujoco.mj_resetDataKeyframe(model, data, 0)
    else:
        mujoco.mj_resetData(model, data)
    mujoco.mj_forward(model, data)


def render_thumbnail(
    xml_path: str | Path,
    out_path: str | Path,
    size: int = THUMBNAIL_MAX_DIM,
    azimuth: float = DEFAULT_AZIMUTH,
    elevation: float = DEFAULT_ELEVATION,
    *,
    fovy: float = AUTO_FOVY,
    padding: float = AUTO_PADDING,
    qpos: str | list[float] | None = None,
    camera: dict | None = None,
    keep_lights: bool = False,
    transparent: bool = True,
    spec_hook: Callable[[Any], None] | None = None,
) -> bool:
    """Render ``xml_path`` to a WebP at ``out_path``; ``False`` on failure.

    Renders offscreen at ``2 * size`` and downscales through
    :func:`save_thumbnail` -- a MuJoCo render costs the same order
    either way, and supersampling beats MSAA-only edges at grid size.
    ``out_path`` should end in ``.webp`` (the bytes are WebP whatever
    the suffix says).

    ``qpos`` injects a ``gallery_thumbnail`` keyframe (the pose to
    render); a keyframe of that name already in the model wins anyway.
    ``camera`` is the manual escape hatch -- a dict of ``pos``,
    ``xyaxes`` (space-separated strings or float lists) and optional
    ``fovy`` that replaces auto-framing entirely. ``spec_hook`` runs on
    the loaded ``MjSpec`` before compilation for per-model surgery.
    Never raises for a bad model: importers call this in a loop and one
    broken XML must not kill the run.
    """
    try:
        import mujoco
    except ImportError:
        warnings.warn(
            "mujoco is not installed; skipping thumbnail render "
            "(pip install 'dreamlake[compose]')",
            stacklevel=2,
        )
        return False

    xml_path = Path(xml_path)
    out_path = Path(out_path)
    render_size = size * 2
    renderer = None
    try:
        # Absolute path so the XML's own directory resolves nested
        # includes and per-model meshdir (chdir-based loading collides
        # mesh caches across models sharing asset filenames).
        spec = mujoco.MjSpec.from_file(str(xml_path.resolve()))
        _apply_gallery_settings(mujoco, spec, render_size)
        if not keep_lights:
            for light in list(spec.lights):
                spec.delete(light)
        if qpos is not None and not any(
                k.name == GALLERY_KEYFRAME for k in spec.keys):
            spec.add_key(name=GALLERY_KEYFRAME, qpos=_parse_floats(qpos))
        if spec_hook is not None:
            spec_hook(spec)

        if camera is not None:
            camera_kwargs = dict(camera)
            camera_kwargs["pos"] = _parse_floats(camera_kwargs["pos"])
            camera_kwargs["xyaxes"] = _parse_floats(camera_kwargs["xyaxes"])
            camera_kwargs.setdefault("fovy", fovy)
        else:
            # Compile once for the posed geometry, then place the camera.
            probe_model = spec.compile()
            probe_data = mujoco.MjData(probe_model)
            _reset_pose(mujoco, probe_model, probe_data)
            lo, hi = _posed_bounds(mujoco, probe_model, probe_data)
            camera_kwargs = _auto_camera(
                lo, hi, azimuth, elevation, fovy, padding)
        spec.worldbody.add_camera(name="__thumbnail", **camera_kwargs)

        model = spec.compile()
        data = mujoco.MjData(model)
        _reset_pose(mujoco, model, data)

        renderer = mujoco.Renderer(
            model, height=render_size, width=render_size)
        renderer.update_scene(data, camera="__thumbnail")
        img = renderer.render()
        # Alpha from a segmentation render, so background pixels go
        # transparent without nuking any geom.
        renderer.enable_segmentation_rendering()
        renderer.update_scene(data, camera="__thumbnail")
        mask = renderer.render()[..., 0] != -1
        renderer.disable_segmentation_rendering()

        if transparent:
            pixels = np.zeros((render_size, render_size, 4), dtype=np.uint8)
            pixels[mask, :3] = img[mask]
            pixels[mask, 3] = 255
        else:
            pixels = np.full((render_size, render_size, 3), 255,
                             dtype=np.uint8)
            pixels[mask] = img[mask]
        save_thumbnail(Image.fromarray(pixels), out_path, max_dim=size)
        return True
    except Exception as e:
        warnings.warn(
            f"thumbnail render failed for {xml_path}: "
            f"{type(e).__name__}: {e}",
            stacklevel=2,
        )
        return False
    finally:
        if renderer is not None:
            renderer.close()
