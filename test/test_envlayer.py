"""Env-layer composition engine tests (RFC 0007, v3 component grammar).

Self-contained: every layer -- mini scene, mini robot, nameless-mesh
gripper (a programmatic one-triangle binary STL), minimal URDFs, sparse
patch docs -- is written into tmp_path by the fixtures below. Requires
mujoco (the ``compose`` extra); the whole module skips cleanly without it.
"""

import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest

mujoco = pytest.importorskip(
    "mujoco", reason="needs mujoco (pip install 'dreamlake[compose]')")

from dreamlake.envlayer import ComposeError, compose_stack, load_stack

SCHEMA = "dreamlake.env-layers/v3"


# ─── fixture builders ────────────────────────────────────────────────

SCENE_XML = """
<mujoco model="mini-scene">
  <option timestep="0.002" gravity="0 0 -9.81"/>
  <default><default class="props"><geom rgba="0.6 0.6 0.9 1"/></default></default>
  <asset>
    <texture name="grid" type="2d" builtin="checker" rgb1="0.1 0.2 0.3"
             rgb2="0.2 0.3 0.4" width="64" height="64"/>
    <material name="gridmat" texture="grid"/>
  </asset>
  <worldbody>
    <geom name="floor" type="plane" size="2 2 0.1" material="gridmat"/>
    <light name="key" pos="0 0 3" dir="0 0 -1" diffuse="0.8 0.8 0.8"/>
    <body name="fixture/shelf" pos="0.6 0 0.3">
      <geom name="fixture/shelf_geom" type="box" size="0.2 0.3 0.02"/>
      <site name="mount" pos="0 0 0.1" quat="1 0 0 0"/>
    </body>
    <body name="obj/mug" pos="0.3 0.1 0.05">
      <joint name="obj/mug_free" type="free"/>
      <geom name="obj/mug_geom" class="props" type="sphere" size="0.03" mass="0.1"/>
    </body>
  </worldbody>
  <keyframe><key name="home" qpos="0.3 0.1 0.05 1 0 0 0"/></keyframe>
</mujoco>
"""

ROBOT_XML = """
<mujoco model="mini-robot">
  <compiler angle="radian"/>
  <option cone="elliptic" impratio="10"/>
  <worldbody>
    <body name="base">
      <geom name="base_geom" type="box" size="0.05 0.05 0.05" mass="1"/>
      <body name="link1" pos="0 0 0.1">
        <joint name="shoulder" type="hinge" damping="0.5"/>
        <geom name="link1_geom" type="capsule" size="0.02" fromto="0 0 0 0 0 0.2" mass="0.3"/>
        <body name="link2" pos="0 0 0.2">
          <joint name="elbow" type="hinge" damping="0.5"/>
          <geom name="link2_geom" type="capsule" size="0.02" fromto="0 0 0 0 0 0.15" mass="0.2"/>
        </body>
      </body>
    </body>
  </worldbody>
  <actuator>
    <position name="a_shoulder" joint="shoulder" kp="10"/>
    <position name="a_elbow" joint="elbow" kp="10"/>
  </actuator>
</mujoco>
"""

MUG_XML = """
<mujoco model="mini-mug">
  <worldbody>
    <body name="mug">
      <geom name="cup" type="sphere" size="0.03" mass="0.1" rgba="0.9 0.9 0.9 1"/>
    </body>
  </worldbody>
</mujoco>
"""

MINI_URDF = """<?xml version="1.0"?>
<robot name="mini">
  <link name="base_link">
    <inertial><mass value="1.0"/>
      <inertia ixx="0.01" iyy="0.01" izz="0.01" ixy="0" ixz="0" iyz="0"/></inertial>
    <collision><geometry><box size="0.1 0.1 0.1"/></geometry></collision>
  </link>
  <link name="arm_link">
    <inertial><mass value="0.5"/>
      <inertia ixx="0.005" iyy="0.005" izz="0.005" ixy="0" ixz="0" iyz="0"/></inertial>
    <collision><geometry><cylinder radius="0.02" length="0.2"/></geometry></collision>
  </link>
  <joint name="shoulder" type="revolute">
    <parent link="base_link"/><child link="arm_link"/>
    <origin xyz="0 0 0.05"/><axis xyz="0 1 0"/>
    <limit lower="-1.57" upper="1.57" effort="10" velocity="2"/>
  </joint>
</robot>
"""

#: The .mjcf.urdf mujoco-extension shape: the imported root ALREADY carries
#: a free joint (here via a URDF floating joint to world), so the attach
#: rule must reconcile instead of double-adding.
FLOATING_URDF = """<?xml version="1.0"?>
<robot name="floaty">
  <link name="world"/>
  <joint name="root_float" type="floating">
    <parent link="world"/><child link="base_link"/>
  </joint>
  <link name="base_link">
    <inertial><mass value="1.0"/>
      <inertia ixx="0.01" iyy="0.01" izz="0.01" ixy="0" ixz="0" iyz="0"/></inertial>
    <collision><geometry><box size="0.1 0.1 0.1"/></geometry></collision>
  </link>
</robot>
"""


def _write_env(tmp_path: Path, name: str, xml: str, entry: str = "scene.xml") -> Path:
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    (d / entry).write_text(xml)
    return d


