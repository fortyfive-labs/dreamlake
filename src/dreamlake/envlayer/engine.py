"""Materialize a resolved layer stack into a self-contained env directory.

Ports the proven hand_teleop v1 composer (flat asset staging with clash
detection, mesh-name pinning + rewrite-before-attach, attach + mocap-weld
idiom, ``content_type`` strip, stale-file removal) and executes the five
v3 ops over one accumulated ``MjSpec``:

* ``Merge`` -- ``spec.attach(child, prefix="", frame=...)`` is the
  whole-spec union primitive (verified: it carries worldbody children,
  assets, defaults classes, actuators, sensors, tendons, equalities and
  contact pairs/excludes; keyframes are deferred and resurrected --
  correctly remapped -- at compile; it does NOT error on name collisions,
  which are pre-checked here).
  ``<option>/<visual>/<compiler>`` merge field-wise, later layer wins,
  restricted to the fields the layer's raw XML explicitly states (MjSpec
  fills defaults and erases the stated/defaulted distinction, so the
  stated set comes from ElementTree and the parsed values from the
  layer's own MjSpec).
* ``Attach`` -- stage child assets BEFORE attach, then graft the source's
  root body at ``world | body:<name> | site:<name>`` under the layer's
  ``key`` as name prefix (``site.attach_body`` makes the site's pose
  win). Joint modes: ``rigid`` (welded by construction; an imported
  freejoint is removed), ``free`` (freejoint), ``free-anchored``
  (freejoint + mocap anchor weld holding the qpos0 relative pose). URDF
  sources import natively via ``MjSpec.from_file``, with freejoint
  reconciliation and mesh-path normalization.
* ``Update`` -- the meta component: ``key`` addresses one element
  (``kind:name``, a bare unambiguous name, ``option``, or
  ``visual:<sub>``), every other prop is an MJCF attribute opinion. JSON
  values serialize to mujoco's XML conventions (space-separated floats
  for vectors, true/false booleans) and flow through the same strict
  matcher as ``Patch`` -- an unmatched address is an error. Unit-sensitive
  values (joint ``range`` etc.) are interpreted in the TARGET element's
  own compiler context -- the angle units its layer was authored in --
  because MjSpec stores authored values per element context (write
  radians against radian-authored robots, which is all of them in
  practice).
* ``Remove`` -- delete the addressed element and its subtree; the layer
  below is untouched, so dropping the line brings the element back.
* ``Patch`` -- sparse MJCF parsed as RAW XML (attribute presence = the
  opinion) from a file or env source; same strict semantics as
  ``Update``, for opinion sets big enough to be their own artifact.

Keyframes are stripped iff the stack contains an Attach layer (attach
changes ``nq``); attach-free stacks keep them. ``spec.compile()`` gates
the write; the entry XML is serialized with ``content_type`` stripped and
a fully PINNED ``dreamlake.layers.json`` (plus a ``builder`` record) sits
next to it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import warnings
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .stack import (
    DELETE_KINDS,
    SCHEMA,
    SINGLETON_KINDS,
    UPDATE_KINDS,
    ComposeError,
    Layer,
    Stack,
    load_stack,
    parse_address,
    require_resolved,
)

LAYERS_JSON = "dreamlake.layers.json"

#: Same compliance trade as hand_teleop's collectors' base welds.
BASE_WELD_SOLREF = [0.01, 1.0]
#: Zero quat in relpose -> the weld holds the qpos0 relative pose, i.e.
#: exactly the spawn pose the attach frame baked in; trailing 1 = torquescale.
ANCHOR_WELD_DATA = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1]

#: XML option attributes whose MjSpec field is spelled differently.
_OPTION_ATTR_MAP = {"actuatorgroupdisable": "disableactuator"}
#: XML compiler attributes whose MjSpec field is spelled differently.
_COMPILER_ATTR_MAP = {"angle": "degree", "inertiafromgeom": "inertiafromgeom"}
#: Compiler attributes that are per-layer PARSE context, not mergeable
#: opinions (each layer is resolved in its own compiler context before
#: union; the output's asset dirs belong to the stager).
_COMPILER_SKIP = {"meshdir", "texturedir", "assetdir"}
#: XML <visual> child tag -> MjSpec attribute.
_VISUAL_SUB_MAP = {"global": "global_"}

#: MjSpec collections checked for merge name collisions (worldbody's
#: "world" name excluded).
_NAMED_COLLECTIONS = (
    "bodies", "geoms", "joints", "sites", "cameras", "lights", "meshes",
    "textures", "materials", "actuators", "sensors", "tendons",
    "equalities", "keys", "numerics", "texts", "tuples", "hfields",
    "skins", "flexes",
)

#: Update/Patch: structural wrappers descended through, never matched.
#: (The matchable kinds are stack.UPDATE_KINDS / stack.DELETE_KINDS.)
_OVERRIDE_WRAPPERS = {"worldbody", "asset"}
#: Update/Patch: attributes that are identity/class plumbing, not opinions.
_OVERRIDE_META_ATTRS = {"name", "class", "childclass"}


def _require_mujoco():
    try:
        import mujoco
    except ImportError as e:
        raise ComposeError(
            "mujoco is required to compose env stacks but is not installed. "
            "Install the compose extra: pip install 'dreamlake[compose]' "
            "(or: uv pip install mujoco)"
        ) from e
    return mujoco


@dataclass
class Report:
    """A successful composition: what was written and what to relay."""

    entry: Path
    dir: Path
    stats: dict[str, int]
    warnings: list[str] = field(default_factory=list)
    layers: list[dict] = field(default_factory=list)
    ok: bool = True

    def to_json(self) -> dict:
        return {
            "ok": True,
            "entry": str(self.entry),
            "stats": self.stats,
            "warnings": self.warnings,
        }


# ── asset staging (ported from hand_teleop.envlayer.compose) ─────────────


class _Stager:
    """Flat staging into ``<out>/meshes/`` with clash detection.

    Two different source files may NOT claim one staged name -- that is
    the left/right-hand bare-filename trap, surfaced as an error instead
    of a silent overwrite. Staging the same source twice is fine.
    """

    def __init__(self, out_dir: Path):
        self.dir = out_dir / "meshes"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.staged: dict[str, Path] = {}

    def stage(self, source: Path, flat: str) -> str:
        source = source.resolve()
        if not source.is_file():
            raise FileNotFoundError(f"envlayer: no such asset file {source}")
        prior = self.staged.get(flat)
        if prior is not None and prior != source:
            raise ComposeError(
                f"envlayer: staged name clash {flat!r}: {prior} vs {source}")
        self.staged[flat] = source
        dst = self.dir / flat
        if not dst.is_file() or dst.stat().st_mtime < source.stat().st_mtime:
            shutil.copy2(source, dst)
        return flat

    def remove_stale(self) -> None:
        """Drop files a previous export to the same dir staged but this
        one did not (deterministic output directories)."""
        for f in self.dir.iterdir():
            if f.is_file() and f.name not in self.staged:
                f.unlink()


def _resolve_asset_file(base: Path, dirpath: str, file: str) -> Path:
    """Locate an asset file, absorbing URDF quirks.

    URDF ``mesh.file`` often already carries the meshdir prefix
    (``meshes/x.STL`` + ``meshdir="meshes"`` would double to
    ``meshes/meshes/x.STL``), and ``package://pkg/...`` URIs need the
    strip-pkg mapping. Candidates are tried in order; the first existing
    file wins.
    """
    candidates: list[Path] = []
    if file.startswith("package://"):
        rest = file[len("package://"):]
        candidates.append(base / rest)  # pkg dir vendored next to the model
        parts = rest.split("/", 1)
        if len(parts) == 2:
            candidates.append(base / parts[1])  # strip the package name
            candidates.append(base / dirpath / parts[1])
    else:
        candidates.append(base / dirpath / file)
        if dirpath:
            candidates.append(base / file)  # path-doubling normalization
            candidates.append(base / dirpath / Path(file).name)
    for cand in candidates:
        if cand.is_file():
            return cand
    raise ComposeError(
        f"envlayer: asset file {file!r} not found (tried: "
        + ", ".join(str(c) for c in candidates) + ")")


def _stage_child_assets(child, xml: Path, file_prefix: str, stager: _Stager) -> None:
    """Rewrite the child's asset references to (optionally prefixed) flat
    names and copy the files -- BEFORE attach: an attached child spec keeps
    resolving assets against its own meshdir at compile and to_xml time,
    so a rewrite after composition cannot redirect them. Meshes that carry
    no explicit name get their derived name (the file stem) pinned first,
    or the rename would re-derive it and orphan every geom reference."""
    base = xml.parent.resolve()
    for mesh in child.meshes:
        if not mesh.file:
            continue  # e.g. vertex meshes declared inline
        if not mesh.name:
            mesh.name = Path(mesh.file).stem
        src = _resolve_asset_file(base, child.meshdir or "", mesh.file)
        mesh.file = stager.stage(src, file_prefix + Path(mesh.file).name)
    for tex in child.textures:
        if not tex.file:
            continue  # builtin: serialized inline
        if not tex.name:
            tex.name = Path(tex.file).stem
        src = _resolve_asset_file(base, child.texturedir or "", tex.file)
        tex.file = stager.stage(src, file_prefix + Path(tex.file).name)
    child.meshdir = str(stager.dir) + os.sep
    child.texturedir = str(stager.dir) + os.sep


def _write_entry(spec, out_dir: Path, entry_name: str) -> Path:
    """Serialize the spec as the env's entry XML. to_xml stamps
    ``content_type=`` on assets it re-sniffed this run, flapping between
    builds; the extension carries the same fact, so it is stripped."""
    xml = re.sub(r'\s+content_type="[^"]*"', "", spec.to_xml())
    entry = out_dir / entry_name
    entry.write_text(xml)
    return entry


# ── raw-XML parsing (attribute presence = the opinion) ────────────────────


def _read_xml(path: Path) -> ET.Element:
    """Comment-stripped ElementTree parse (authored scenes use ``--``
    inside XML comments, which ElementTree rejects)."""
    text = re.sub(r"<!--.*?-->", "", path.read_text(), flags=re.DOTALL)
    try:
        return ET.fromstring(text)
    except ET.ParseError as e:
        raise ComposeError(f"cannot parse XML {path}: {e}") from e


@dataclass
class _StatedProfile:
    """Which option/visual/compiler fields a layer's XML explicitly states."""

    option: list[str] = field(default_factory=list)
    flags: dict[str, str] = field(default_factory=dict)
    compiler: list[str] = field(default_factory=list)
    visual: dict[str, list[str]] = field(default_factory=dict)  # sub tag -> attrs

    def __bool__(self) -> bool:
        return bool(self.option or self.flags or self.compiler or self.visual)


