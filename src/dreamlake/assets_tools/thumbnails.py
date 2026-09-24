"""Offscreen MJCF thumbnail rendering with gallery-style auto-framing.

A port of the mujoco_menagerie ``generate_gallery.py`` approach, plus
meshy.ai-style model staging:

* studio three-point lighting (camera-relative key/fill/rim
  directionals, weak headlight, moderate ambient), model lights
  deleted (unless ``keep_lights``) so every thumbnail shares one look;
  MuJoCo cast shadows stay off -- grounding comes from the baked
  contact shadow below;
* pose from the ``gallery_thumbnail`` keyframe if the model has one
  (or an injected ``qpos``), else keyframe 0, else the reset pose;
* camera auto-placed to frame the posed AABB of the visible geoms
  (``geom_group != 3``) from a caller-chosen azimuth/elevation, with
  ``AUTO_FOVY``/``AUTO_PADDING`` margins -- every AABB corner is kept
  inside the perspective frustum;
* transparent background via a segmentation-render alpha mask
  (chroma-keying the white skybox would eat white robot parts);
* :func:`stage_thumbnail` (render path only): crop to the alpha bbox,
  recenter on a square canvas at a consistent object scale, and bake a
  soft elliptical contact shadow -- as ALPHA, under the object -- so
  the frontend can draw any theme backdrop behind it and the model
  still reads as grounded;
* output through :func:`save_thumbnail`: rendered at 2x the requested
  size (1280 for the default 640), LANCZOS-downscaled, and saved as
  lossy WebP (alpha preserved) -- 640px WebP thumbnails run tens of KB
  where 512px PNGs ran 200-400KB, and library grids hold hundreds of
  them. User-provided preview images flow through the same writer with
  ``stage=False``: photos with real backgrounds must never grow a fake
  shadow.

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
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

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

# ─── staging (meshy.ai-style model presentation) ─────────────────────
#: object width as a fraction of the square staged canvas
STAGE_OBJECT_FRAC = 0.78
#: canvas fraction kept clear UNDER the object -- room for the shadow
STAGE_BOTTOM_FRAC = 0.13
#: canvas fraction kept clear above the object
STAGE_TOP_FRAC = 0.055
#: contact-shadow peak opacity, measured AFTER the blur (0..1)
STAGE_SHADOW_OPACITY = 0.36
#: fraction of the object height (from the bottom) whose alpha counts
#: as the ground footprint -- wide enough to catch all of a
#: quadruped's feet, not just the lowest one
STAGE_FOOT_BAND = 0.16
#: shadow ellipse width relative to the object's ground footprint
STAGE_SHADOW_SPREAD = 1.18
#: shadow ellipse height relative to its width (flat = grounded)
STAGE_SHADOW_FLATTEN = 0.19
#: gaussian blur sigma for the shadow, relative to the object width --
#: capped relative to the SHADOW width so an arm with a small base
#: keeps a defined shadow instead of a wash
STAGE_SHADOW_BLUR = 0.10
STAGE_SHADOW_BLUR_CAP = 0.22
#: subtle grade applied to the object layer while staging
STAGE_CONTRAST = 1.04
STAGE_COLOR = 1.05

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


def _merge_alpha(rgb: Image.Image, alpha: Image.Image) -> Image.Image:
    """RGB channels of ``rgb`` + the given alpha channel, as RGBA."""
    return Image.merge("RGBA", (*rgb.split()[:3], alpha))


def stage_thumbnail(image: Image.Image) -> Image.Image:
    """Stage a transparent render: recenter + bake a contact shadow.

    Meshy.ai-style presentation for the render path, all in alpha so
    the frontend keeps drawing its own theme-aware backdrop:

    * crop to the alpha bounding box (small margin) so every asset
      lands at the same scale regardless of how the camera framed it;
    * place on a square canvas -- object :data:`STAGE_OBJECT_FRAC`
      of the width, bottom anchored on a shared ground line with
      :data:`STAGE_BOTTOM_FRAC` of the canvas left underneath;
    * bake a soft elliptical contact shadow under the object's ground
      footprint (the bottom ~10% of its alpha, projected to a width):
      gaussian-blurred, normalized to :data:`STAGE_SHADOW_OPACITY`
      peak opacity, composited BENEATH the object layer;
    * grade the object slightly (:data:`STAGE_CONTRAST`,
      :data:`STAGE_COLOR`) -- alpha untouched.

    Runs before the downscale in :func:`save_thumbnail`; images with
    no alpha coverage (or no alpha at all) pass through unstaged.
    """
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    bbox = image.getchannel("A").getbbox()
    if bbox is None:  # fully transparent: nothing to stage
        return image

    margin = max(2, round(0.01 * max(image.size)))
    obj = image.crop((
        max(0, bbox[0] - margin), max(0, bbox[1] - margin),
        min(image.width, bbox[2] + margin),
        min(image.height, bbox[3] + margin),
    ))
    ow, oh = obj.size

    side = max(
        round(ow / STAGE_OBJECT_FRAC),
        round(oh / (1.0 - STAGE_TOP_FRAC - STAGE_BOTTOM_FRAC)),
    )
    ox = (side - ow) // 2
    oy = side - round(STAGE_BOTTOM_FRAC * side) - oh  # shared ground line

    # Ground footprint: the columns the bottom ~10% of the object's
    # alpha actually covers -- a robot arm gets its shadow under the
    # base, not under the reach of its elbow.
    alpha_arr = np.asarray(obj.getchannel("A"), dtype=np.uint8)
    band = alpha_arr[int(oh * (1.0 - STAGE_FOOT_BAND)):, :]
    cols = np.flatnonzero(band.max(axis=0) > 32)
    if cols.size:
        foot_lo, foot_hi = int(cols[0]), int(cols[-1]) + 1
    else:  # nothing near the bottom edge (concave base): whole width
        foot_lo, foot_hi = 0, ow
    foot_w = max(foot_hi - foot_lo, round(0.25 * ow))
    foot_cx = ox + (foot_lo + foot_hi) / 2

    # Contact shadow: flat ellipse just under the object bottom,
    # blurred, then normalized so the post-blur PEAK sits exactly at
    # STAGE_SHADOW_OPACITY (blur alone would leave the peak dependent
    # on the footprint's aspect).
    sw = foot_w * STAGE_SHADOW_SPREAD
    sh = max(4.0, sw * STAGE_SHADOW_FLATTEN)
    cy = oy + oh - 0.15 * sh  # tucked just under the ground line
    shadow = Image.new("L", (side, side), 0)
    ImageDraw.Draw(shadow).ellipse(
        (foot_cx - sw / 2, cy - sh / 2, foot_cx + sw / 2, cy + sh / 2),
        fill=255,
    )
    blur = max(3.0, min(ow * STAGE_SHADOW_BLUR, sw * STAGE_SHADOW_BLUR_CAP))
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    arr = np.asarray(shadow, dtype=np.float32)
    peak = float(arr.max())
    if peak > 0:
        arr = arr * (STAGE_SHADOW_OPACITY * 255.0 / peak)
    shadow = Image.fromarray(arr.astype(np.uint8), "L")

    # Subtle grade on the object only (RGB channels; alpha untouched).
    rgb = obj.convert("RGB")
    rgb = ImageEnhance.Contrast(rgb).enhance(STAGE_CONTRAST)
    rgb = ImageEnhance.Color(rgb).enhance(STAGE_COLOR)
    obj = _merge_alpha(rgb, obj.getchannel("A"))

    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.putalpha(shadow)  # black shadow layer, alpha-only
    canvas.alpha_composite(obj, (ox, oy))
    return canvas


def save_thumbnail(
    image: Image.Image,
    out_path: str | Path,
    max_dim: int = THUMBNAIL_MAX_DIM,
    *,
    stage: bool = False,
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

    ``stage=True`` runs :func:`stage_thumbnail` before the downscale
    and a gentle unsharp mask after it. ONLY the offscreen render path
    sets it: user-provided preview images may be photos with real
    backgrounds, and must never grow a fake contact shadow.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA")
    if stage and image.mode == "RGBA":
        image = stage_thumbnail(image)
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
    if stage and image.mode == "RGBA":
        # crisp at grid size; RGB only -- sharpening alpha would put a
        # ring on the soft shadow edge
        rgb = image.convert("RGB").filter(
            ImageFilter.UnsharpMask(radius=1.4, percent=65, threshold=2))
        image = _merge_alpha(rgb, image.getchannel("A"))
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


#: studio lights, camera-relative: (azimuth offset from the camera in
#: degrees -- negative = screen-left, elevation degrees, diffuse,
#: specular). Key models the form from upper-front-left, fill lifts
#: the right side, rim separates the silhouette from behind.
STUDIO_LIGHTS = (
    ("key", -35.0, 52.0, 0.85, 0.22),
    ("fill", 55.0, 18.0, 0.32, 0.04),
    ("rim", 165.0, 40.0, 0.22, 0.10),
)
#: weak headlight + moderate ambient under the studio rig
STUDIO_HEADLIGHT_DIFFUSE = 0.20
STUDIO_HEADLIGHT_AMBIENT = 0.28
STUDIO_HEADLIGHT_SPECULAR = 0.08


def _apply_gallery_settings(mujoco, spec, size: int) -> None:
    """White-skybox look + offscreen buffer sized for the render."""
    spec.visual.headlight.diffuse = [STUDIO_HEADLIGHT_DIFFUSE] * 3
    spec.visual.headlight.ambient = [STUDIO_HEADLIGHT_AMBIENT] * 3
    spec.visual.headlight.specular = [STUDIO_HEADLIGHT_SPECULAR] * 3
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


def _add_studio_lights(mujoco, spec, camera_azimuth: float) -> None:
    """Three directional lights around the camera, cast shadows OFF.

    The rig follows the camera azimuth so "upper-front-left" means the
    same thing for a quadruped shot from -30 deg and an arm shot from
    70 deg. MuJoCo shadows stay disabled -- grounding comes from the
    baked contact shadow in :func:`stage_thumbnail`, and hard renderer
    shadows would fight it.
    """
    for name, d_az, elevation, diffuse, specular in STUDIO_LIGHTS:
        az = math.radians(camera_azimuth + d_az)
        el = math.radians(elevation)
        toward = np.array([
            math.cos(el) * math.cos(az),
            math.cos(el) * math.sin(az),
            math.sin(el),
        ])
        spec.worldbody.add_light(
            name=f"__studio_{name}",
            type=mujoco.mjtLightType.mjLIGHT_DIRECTIONAL,
            castshadow=False,
            pos=(toward * 3.0).tolist(),
            dir=(-toward).tolist(),
            diffuse=[diffuse] * 3,
            specular=[specular] * 3,
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

        if not keep_lights:
            # studio rig oriented to the ACTUAL camera (manual
            # ``camera`` dicts included): viewing azimuth from the
            # camera frame's z axis (x cross y points at the camera)
            x_cam = np.array(camera_kwargs["xyaxes"][:3], dtype=float)
            y_cam = np.array(camera_kwargs["xyaxes"][3:], dtype=float)
            z_cam = np.cross(x_cam, y_cam)
            cam_azimuth = math.degrees(math.atan2(z_cam[1], z_cam[0]))
            _add_studio_lights(mujoco, spec, cam_azimuth)

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
        # staging needs the alpha mask; opaque renders skip it
        save_thumbnail(
            Image.fromarray(pixels), out_path, max_dim=size,
            stage=transparent)
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