def _write_stl(path: Path) -> None:
    """A tiny tetrahedron as binary STL (mujoco requires >= 4 vertices)."""
    v = [(0, 0, 0), (0.02, 0, 0), (0, 0.02, 0), (0, 0, 0.02)]
    faces = [(0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)]
    blob = b"\0" * 80 + struct.pack("<I", len(faces))
    for a, b, c in faces:
        blob += struct.pack("<12f", 0, 0, 0, *v[a], *v[b], *v[c])
        blob += struct.pack("<H", 0)
    path.write_bytes(blob)


def _write_gripper(tmp_path: Path) -> Path:
    """A gripper whose mesh is declared NAME-LESS (the 2F-85/rizon4 trap):
    staging must pin the derived name before renaming the file."""
    d = tmp_path / "gripper"
    (d / "assets").mkdir(parents=True, exist_ok=True)
    _write_stl(d / "assets" / "pad.stl")
    (d / "gripper.xml").write_text("""
<mujoco model="mini-gripper">
  <compiler meshdir="assets"/>
  <asset><mesh file="pad.stl"/></asset>
  <worldbody>
    <body name="palm">
      <geom name="palm_geom" type="box" size="0.02 0.02 0.02" mass="0.2"/>
      <geom name="pad_geom" type="mesh" mesh="pad" mass="0.05"/>
    </body>
  </worldbody>
</mujoco>
""")
    return d


def _stack(tmp_path: Path, layers: list[dict], **top) -> Path:
    doc = {"schema": SCHEMA, **top, "layers": layers}
    path = tmp_path / "dreamlake.layers.json"
    path.write_text(json.dumps(doc, indent=2))
    return path


def _merge(path, **extra) -> dict:
    return {"tag": "Merge", "src": str(path), **extra}


def _attach(path, key: str, **extra) -> dict:
    return {"tag": "Attach", "src": str(path), "key": key, **extra}


def _compose(tmp_path: Path, layers: list[dict], out: str = "out", **top):
    stack = _stack(tmp_path, layers, **top)
    return compose_stack(stack, tmp_path / out)


def _model(report) -> "mujoco.MjModel":
    """The written artifact must recompile FROM DISK (the v1 discipline)."""
    return mujoco.MjModel.from_xml_path(str(report.entry))


# ─── Merge ───────────────────────────────────────────────────────────


def test_merge_completeness(tmp_path):
    """The union primitive carries assets, defaults classes and actuators
    (the fallback-proof: a layer using all three merges and compiles)."""
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    robot = _write_env(tmp_path, "robot", ROBOT_XML)  # merged, not attached
    report = _compose(tmp_path, [_merge(scene), _merge(robot)])
    m = _model(report)
    assert m.nu == 2 and m.actuator("a_shoulder") is not None
    assert m.body("fixture/shelf") is not None and m.body("base") is not None
    # the scene's default class applied to the class-referencing geom
    assert m.geom("obj/mug_geom").rgba == pytest.approx([0.6, 0.6, 0.9, 1])
    # scene texture/material survived and the floor still references them
    assert m.nmat == 1 and m.ntex == 1
    assert report.stats == {"nbody": m.nbody, "njnt": m.njnt, "nu": m.nu}


def test_merge_collision_is_layer_indexed_error(tmp_path):
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    clash = _write_env(tmp_path, "clash", """
<mujoco model="clash">
  <worldbody><body name="obj/mug"><geom type="sphere" size="0.01" mass="0.1"/></body></worldbody>
</mujoco>
""")
    with pytest.raises(ComposeError) as e:
        _compose(tmp_path, [_merge(scene), _merge(clash)])
    assert e.value.layer == 1
    assert "collision" in str(e.value) and "obj/mug" in str(e.value)


def test_option_fieldwise_later_wins_stated_fields_only(tmp_path):
    """A physics-profile layer's stated option fields win; its UNSTATED
    fields (which MjSpec fills with defaults) must not clobber the base."""
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    profile = _write_env(tmp_path, "profile", """
<mujoco model="mjx-profile">
  <option cone="elliptic" impratio="10"><flag contact="disable"/></option>
</mujoco>
""")
    report = _compose(tmp_path, [_merge(scene), _merge(profile)])
    m = _model(report)
    assert m.opt.cone == mujoco.mjtCone.mjCONE_ELLIPTIC
    assert m.opt.impratio == 10
    assert m.opt.disableflags & mujoco.mjtDisableBit.mjDSBL_CONTACT
    # stated by the scene, unstated by the profile: the scene's value holds
    assert m.opt.timestep == pytest.approx(0.002)
    assert m.opt.gravity[2] == pytest.approx(-9.81)


# ─── Attach ──────────────────────────────────────────────────────────