def _stated_profile(root: ET.Element) -> _StatedProfile:
    prof = _StatedProfile()
    if root.tag != "mujoco":
        return prof  # URDF etc.: no MJCF option/visual/compiler blocks
    for el in root.findall("option"):
        prof.option.extend(el.attrib)
        for flag in el.findall("flag"):
            prof.flags.update(flag.attrib)
    for el in root.findall("compiler"):
        prof.compiler.extend(el.attrib)
    for el in root.findall("visual"):
        for sub in el:
            prof.visual.setdefault(sub.tag, []).extend(sub.attrib)
    return prof


def _flag_bits(mujoco):
    disable = {
        name[len("mjDSBL_"):].lower(): int(getattr(mujoco.mjtDisableBit, name))
        for name in dir(mujoco.mjtDisableBit) if name.startswith("mjDSBL_")
    }
    enable = {
        name[len("mjENBL_"):].lower(): int(getattr(mujoco.mjtEnableBit, name))
        for name in dir(mujoco.mjtEnableBit) if name.startswith("mjENBL_")
    }
    return disable, enable


def _apply_profile(mujoco, spec, child, prof: _StatedProfile, i: int) -> None:
    """Copy the layer's STATED option/visual/compiler fields (parsed by
    mujoco itself in ``child``) onto the accumulated spec -- field-wise,
    later layer wins."""
    for attr in prof.option:
        name = _OPTION_ATTR_MAP.get(attr, attr)
        if not hasattr(spec.option, name):
            raise ComposeError(
                f"layer {i}: option attribute {attr!r} is not supported for "
                "field-wise merge", i)
        _assign(spec.option, name, getattr(child.option, name))
    if prof.flags:
        disable, enable = _flag_bits(mujoco)
        for attr, value in prof.flags.items():
            if value not in ("enable", "disable"):
                raise ComposeError(
                    f"layer {i}: flag {attr}={value!r} (want enable|disable)", i)
            if attr in disable:
                bit, on = disable[attr], value == "disable"
                spec.option.disableflags = (
                    spec.option.disableflags | bit if on
                    else spec.option.disableflags & ~bit)
            elif attr in enable:
                bit, on = enable[attr], value == "enable"
                spec.option.enableflags = (
                    spec.option.enableflags | bit if on
                    else spec.option.enableflags & ~bit)
            else:
                raise ComposeError(
                    f"layer {i}: unknown option flag {attr!r}", i)
    for attr in prof.compiler:
        if attr in _COMPILER_SKIP:
            continue  # per-layer parse context, already consumed
        name = _COMPILER_ATTR_MAP.get(attr, attr)
        if not hasattr(spec.compiler, name):
            raise ComposeError(
                f"layer {i}: compiler attribute {attr!r} is not supported for "
                "field-wise merge", i)
        _assign(spec.compiler, name, getattr(child.compiler, name))
    for sub, attrs in prof.visual.items():
        subname = _VISUAL_SUB_MAP.get(sub, sub)
        if not hasattr(spec.visual, subname):
            raise ComposeError(
                f"layer {i}: visual block <{sub}> is not supported for "
                "field-wise merge", i)
        dst = getattr(spec.visual, subname)
        src = getattr(child.visual, subname)
        for attr in attrs:
            if not hasattr(dst, attr):
                raise ComposeError(
                    f"layer {i}: visual attribute {sub}.{attr!r} is not "
                    "supported for field-wise merge", i)
            _assign(dst, attr, getattr(src, attr))