def test_attach_free_anchored_parity(tmp_path):
    """v1 parity: names live under the key's identity root
    (bodies/joints/actuators), and the mocap-anchor weld holds the base
    over 200 steps."""
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    robot = _write_env(tmp_path, "robot", ROBOT_XML)
    report = _compose(tmp_path, [
        _merge(scene),
        _attach(robot, "rob", at="world", pos=[0.45, -0.12, 0.3],
                joint="free-anchored"),
    ])
    m = _model(report)
    assert m.body("rob:base") is not None
    assert m.joint("rob:shoulder") is not None
    with pytest.raises(KeyError):
        m.actuator("a_shoulder")  # the bare name is gone
    assert m.actuator("rob:a_shoulder") is not None
    assert m.body("rob:anchor").mocapid[0] >= 0
    # the attach layer's stated option opinions ride the stack
    assert m.opt.cone == mujoco.mjtCone.mjCONE_ELLIPTIC
    d = mujoco.MjData(m)
    for _ in range(200):
        mujoco.mj_step(m, d)
    # without the weld, 0.4s of free fall would put the base on the floor;
    # the compliant weld (solref 0.01/1.0) keeps it within a few cm.
    assert d.xpos[m.body("rob:base").id] == pytest.approx(
        [0.45, -0.12, 0.3], abs=0.06), "anchored base fell"


def test_attach_rigid_at_body_target(tmp_path):
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    mug = _write_env(tmp_path, "mug", MUG_XML)
    report = _compose(tmp_path, [
        _merge(scene),
        _attach(mug, "m", at="body:fixture/shelf", pos=[0, 0, 0.05],
                joint="rigid"),
    ])
    m = _model(report)
    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    # shelf sits at 0.6 0 0.3; rigid child rides its frame
    assert d.xpos[m.body("m:mug").id] == pytest.approx([0.6, 0, 0.35])
    assert m.body("m:mug").jntnum[0] == 0  # welded by construction


def test_attach_at_site_pose_wins(tmp_path):
    """`site:` targets take the site's own pose (the mount-point hook);
    the site comes from a lower layer's body, not the world."""
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    mug = _write_env(tmp_path, "mug", MUG_XML)
    report = _compose(tmp_path, [
        _merge(scene),
        _attach(mug, "m", at="site:mount", joint="rigid"),
    ])
    m = _model(report)
    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    # shelf 0.6 0 0.3 + site 0 0 0.1
    assert d.xpos[m.body("m:mug").id] == pytest.approx([0.6, 0, 0.4])


def test_attach_missing_target_is_layer_indexed(tmp_path):
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    mug = _write_env(tmp_path, "mug", MUG_XML)
    with pytest.raises(ComposeError) as e:
        _compose(tmp_path, [
            _merge(scene),
            _attach(mug, "m", at="site:nope", joint="rigid"),
        ])
    assert e.value.layer == 1 and "no such site" in str(e.value)


def test_repeated_attach_entries_are_independent(tmp_path):
    """v2's `instances` is gone: placing one source N times is N Attach
    entries, each a full identity root an Update above can target alone."""
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    mug = _write_env(tmp_path, "mug", MUG_XML)
    report = _compose(tmp_path, [
        _merge(scene),
        _attach(mug, "mug1", at="world", pos=[0.30, 0.10, 0.05], joint="free"),
        _attach(mug, "mug2", at="world", pos=[0.42, -0.08, 0.05], joint="free"),
        _attach(mug, "mug3", at="world", pos=[0.54, 0.10, 0.05], joint="free"),
        {"tag": "Update", "key": "mug2:cup", "rgba": [1, 0, 0, 1]},
    ])
    m = _model(report)
    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    for key, pos in (("mug1:", [0.30, 0.10, 0.05]),
                     ("mug2:", [0.42, -0.08, 0.05]),
                     ("mug3:", [0.54, 0.10, 0.05])):
        body = m.body(key + "mug")
        assert body.jntnum[0] == 1  # each entry fully independent (free)
        assert d.xpos[body.id] == pytest.approx(pos)
    assert m.geom("mug2:cup").rgba == pytest.approx([1, 0, 0, 1])
    assert m.geom("mug1:cup").rgba == pytest.approx([0.9, 0.9, 0.9, 1])


def test_duplicate_attach_key_rejected(tmp_path):
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    mug = _write_env(tmp_path, "mug", MUG_XML)
    layer = _attach(mug, "m", at="world", joint="rigid")
    with pytest.raises(ComposeError) as e:
        _compose(tmp_path, [_merge(scene), layer, dict(layer)])
    assert e.value.layer == 2 and "duplicate Attach key" in str(e.value)


def test_nameless_mesh_gets_pinned_and_prefixed(tmp_path):
    """The 2F-85 trap: a name-less mesh must keep its derived name (stem)
    while its FILE is renamed under the layer key -- otherwise the geom
    reference orphans at compile."""
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    gripper = _write_gripper(tmp_path)
    report = _compose(tmp_path, [
        _merge(scene),
        _attach(gripper, "grip", at="world", pos=[0.45, 0, 0.35],
                joint="free-anchored", pin="marvin/mini-gripper@1"),
    ])
    m = _model(report)  # geom->mesh reference survived staging + attach
    assert m.mesh("grip:pad") is not None
    staged = sorted(p.name for p in (report.dir / "meshes").iterdir())
    assert staged == ["grip_pad.stl"]


# ─── Update (the meta component) ─────────────────────────────────────