def _assign(obj, attr, value) -> None:
    """Set ``obj.attr``, writing element-wise into fixed-size arrays."""
    current = getattr(obj, attr)
    if isinstance(current, np.ndarray):
        current[...] = value
    else:
        setattr(obj, attr, value)


# ── merge ─────────────────────────────────────────────────────────────────


def _named(spec, collection: str) -> set[str]:
    names = {e.name for e in getattr(spec, collection) if e.name}
    if collection == "bodies":
        names.discard("world")
    return names


def _default_class_names(root: ET.Element) -> set[str]:
    if root.tag != "mujoco":
        return set()
    return {
        el.get("class") for el in root.iter("default") if el.get("class")
    }


def _check_merge_collisions(spec, child, child_root: ET.Element, i: int) -> None:
    """merge never changes an existing element: any name collision between
    the accumulated spec and the incoming layer is an error (mujoco's
    attach does NOT flag these -- duplicate names ride until compile, and
    colliding default classes silently coexist -- so the check runs here,
    with layer context)."""
    for collection in _NAMED_COLLECTIONS:
        clash = _named(spec, collection) & _named(child, collection)
        if clash:
            raise ComposeError(
                f"layer {i}: merge name collision on {collection}: "
                f"{sorted(clash)} already exist in the stack below", i)
    for cls in _default_class_names(child_root):
        if spec.find_default(cls) is not None:
            raise ComposeError(
                f"layer {i}: merge name collision on default class {cls!r}", i)