def test_update_inline_coercion_coverage(tmp_path):
    """Inline props reach every value shape: vectors, floats, ints, bools,
    strings -- through the same coercion as Patch."""
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    robot = _write_env(tmp_path, "robot", ROBOT_XML)
    report = _compose(tmp_path, [
        _merge(scene),
        _attach(robot, "rob", at="world", pos=[0.45, -0.12, 0.3],
                joint="free-anchored"),
        {"tag": "Update", "key": "body:obj/mug",
         "pos": [0.4, 0, 0.05], "quat": [0, 0, 0, 1]},
        {"tag": "Update", "key": "geom:rob:link1_geom",
         "rgba": [1, 0, 0, 1], "friction": [0.5]},
        {"tag": "Update", "key": "floor",  # bare name, unambiguous
         "size": [3, 3, 0.1], "contype": 2, "conaffinity": 2},
        {"tag": "Update", "key": "joint:rob:shoulder",
         "damping": 2.5, "armature": 0.01, "range": [-1, 1],
         "stiffness": 3, "frictionloss": 0.2},
        {"tag": "Update", "key": "light:key",
         "diffuse": [0.2, 0.2, 0.3], "active": False},
        {"tag": "Update", "key": "material:gridmat",
         "rgba": [0.5, 0.4, 0.3, 1]},
    ])
    m = _model(report)
    assert m.body("obj/mug").pos == pytest.approx([0.4, 0, 0.05])
    assert m.body("obj/mug").quat == pytest.approx([0, 0, 0, 1])
    assert m.geom("rob:link1_geom").rgba == pytest.approx([1, 0, 0, 1])
    assert m.geom("rob:link1_geom").friction[0] == pytest.approx(0.5)
    assert m.geom("floor").size == pytest.approx([3, 3, 0.1])
    assert m.geom("floor").contype == 2 and m.geom("floor").conaffinity == 2
    j = m.joint("rob:shoulder")
    assert j.damping[0] == pytest.approx(2.5)
    assert j.armature[0] == pytest.approx(0.01)
    assert j.range == pytest.approx([-1, 1])
    assert j.stiffness[0] == pytest.approx(3)
    assert j.frictionloss[0] == pytest.approx(0.2)
    assert m.light("key").diffuse == pytest.approx([0.2, 0.2, 0.3])
    assert m.mat("gridmat").rgba == pytest.approx([0.5, 0.4, 0.3, 1])
    assert 'active="false"' in report.entry.read_text()


def test_update_option_singleton_fieldwise(tmp_path):
    """`key: "option"` addresses the singleton; enums coerce through
    mujoco's own parser; unstated fields keep the stack's values."""
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    report = _compose(tmp_path, [
        _merge(scene),
        {"tag": "Update", "key": "option", "impratio": 20, "cone": "elliptic"},
    ])
    m = _model(report)
    assert m.opt.impratio == 20
    assert m.opt.cone == mujoco.mjtCone.mjCONE_ELLIPTIC
    assert m.opt.timestep == pytest.approx(0.002)  # unstated: kept


def test_update_visual_subblock(tmp_path):
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    report = _compose(tmp_path, [
        _merge(scene),
        {"tag": "Update", "key": "visual:headlight",
         "ambient": [0.1, 0.2, 0.3]},
    ])
    m = _model(report)
    assert m.vis.headlight.ambient == pytest.approx([0.1, 0.2, 0.3])


def test_update_strict_miss_carries_layer_index(tmp_path):
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    with pytest.raises(ComposeError) as e:
        _compose(tmp_path, [
            _merge(scene),
            {"tag": "Update", "key": "geom:floof", "rgba": [1, 0, 0, 1]},
        ])
    assert e.value.layer == 1
    assert "no geom named 'floof'" in str(e.value)


def test_update_bare_name_ambiguity_wants_qualifier(tmp_path):
    """A name shared across kinds (legal in MJCF: uniqueness is per type)
    must be qualified; the error names the candidate kinds."""
    scene = _write_env(tmp_path, "twin", """
<mujoco model="twin">
  <worldbody>
    <geom name="floor" type="plane" size="2 2 0.1"/>
    <body name="thing" pos="0 0 0.1">
      <geom name="thing" type="sphere" size="0.03" mass="0.1"/>
    </body>
  </worldbody>
</mujoco>
""")
    with pytest.raises(ComposeError) as e:
        _compose(tmp_path, [
            _merge(scene),
            {"tag": "Update", "key": "thing", "pos": [0, 0, 0.2]},
        ])
    assert e.value.layer == 1
    msg = str(e.value)
    assert "ambiguous" in msg and "body" in msg and "geom" in msg
    # the qualified address works
    report = _compose(tmp_path, [
        _merge(scene),
        {"tag": "Update", "key": "body:thing", "pos": [0, 0, 0.2]},
    ], out="ok")
    assert _model(report).body("thing").pos == pytest.approx([0, 0, 0.2])


# ─── Remove ──────────────────────────────────────────────────────────


def test_remove_drops_subtree_and_is_reversible(tmp_path):
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    remove = {"tag": "Remove", "key": "body:fixture/shelf"}

    report = _compose(tmp_path, [_merge(scene), remove], out="deleted")
    m = _model(report)
    with pytest.raises(KeyError):
        m.body("fixture/shelf")
    with pytest.raises(KeyError):
        m.geom("fixture/shelf_geom")  # the subtree went with it

    # non-destructive at the STACK level: drop the line, the body is back
    report2 = _compose(tmp_path, [_merge(scene)], out="restored")
    assert _model(report2).body("fixture/shelf") is not None


def test_remove_bare_name_and_missing_name(tmp_path):
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    # bare unambiguous name resolves across the removable kinds
    report = _compose(tmp_path, [
        _merge(scene),
        {"tag": "Remove", "key": "fixture/shelf"},
    ], out="bare")
    with pytest.raises(KeyError):
        _model(report).body("fixture/shelf")

    with pytest.raises(ComposeError) as e:
        _compose(tmp_path, [
            _merge(scene),
            {"tag": "Remove", "key": "body:fixture/plant"},
        ], out="missing")
    assert e.value.layer == 1 and "fixture/plant" in str(e.value)


# ─── Patch ───────────────────────────────────────────────────────────


def test_patch_attribute_coverage(tmp_path):
    """A sparse-MJCF file layer: same strict semantics as Update, for
    opinion sets big enough to be their own artifact."""
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    patch = tmp_path / "patch.xml"
    patch.write_text("""
<mujoco>
  <!-- sparse MJCF; comments with -- dashes are stripped before parsing -->
  <option impratio="20"/>
  <asset><material name="gridmat" rgba="0.5 0.4 0.3 1"/></asset>
  <worldbody>
    <light name="key" diffuse="0.2 0.2 0.3"/>
    <body name="obj/mug" pos="0.4 0 0.05"/>
  </worldbody>
</mujoco>
""")
    report = _compose(tmp_path, [
        _merge(scene),
        {"tag": "Patch", "src": "./patch.xml"},  # relative to the stack dir
    ])
    m = _model(report)
    assert m.opt.impratio == 20
    assert m.body("obj/mug").pos == pytest.approx([0.4, 0, 0.05])
    assert m.light("key").diffuse == pytest.approx([0.2, 0.2, 0.3])
    assert m.mat("gridmat").rgba == pytest.approx([0.5, 0.4, 0.3, 1])


def test_patch_strict_miss_carries_layer_index(tmp_path):
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    (tmp_path / "typo.xml").write_text(
        '<mujoco><worldbody><geom name="floof" rgba="1 0 0 1"/></worldbody></mujoco>')
    with pytest.raises(ComposeError) as e:
        _compose(tmp_path, [
            _merge(scene),
            {"tag": "Patch", "src": "./typo.xml"},
        ])
    assert e.value.layer == 1
    assert "no geom named 'floof'" in str(e.value)


# ─── stack schema + resolution ───────────────────────────────────────


def test_env_src_rejected_with_guidance(tmp_path):
    with pytest.raises(ComposeError) as e:
        _compose(tmp_path, [
            {"tag": "Merge", "src": "fortyfive/scene-berry@3"},
        ])
    assert e.value.layer == 0
    assert "resolve refs first" in str(e.value)


def test_v2_schema_gets_migration_error(tmp_path):
    with pytest.raises(ComposeError) as e:
        load_stack({"schema": "dreamlake.env-layers/v2", "layers": [
            {"source": {"path": "/x"}, "compose": {"mode": "merge"}}]})
    msg = str(e.value)
    assert "superseded" in msg and "Merge" in msg and "Update" in msg


def test_stack_validation_errors(tmp_path):
    with pytest.raises(ComposeError, match="schema"):
        load_stack({"schema": "dreamlake.env-layers/v1", "layers": []})
    with pytest.raises(ComposeError) as e:
        load_stack({"schema": SCHEMA, "layers": [
            {"tag": "Merge", "src": "/x", "key": "a"}]})
    assert e.value.layer == 0 and "unknown Merge keys" in str(e.value)
    with pytest.raises(ComposeError) as e:
        load_stack({"schema": SCHEMA, "layers": [
            {"tag": "Attach", "src": "/x", "key": "a", "joint": "loose"}]})
    assert e.value.layer == 0 and "joint" in str(e.value)
    with pytest.raises(ComposeError) as e:
        load_stack({"schema": SCHEMA, "layers": [
            {"tag": "Attach", "src": "/x", "key": "a:", "joint": "rigid"}]})
    assert "key" in str(e.value)  # the separator is the composer's job
    with pytest.raises(ComposeError) as e:
        load_stack({"schema": SCHEMA, "layers": [
            {"tag": "Merge", "src": "kitchen"}]})
    assert "local paths must start with" in str(e.value)
    with pytest.raises(ComposeError) as e:
        load_stack({"schema": SCHEMA, "layers": [
            {"tag": "Update", "key": "obj/mug"}]})
    assert "no opinions" in str(e.value)
    with pytest.raises(ComposeError) as e:
        load_stack({"schema": SCHEMA, "layers": [
            {"tag": "Update", "key": "obj/mug", "name": "renamed"}]})
    assert "cannot set 'name'" in str(e.value)
    with pytest.raises(ComposeError) as e:
        load_stack({"schema": SCHEMA, "layers": [
            {"tag": "Remove", "key": "option:impratio"}]})
    assert "singleton" in str(e.value)


def test_keyframes_kept_iff_no_attach(tmp_path):
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    mug = _write_env(tmp_path, "mug", MUG_XML)
    merge_only = _compose(tmp_path, [_merge(scene)], out="merge-only")
    m = _model(merge_only)
    assert m.nkey == 1
    assert m.key("home").qpos == pytest.approx([0.3, 0.1, 0.05, 1, 0, 0, 0])

    with_attach = _compose(tmp_path, [
        _merge(scene),
        _attach(mug, "m", at="world", joint="rigid"),
    ], out="with-attach")
    assert _model(with_attach).nkey == 0