def _merge_layer(mujoco, ctx: _Context, layer: Layer, entry_path: Path) -> None:
    i = layer.index
    if entry_path.suffix.lower() == ".urdf":
        raise ComposeError(
            f"layer {i}: urdf sources are attach-only (URDF cannot express "
            "a world)", i)
    child_root = _read_xml(entry_path)
    child = mujoco.MjSpec.from_file(str(entry_path))
    _check_merge_collisions(ctx.spec, child, child_root, i)
    # Rewrite-before-attach, bare basenames (merge renames nothing; the
    # stager's clash detection surfaces same-name/different-bytes files).
    _stage_child_assets(child, entry_path, "", ctx.stager)
    # spec.attach DEFERS child keyframes: they are invisible on the parent
    # until compile resurrects them, correctly remapped to the composed
    # qpos layout (verified empirically). Merge-only stacks therefore keep
    # them for free; attach-bearing stacks strip them here (attach changes
    # nq). Deferral also hides them from the collision check above, so
    # their names are tracked in ctx.
    if ctx.keep_keys:
        clash = {k.name for k in child.keys if k.name} & ctx.deferred_keys
        if clash:
            raise ComposeError(
                f"layer {i}: merge name collision on keyframes: "
                f"{sorted(clash)} already exist in the stack below", i)
        ctx.deferred_keys.update(k.name for k in child.keys if k.name)
    else:
        _strip_keyframes(child)
    _apply_profile(mujoco, ctx.spec, child, _stated_profile(child_root), i)
    if ctx.modelname is None and child.modelname not in ("", "MuJoCo Model"):
        ctx.modelname = child.modelname
    # spec.attach copies the child's root <default> as an ANONYMOUS nested
    # group; to_xml serializes it class-less and the entry stops reloading
    # ("empty class name", observed empirically -- prefixed attach_body is
    # not affected). Naming it keeps the output XML valid and the child's
    # authoring conventions isolated, without touching element identities.
    cls = f"layer{i}"
    while ctx.spec.find_default(cls) is not None or child.find_default(cls) is not None:
        cls += "_"
    child.default.name = cls
    with warnings.catch_warnings():
        # mujoco warns about option/visual "attach conflicts" and keeps the
        # parent value -- exactly right: the stated fields were merged above.
        warnings.simplefilter("ignore")
        ctx.spec.attach(child, prefix="", frame=ctx.spec.worldbody.add_frame())


# ── attach ────────────────────────────────────────────────────────────────