def test_pinned_layers_json_shape(tmp_path):
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    mug = _write_env(tmp_path, "mug", MUG_XML)
    report = _compose(tmp_path, [
        _merge(scene, pin="marvin/mini-scene@3"),
        _attach(mug, "m", at="world", pos=[0.3, 0.1, 0.05], joint="free"),
        {"tag": "Update", "key": "m:cup", "rgba": [1, 0, 0, 1]},
    ], name="kitchen-test")
    pinned = json.loads((report.dir / "dreamlake.layers.json").read_text())
    assert pinned["schema"] == SCHEMA
    assert pinned["substrate"] == "mujoco"
    assert pinned["entry"] == "scene.xml"
    assert pinned["name"] == "kitchen-test"
    assert pinned["layers"][0] == {"tag": "Merge", "src": "marvin/mini-scene@3"}
    assert pinned["layers"][1]["src"] == str(mug)
    assert pinned["layers"][1]["unpinned"] is True
    assert pinned["layers"][1]["key"] == "m"
    # ops without a src embed verbatim
    assert pinned["layers"][2] == {"tag": "Update", "key": "m:cup",
                                   "rgba": [1, 0, 0, 1]}
    builder = pinned["builder"]
    assert builder["engine"].startswith("dreamlake-py/")
    assert builder["mujoco"] == mujoco.__version__


def test_nested_composed_env_is_consumed_depth_0(tmp_path):
    """A materialized composed env (artifact + its dreamlake.layers.json)
    works as an ordinary layer source: the engine reads the recorded entry
    and its staged assets, never re-walking the inner stack."""
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    gripper = _write_gripper(tmp_path)
    inner = _compose(tmp_path, [
        _merge(scene),
        _attach(gripper, "grip", at="world", pos=[0.45, 0, 0.35],
                joint="free-anchored"),
    ], out="inner")
    mug = _write_env(tmp_path, "mug", MUG_XML)
    outer = _compose(tmp_path, [
        _merge(inner.dir),  # the composed env, whole
        _attach(mug, "m", at="world", pos=[0.3, -0.2, 0.05], joint="free"),
    ], out="outer")
    m = _model(outer)
    assert m.body("grip:palm") is not None  # inner attach came through
    assert m.mesh("grip:pad") is not None
    assert m.body("m:mug") is not None
    assert (outer.dir / "meshes" / "grip_pad.stl").is_file()  # re-staged


# ─── urdf layers ─────────────────────────────────────────────────────


def test_urdf_attach_free_with_unactuated_warning(tmp_path):
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    urdf = _write_env(tmp_path, "urdfbot", MINI_URDF, entry="mini.urdf")
    report = _compose(tmp_path, [
        _merge(scene),
        _attach(urdf, "bot", at="world", pos=[0.45, -0.3, 0.3], joint="free"),
    ])
    assert any("unactuated import" in w for w in report.warnings)
    m = _model(report)
    base = m.body("bot:base_link")
    assert base.jntnum[0] == 1  # exactly one freejoint added
    assert m.joint("bot:shoulder") is not None
    d = mujoco.MjData(m)
    for _ in range(400):
        mujoco.mj_step(m, d)
    z = float(d.xpos[base.id][2])
    assert 0.0 < z < 0.3, f"free urdf robot should land on the floor, z={z}"


def test_urdf_freejoint_reconciliation(tmp_path):
    """An import that already carries a root freejoint (.mjcf.urdf case)
    must not get a second one; `rigid` removes it."""
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    urdf = _write_env(tmp_path, "floaty", FLOATING_URDF, entry="floaty.urdf")
    free = _compose(tmp_path, [
        _merge(scene),
        _attach(urdf, "f", at="world", pos=[0, 0.5, 0.3], joint="free"),
    ], out="free")
    m = _model(free)
    assert m.body("f:base_link").jntnum[0] == 1  # reconciled, not doubled

    rigid = _compose(tmp_path, [
        _merge(scene),
        _attach(urdf, "f", at="world", pos=[0, 0.5, 0.3], joint="rigid"),
    ], out="rigid")
    m2 = _model(rigid)
    assert m2.body("f:base_link").jntnum[0] == 0  # imported freejoint removed


# ─── the module CLI ──────────────────────────────────────────────────


def _run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "dreamlake.envlayer", *args],
        capture_output=True, text=True, check=False)


def test_main_compose_smoke(tmp_path):
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    stack = _stack(tmp_path, [_merge(scene)])
    out = tmp_path / "out"
    proc = _run_cli("compose", str(stack), "--out", str(out))
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout.strip().splitlines()[-1])
    assert report["ok"] is True
    assert Path(report["entry"]) == out / "scene.xml"  # the entry default
    assert set(report["stats"]) == {"nbody", "njnt", "nu"}
    assert report["warnings"] == []
    assert (out / "dreamlake.layers.json").is_file()
    assert (out / "meshes").is_dir()


def test_main_failure_reports_layer_and_exits_1(tmp_path):
    stack = _stack(tmp_path, [{"tag": "Merge", "src": "ns/thing@1"}])
    proc = _run_cli("compose", str(stack), "--out", str(tmp_path / "out"))
    assert proc.returncode == 1
    report = json.loads(proc.stdout.strip().splitlines()[-1])
    assert report["ok"] is False
    assert report["layer"] == 0
    assert "resolve refs first" in report["error"]


def test_main_help(tmp_path):
    proc = _run_cli("--help")
    assert proc.returncode == 0
    assert "compose" in proc.stdout


def test_warns_on_sizeable_hidden_visual_geoms(tmp_path):
    scene = _write_env(tmp_path, "hidden-decor", """
<mujoco model="hidden-decor">
  <worldbody>
    <geom name="floor" type="plane" size="2 2 0.1"/>
    <geom name="poster" type="box" size="0.3 0.02 0.4" pos="0 1 1"
          contype="0" conaffinity="0" group="4"/>
    <geom name="marker" type="box" size="0.001 0.001 0.001" pos="0 0 0.5"
          contype="0" conaffinity="0" group="3"/>
    <geom name="colproxy" type="box" size="0.2 0.2 0.2" pos="1 0 0.2" group="3"/>
  </worldbody>
</mujoco>
""")
    stack = _stack(tmp_path, [_merge(scene)])
    report = compose_stack(stack, tmp_path / "out")
    hits = [w for w in report.warnings if "groups 3-5" in w]
    # only the poster counts: the marker is sub-centimeter, the proxy collides
    assert hits and hits[0].startswith("1 visual-only geom")


# ─── orientation opinions & runtime compatibility ────────────────────

CAMERA_SCENE_XML = """
<mujoco model="camera-scene">
  <worldbody>
    <geom name="floor" type="plane" size="2 2 0.1"/>
    <light name="key" pos="0 0 3" dir="0 0 -1"/>
    <camera name="hero" pos="1.5 -1.9 1.55" fovy="50"/>
    <body name="obj/box" pos="0.3 0 0.05">
      <geom name="obj/box_geom" type="box" size="0.05 0.05 0.05" mass="0.2"/>
    </body>
  </worldbody>
</mujoco>
"""

_XYAXES = [0.807, 0.5905, 0.0, -0.203, 0.2775, 0.939]


def _authored(xml_attr: str) -> "mujoco.MjModel":
    """CAMERA_SCENE_XML with an attribute spliced into one element -- the
    raw-MJCF authoring the opinion path must be indistinguishable from."""
    return mujoco.MjModel.from_xml_string(
        CAMERA_SCENE_XML.replace('fovy="50"', f'fovy="50" {xml_attr}'))


def test_update_camera_xyaxes_matches_raw_authoring(tmp_path):
    """An xyaxes opinion routes through mjsOrientation and compiles to the
    exact quat raw authoring produces; neighbor fields stay untouched."""
    import numpy as np

    scene = _write_env(tmp_path, "scene", CAMERA_SCENE_XML)
    report = _compose(tmp_path, [
        _merge(scene),
        {"tag": "Update", "key": "camera:hero", "xyaxes": _XYAXES},
    ])
    m = _model(report)
    raw = _authored(f'xyaxes="{" ".join(str(v) for v in _XYAXES)}"')
    # the artifact roundtrips through MuJoCo's XML writer, so compare at
    # its serialization precision, not exactly
    assert m.cam("hero").quat == pytest.approx(raw.cam("hero").quat, abs=1e-6)
    # The compiled optical axis (-z of the camera frame) points where the
    # authored x/y axes say it must (their cross product, negated).
    rot = np.zeros(9)
    mujoco.mju_quat2Mat(rot, m.cam("hero").quat)
    optical = -rot.reshape(3, 3)[:, 2]
    x, y = np.array(_XYAXES[:3]), np.array(_XYAXES[3:])
    expected = -np.cross(x, y) / np.linalg.norm(np.cross(x, y))
    assert optical == pytest.approx(expected, abs=1e-6)
    assert m.cam("hero").fovy[0] == pytest.approx(50)
    assert m.cam("hero").pos == pytest.approx([1.5, -1.9, 1.55])


def test_patch_xyaxes_agrees_with_update(tmp_path):
    """Patch and Update share one orientation path -- identical output."""
    scene = _write_env(tmp_path, "scene", CAMERA_SCENE_XML)
    patch_dir = tmp_path / "patches"
    patch_dir.mkdir()
    (patch_dir / "aim.xml").write_text(
        '<mujoco><worldbody><camera name="hero" '
        f'xyaxes="{" ".join(str(v) for v in _XYAXES)}"/>'
        "</worldbody></mujoco>")
    via_patch = _compose(
        tmp_path, [_merge(scene), {"tag": "Patch", "src": "./patches/aim.xml"}],
        out="out-patch")
    via_update = _compose(
        tmp_path,
        [_merge(scene), {"tag": "Update", "key": "camera:hero", "xyaxes": _XYAXES}],
        out="out-update")
    assert _model(via_patch).cam("hero").quat == pytest.approx(
        _model(via_update).cam("hero").quat, abs=1e-12)


def test_body_xyaxes_opinion(tmp_path):
    scene = _write_env(tmp_path, "scene", CAMERA_SCENE_XML)
    report = _compose(tmp_path, [
        _merge(scene),
        {"tag": "Update", "key": "body:obj/box", "xyaxes": [0, 1, 0, -1, 0, 0]},
    ])
    m = _model(report)
    raw = mujoco.MjModel.from_xml_string(CAMERA_SCENE_XML.replace(
        'pos="0.3 0 0.05"', 'pos="0.3 0 0.05" xyaxes="0 1 0 -1 0 0"'))
    assert m.body("obj/box").quat == pytest.approx(raw.body("obj/box").quat, abs=1e-6)
    assert m.body("obj/box").pos == pytest.approx([0.3, 0, 0.05])