def _sanitize_prefix(prefix: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", prefix)


def _strip_keyframes(spec) -> None:
    for key in list(spec.keys):
        spec.delete(key)


def _attach_layer(mujoco, ctx: _Context, layer: Layer, entry_path: Path) -> None:
    i = layer.index
    is_urdf = entry_path.suffix.lower() == ".urdf"
    child = mujoco.MjSpec.from_file(str(entry_path))
    _strip_keyframes(child)

    roots = list(child.worldbody.bodies)
    if len(roots) != 1:
        raise ComposeError(
            f"layer {i}: Attach source has {len(roots)} root bodies, "
            "attach grafts exactly one subtree", i)
    stray = len(child.worldbody.geoms) + len(child.worldbody.lights) \
        + len(child.worldbody.sites) + len(child.worldbody.cameras)
    if stray:
        ctx.warnings.append(
            f"Attach: {stray} worldbody-level geom/light/site/camera "
            f"element(s) of {layer.label()} are not attached "
            "(attach grafts the root body's subtree)")

    # The identity root: key "right" manufactures right:palm etc.
    prefix = layer.key + ":"
    _stage_child_assets(child, entry_path, _sanitize_prefix(prefix), ctx.stager)

    if is_urdf:
        # Actuators do not import from URDF (<transmission> has no MuJoCo
        # mapping): surface it, per the RFC's materialization report.
        nu = None
        try:
            nu = child.copy().compile().nu
        except Exception:
            nu = len(child.actuators)
        if nu == 0:
            ctx.warnings.append(f"unactuated import: {layer.label()}")

    # Stated option/visual/compiler fields ride the layer regardless of
    # arc (a child <option> dies on attach; field-wise merge is the
    # mechanism that keeps e.g. a gripper's cone/impratio tuning).
    if not is_urdf:
        _apply_profile(
            mujoco, ctx.spec, child, _stated_profile(_read_xml(entry_path)), i)

    at = layer.at
    root = child.worldbody.bodies[0]
    if at == "world":
        frame = ctx.spec.worldbody.add_frame(
            pos=list(layer.pos), quat=list(layer.quat))
        base = frame.attach_body(root, prefix, "")
    elif at.startswith("body:"):
        body = ctx.spec.body(at[len("body:"):])
        if body is None:
            raise ComposeError(
                f"layer {i}: Attach target {at!r}: no such body in the "
                "stack below", i)
        frame = body.add_frame(pos=list(layer.pos), quat=list(layer.quat))
        base = frame.attach_body(root, prefix, "")
    else:  # site:<name> -- the site's pose wins (the mount-point hook)
        site = ctx.spec.site(at[len("site:"):])
        if site is None:
            raise ComposeError(
                f"layer {i}: Attach target {at!r}: no such site in the "
                "stack below", i)
        base = site.attach_body(root, prefix, "")

    free_joints = [
        j for j in base.joints if j.type == mujoco.mjtJoint.mjJNT_FREE
    ]
    if layer.joint == "rigid":
        # Welded by construction; a freejoint the import brought along
        # (.mjcf.urdf mujoco-extension roots declare one) is removed.
        for j in free_joints:
            ctx.spec.delete(j)
    elif layer.joint in ("free", "free-anchored"):
        if not free_joints:
            # Reconcile: never double-add (a second freejoint is a
            # >6-dof compile error).
            base.add_freejoint()
        if layer.joint == "free-anchored":
            anchor = ctx.spec.worldbody.add_body(
                name=f"{prefix}anchor", mocap=True,
                pos=list(layer.pos), quat=list(layer.quat))
            weld = ctx.spec.add_equality()
            weld.type = mujoco.mjtEq.mjEQ_WELD
            weld.objtype = mujoco.mjtObj.mjOBJ_BODY
            weld.name1 = base.name
            weld.name2 = anchor.name
            weld.solref = BASE_WELD_SOLREF
            weld.data = ANCHOR_WELD_DATA


# ── the opinion ops: Update / Remove / Patch ─────────────────────────────


def _finders(spec) -> dict:
    """(kind -> MjSpec finder) for the named kinds Update/Patch match."""
    return {
        "body": spec.body, "geom": spec.geom, "joint": spec.joint,
        "site": spec.site, "camera": spec.camera,
        "light": spec.light, "material": spec.material,
    }


def _delete_finders(spec) -> dict:
    return {
        **_finders(spec),
        "mesh": spec.mesh, "texture": spec.texture,
        "actuator": spec.actuator, "sensor": spec.sensor,
        "tendon": spec.tendon, "equality": spec.equality,
        "key": spec.key,
    }


def _resolve_bare_name(name: str, finders: dict, op: str, i: int):
    """Probe every kind for a bare (unqualified) address; exactly one hit
    is the target, more is an ambiguity error, none a strict miss."""
    hits = [(kind, find(name)) for kind, find in finders.items()
            if find(name) is not None]
    if len(hits) > 1:
        kinds = ", ".join(sorted(k for k, _ in hits))
        raise ComposeError(
            f"layer {i}: {op}: {name!r} is ambiguous (matches {kinds}) -- "
            f'qualify the address as "<kind>:{name}"', i)
    if not hits:
        raise ComposeError(
            f"layer {i}: {op}: no element named {name!r} in the stack below "
            "(matching is strict -- check for typos or a stale stack)", i)
    return hits[0]


def _coerce(text: str, current, what: str, i: int):
    """Convert an XML attribute string to the field's Python type, using
    mujoco's XML value conventions (space-separated floats for vectors,
    true/false for booleans)."""
    try:
        if isinstance(current, np.ndarray):
            values = [float(v) for v in text.split()]
            if not values or len(values) > len(current):
                raise ValueError(
                    f"want 1..{len(current)} numbers, got {len(values)}")
            return values
        if isinstance(current, bool):
            if text in ("true", "1"):
                return True
            if text in ("false", "0"):
                return False
            raise ValueError("want true|false")
        if isinstance(current, int):
            return int(text)
        if isinstance(current, float):
            return float(text)
        if isinstance(current, str):
            return text
    except ValueError as e:
        raise ComposeError(f"layer {i}: {what}: cannot parse {text!r} ({e})", i)
    raise ComposeError(
        f"layer {i}: {what}: unsupported value type "
        f"{type(current).__name__}", i)


def _set_stated_attr(
    obj, kind: str, name: str, attr: str, text: str, i: int, op: str,
) -> None:
    if not hasattr(obj, attr):
        raise ComposeError(
            f"layer {i}: {op}: {kind} {name!r} has no attribute "
            f"{attr!r} settable through MjSpec", i)
    current = getattr(obj, attr)
    value = _coerce(text, current, f"{op} {kind} {name!r} attr {attr!r}", i)
    if isinstance(current, np.ndarray):
        current[:len(value)] = value  # XML allows partial vectors (e.g. size)
    else:
        setattr(obj, attr, value)


def _override_element(spec, el: ET.Element, i: int, finders, op: str) -> None:
    kind = el.tag
    name = el.get("name")
    if name is None:
        raise ComposeError(
            f"layer {i}: {op}: <{kind}> element without a name cannot be "
            "matched (identity is the MJCF name)", i)
    target = finders[kind](name)
    if target is None:
        raise ComposeError(
            f"layer {i}: {op}: no {kind} named {name!r} in the stack "
            "below (matching is strict -- check for typos or a stale stack)",
            i)
    for attr, text in el.attrib.items():
        if attr in _OVERRIDE_META_ATTRS:
            continue
        _set_stated_attr(target, kind, name, attr, text, i, op)


def _walk_override(spec, el: ET.Element, i: int, finders, op: str) -> None:
    for child in el:
        if child.tag in _OVERRIDE_WRAPPERS:
            _walk_override(spec, child, i, finders, op)
        elif child.tag in UPDATE_KINDS:
            _override_element(spec, child, i, finders, op)
            _walk_override(spec, child, i, finders, op)  # nested geoms/joints/bodies
        else:
            raise ComposeError(
                f"layer {i}: {op}: <{child.tag}> elements are not "
                f"overridable (supported: {', '.join(UPDATE_KINDS)}, plus "
                "option/visual/compiler blocks)", i)


def _apply_singleton_blocks(mujoco, ctx: _Context, blocks: str, i: int) -> None:
    """Route option/visual/compiler opinions through mujoco's own parser so
    stated values are coerced by its XML conventions (enums like
    integrator="implicitfast" included), then field-wise merge them."""
    doc = f"<mujoco>{blocks}<worldbody/></mujoco>"
    prof = _stated_profile(ET.fromstring(doc))
    try:
        mini = mujoco.MjSpec.from_string(doc)
    except Exception as e:
        raise ComposeError(f"layer {i}: {e}", i) from e
    _apply_profile(mujoco, ctx.spec, mini, prof, i)


def _json_attr_text(value, key: str, attr: str, i: int) -> str:
    """Serialize an Update prop to mujoco's XML value conventions; the
    typed coercion against the target field happens in _set_stated_attr."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value)
    raise ComposeError(  # load_stack pre-validates; belt and braces
        f"layer {i}: Update {key!r} attr {attr!r}: unsupported value "
        f"{value!r}", i)


def _update_layer(mujoco, ctx: _Context, layer: Layer) -> None:
    """The meta component: key addresses one element, props are the
    opinions -- serialized to XML strings and fed through the SAME strict
    matcher and coercion as Patch, so the two never drift."""
    i = layer.index
    key = layer.key
    attrs = {
        attr: _json_attr_text(value, key, attr, i)
        for attr, value in layer.props.items()
    }
    kind, name = parse_address(key, UPDATE_KINDS, SINGLETON_KINDS)

    if kind in SINGLETON_KINDS:
        if kind == "option":
            el = ET.Element("option", attrs)
        else:  # visual:<sub>
            el = ET.Element("visual")
            el.append(ET.Element(name, attrs))
        _apply_singleton_blocks(
            mujoco, ctx, ET.tostring(el, encoding="unicode"), i)
        return

    finders = _finders(ctx.spec)
    if kind is None:
        kind, _target = _resolve_bare_name(name, finders, "Update", i)
    el = ET.Element(kind, {"name": name, **attrs})
    _override_element(ctx.spec, el, i, finders, "Update")


def _remove_layer(ctx: _Context, layer: Layer) -> None:
    i = layer.index
    finders = _delete_finders(ctx.spec)
    kind, name = parse_address(layer.key, DELETE_KINDS)
    if kind is None:
        kind, target = _resolve_bare_name(name, finders, "Remove", i)
    else:
        target = finders[kind](name)
        if target is None:
            raise ComposeError(
                f"layer {i}: Remove: no {kind} named {name!r} in the stack "
                "below", i)
    ctx.spec.delete(target)


def _patch_layer(mujoco, ctx: _Context, layer: Layer, entry_path: Path) -> None:
    i = layer.index
    root = _read_xml(entry_path)
    if root.tag != "mujoco":
        raise ComposeError(
            f"layer {i}: Patch source {entry_path.name} is not an MJCF "
            "document (root <mujoco> expected)", i)

    if _stated_profile(root):
        blocks = "".join(
            ET.tostring(el, encoding="unicode")
            for tag in ("compiler", "option", "visual")
            for el in root.findall(tag)
        )
        _apply_singleton_blocks(mujoco, ctx, blocks, i)

    finders = _finders(ctx.spec)
    for el in root:
        if el.tag in ("option", "visual", "compiler"):
            continue  # handled above
        if el.tag in _OVERRIDE_WRAPPERS:
            _walk_override(ctx.spec, el, i, finders, "Patch")
        elif el.tag in UPDATE_KINDS:
            _override_element(ctx.spec, el, i, finders, "Patch")
            _walk_override(ctx.spec, el, i, finders, "Patch")
        else:
            raise ComposeError(
                f"layer {i}: Patch: <{el.tag}> elements are not "
                f"overridable (supported: {', '.join(UPDATE_KINDS)}, plus "
                "option/visual/compiler blocks)", i)


# ── the stack evaluator ───────────────────────────────────────────────────


@dataclass
class _Context:
    spec: object
    stager: _Stager
    #: Keyframes survive only in merge-only stacks: attach changes nq, so
    #: an attach-bearing stack never admits them (decided up front -- a
    #: stale key would also trip spec.delete's keyframe validation
    #: mid-stack, observed empirically).
    keep_keys: bool = True
    #: Names of keyframes attach has deferred (resurrected at compile).
    deferred_keys: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)
    modelname: str | None = None


def _layer_entry_path(layer: Layer, base_dir: Path | None) -> Path:
    """Resolve a layer's local src to the file the substrate loads: a
    single file is used as-is, a directory is read in the standard env
    shape."""
    i = layer.index
    path = Path(layer.src)
    if not path.is_absolute():
        if base_dir is None:
            raise ComposeError(
                f"layer {i}: relative src {layer.src!r} needs the stack to "
                "be loaded from a file (no base directory)", i)
        path = base_dir / path
    if path.is_file():
        return path
    if not path.is_dir():
        raise ComposeError(f"layer {i}: no such src: {path}", i)
    # A directory in the standard env shape: a materialized composed env
    # records its entry in dreamlake.layers.json (consumed depth-0 -- the
    # artifact, never a recursive re-compose); otherwise scene.xml, or a
    # sole .xml/.urdf.
    layers_json = path / LAYERS_JSON
    if layers_json.is_file():
        try:
            entry = json.loads(layers_json.read_text()).get("entry", "scene.xml")
        except json.JSONDecodeError:
            entry = "scene.xml"
        candidate = path / entry
        if candidate.is_file():
            return candidate
    if (path / "scene.xml").is_file():
        return path / "scene.xml"
    xmls = sorted(path.glob("*.xml"))
    if len(xmls) == 1:
        return xmls[0]
    urdfs = sorted(path.glob("*.urdf"))
    if len(urdfs) == 1:
        return urdfs[0]
    raise ComposeError(
        f"layer {i}: cannot resolve an entry file in {path} (want "
        f"{LAYERS_JSON} with an entry, a scene.xml, or exactly one "
        ".xml/.urdf)", i)


def _engine_version() -> str:
    try:
        from importlib.metadata import version
        return version("dreamlake")
    except Exception:
        return "0.0.0+unknown"


def _pinned_layers(stack: Stack, base_dir: Path | None) -> list[dict]:
    """The stack copy embedded in the artifact: every src pinned, local
    srcs marked unpinned (push discipline reads this). Components are
    carried verbatim otherwise -- authored prop order included."""
    out = []
    for layer in stack.layers:
        entry = dict(layer.data)
        entry.pop("pin", None)
        entry.pop("unpinned", None)
        if layer.src is not None:
            if layer.pin:
                entry["src"] = layer.pin
            else:
                resolved = Path(layer.src)
                if not resolved.is_absolute() and base_dir is not None:
                    resolved = base_dir / resolved
                entry["src"] = str(resolved)
                entry["unpinned"] = True
        out.append(entry)
    return out


def compose_stack(resolved_stack: dict | str | Path, out_dir: Path) -> Report:
    """Materialize a RESOLVED stack into ``out_dir``.

    Writes ``<out_dir>/{entry, meshes/, dreamlake.layers.json}`` and
    returns a :class:`Report`; raises :class:`ComposeError` (with the
    offending layer index where attributable) on any failure. Network-free
    and deterministic: sources are local paths, the importer/serializer is
    mujoco itself, and the builder record pins both versions.
    """
    stack = load_stack(resolved_stack)
    require_resolved(stack)
    mujoco = _require_mujoco()

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ctx = _Context(
        spec=mujoco.MjSpec(),
        stager=_Stager(out_dir),
        keep_keys=not stack.has_attach,
    )

    for layer in stack.layers:
        try:
            if layer.tag == "Merge":
                _merge_layer(
                    mujoco, ctx, layer, _layer_entry_path(layer, stack.base_dir))
            elif layer.tag == "Attach":
                _attach_layer(
                    mujoco, ctx, layer, _layer_entry_path(layer, stack.base_dir))
            elif layer.tag == "Update":
                _update_layer(mujoco, ctx, layer)
            elif layer.tag == "Remove":
                _remove_layer(ctx, layer)
            else:  # Patch
                _patch_layer(
                    mujoco, ctx, layer, _layer_entry_path(layer, stack.base_dir))
        except ComposeError:
            raise
        except Exception as e:
            raise ComposeError(
                f"layer {layer.index} ({layer.tag} {layer.label()}): {e}",
                layer.index) from e

    if stack.name:
        ctx.spec.modelname = stack.name
    elif ctx.modelname:
        ctx.spec.modelname = ctx.modelname

    try:
        model = ctx.spec.compile()
    except Exception as e:
        raise ComposeError(f"composed stack does not compile: {e}") from e

    # MuJoCo-convention viewers render geom groups 0-2 only; sizeable
    # visual-only geoms parked in 3-5 (a decor tier, say) silently vanish
    # for every consumer. Collision proxies (nonzero contype/conaffinity)
    # and sub-centimeter marker geoms are legitimately hidden -- skip them.
    hidden_visual = sum(
        1 for g in range(model.ngeom)
        if model.geom_group[g] >= 3
        and model.geom_contype[g] == 0 and model.geom_conaffinity[g] == 0
        and float(max(model.geom_size[g])) > 0.01
    )
    if hidden_visual:
        ctx.warnings.append(
            f"{hidden_visual} visual-only geoms sit in geom groups 3-5, "
            "which viewers hide by default -- put presentation content in "
            "groups 0-2"
        )

    ctx.spec.meshdir = "meshes/"
    ctx.spec.texturedir = "meshes/"
    ctx.spec.modelfiledir = str(out_dir) + os.sep
    entry = _write_entry(ctx.spec, out_dir, stack.entry)
    ctx.stager.remove_stale()

    layers = _pinned_layers(stack, stack.base_dir)
    pinned = {
        "schema": SCHEMA,
        "substrate": stack.substrate,
        "entry": stack.entry,
        **({"name": stack.name} if stack.name else {}),
        "layers": layers,
        "builder": {
            "engine": f"dreamlake-py/{_engine_version()}",
            "mujoco": mujoco.__version__,
        },
    }
    (out_dir / LAYERS_JSON).write_text(json.dumps(pinned, indent=2) + "\n")

    return Report(
        entry=entry,
        dir=out_dir,
        stats={"nbody": model.nbody, "njnt": model.njnt, "nu": model.nu},
        warnings=ctx.warnings,
        layers=layers,
    )