def test_orientation_later_op_replaces_earlier(tmp_path):
    """quat after xyaxes wins, and xyaxes after quat wins -- later wins,
    with no stale alternate surviving a switch back to quat."""
    scene = _write_env(tmp_path, "scene", CAMERA_SCENE_XML)
    back_to_quat = _compose(tmp_path, [
        _merge(scene),
        {"tag": "Update", "key": "camera:hero", "xyaxes": _XYAXES},
        {"tag": "Update", "key": "camera:hero", "quat": [1, 0, 0, 0]},
    ], out="out-quat")
    assert _model(back_to_quat).cam("hero").quat == pytest.approx(
        [1, 0, 0, 0], abs=1e-12)
    alt_wins = _compose(tmp_path, [
        _merge(scene),
        {"tag": "Update", "key": "camera:hero", "quat": [0, 0, 0, 1]},
        {"tag": "Update", "key": "camera:hero", "xyaxes": _XYAXES},
    ], out="out-alt")
    raw = _authored(f'xyaxes="{" ".join(str(v) for v in _XYAXES)}"')
    assert _model(alt_wins).cam("hero").quat == pytest.approx(
        raw.cam("hero").quat, abs=1e-6)


def test_contradictory_orientation_props_rejected(tmp_path):
    scene = _write_env(tmp_path, "scene", CAMERA_SCENE_XML)
    with pytest.raises(ComposeError, match="contradictory orientation"):
        _compose(tmp_path, [
            _merge(scene),
            {"tag": "Update", "key": "camera:hero",
             "quat": [1, 0, 0, 0], "xyaxes": _XYAXES},
        ])
    patch_dir = tmp_path / "patches"
    patch_dir.mkdir()
    (patch_dir / "bad.xml").write_text(
        '<mujoco><worldbody><camera name="hero" euler="0 0 90" '
        'xyaxes="1 0 0 0 1 0"/></worldbody></mujoco>')
    with pytest.raises(ComposeError, match="euler, xyaxes"):
        _compose(
            tmp_path,
            [_merge(scene), {"tag": "Patch", "src": "./patches/bad.xml"}],
            out="out-patch")


def test_orientation_vector_validation(tmp_path):
    scene = _write_env(tmp_path, "scene", CAMERA_SCENE_XML)
    with pytest.raises(ComposeError, match="want exactly 6 finite numbers"):
        _compose(tmp_path, [
            _merge(scene),
            {"tag": "Update", "key": "camera:hero", "xyaxes": [1, 0, 0, 0, 1]},
        ])
    with pytest.raises(ComposeError, match="want exactly 6 finite numbers"):
        _compose(tmp_path, [
            _merge(scene),
            {"tag": "Update", "key": "camera:hero",
             "xyaxes": [float("nan"), 0, 0, 0, 1, 0]},
        ], out="out-nan")


def test_orientation_alt_unsupported_kind_still_errors(tmp_path):
    """Lights have no orientation alternates -- the strict attribute error
    stays, no silent widening."""
    scene = _write_env(tmp_path, "scene", CAMERA_SCENE_XML)
    with pytest.raises(ComposeError, match="no attribute 'xyaxes'"):
        _compose(tmp_path, [
            _merge(scene),
            {"tag": "Update", "key": "light:key", "xyaxes": _XYAXES},
        ])


def test_legacy_bool_flags_coerce_without_widening(tmp_path):
    """MJCF boolean flags accept true/false on int-typed (3.8) and
    bool-typed (3.14) bindings alike; numeric ints keep rejecting them."""
    scene = _write_env(tmp_path, "scene", CAMERA_SCENE_XML)
    report = _compose(tmp_path, [
        _merge(scene),
        {"tag": "Update", "key": "light:key",
         "active": False, "castshadow": False},
    ])
    m = _model(report)
    assert int(m.light("key").active[0]) == 0
    assert int(m.light("key").castshadow[0]) == 0
    with pytest.raises(ComposeError, match="cannot parse 'false'"):
        _compose(tmp_path, [
            _merge(scene),
            {"tag": "Update", "key": "geom:floor", "contype": "false"},
        ], out="out-contype")


def test_mujoco_version_guard(tmp_path):
    """< 3.8 must fail up front with an actionable error and no partial
    output; >= 3.8 passes the guard. (The negative arm is exercised for
    real on a MuJoCo 3.2 environment.)"""
    from dreamlake.envlayer import engine

    found = tuple(int(p) for p in mujoco.__version__.split(".")[:2])
    if found >= engine._MIN_MUJOCO:
        assert engine._require_mujoco() is mujoco
        return
    scene = _write_env(tmp_path, "scene", CAMERA_SCENE_XML)
    out = tmp_path / "out"
    with pytest.raises(ComposeError, match="mujoco >= 3.8"):
        compose_stack(_stack(tmp_path, [_merge(scene)]), out)
    assert not out.exists() or not any(out.iterdir())
