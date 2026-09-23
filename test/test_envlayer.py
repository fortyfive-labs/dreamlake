"""Env-layer composition engine tests (RFC 0007 P0.5 checklist).

Self-contained: every layer -- mini scene, mini robot, nameless-mesh
gripper (a programmatic one-triangle binary STL), minimal URDFs, sparse
override docs -- is written into tmp_path by the fixtures below. Requires
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

SCHEMA = "dreamlake.env-layers/v2"


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
    doc = {"schema": SCHEMA, "entry": "scene.xml", **top, "layers": layers}
    path = tmp_path / "dreamlake.layers.json"
    path.write_text(json.dumps(doc, indent=2))
    return path


def _merge(path: Path, **src) -> dict:
    return {"source": {"path": str(path), **src}, "compose": {"mode": "merge"}}


def _compose(tmp_path: Path, layers: list[dict], out: str = "out", **top):
    stack = _stack(tmp_path, layers, **top)
    return compose_stack(stack, tmp_path / out)


def _model(report) -> "mujoco.MjModel":
    """The written artifact must recompile FROM DISK (the v1 discipline)."""
    return mujoco.MjModel.from_xml_path(str(report.entry))


# ─── merge ───────────────────────────────────────────────────────────


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


# ─── attach ──────────────────────────────────────────────────────────


def test_attach_free_anchored_parity(tmp_path):
    """v1 parity: names prefixed on bodies/joints/actuators, and the
    mocap-anchor weld holds the base over 200 steps."""
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    robot = _write_env(tmp_path, "robot", ROBOT_XML)
    report = _compose(tmp_path, [
        _merge(scene),
        {"source": {"path": str(robot)},
         "compose": {"mode": "attach", "prefix": "rob:", "at": "world",
                     "pos": [0.45, -0.12, 0.3], "joint": "free-anchored"}},
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
        {"source": {"path": str(mug)},
         "compose": {"mode": "attach", "prefix": "m:", "at": "body:fixture/shelf",
                     "pos": [0, 0, 0.05], "joint": "rigid"}},
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
        {"source": {"path": str(mug)},
         "compose": {"mode": "attach", "prefix": "m:", "at": "site:mount",
                     "joint": "rigid"}},
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
            {"source": {"path": str(mug)},
             "compose": {"mode": "attach", "prefix": "m:", "at": "site:nope"}},
        ])
    assert e.value.layer == 1 and "no such site" in str(e.value)


def test_instances_are_independent_and_overridable_individually(tmp_path):
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    mug = _write_env(tmp_path, "mug", MUG_XML)
    night = tmp_path / "tweak.xml"
    night.write_text("""
<mujoco>
  <worldbody><geom name="mug2:cup" rgba="1 0 0 1"/></worldbody>
</mujoco>
""")
    report = _compose(tmp_path, [
        _merge(scene),
        {"source": {"path": str(mug)},
         "compose": {"mode": "attach", "at": "world", "joint": "free",
                     "instances": [
                         {"prefix": "mug1:", "pos": [0.30, 0.10, 0.05]},
                         {"prefix": "mug2:", "pos": [0.42, -0.08, 0.05]},
                         {"prefix": "mug3:", "pos": [0.54, 0.10, 0.05]},
                     ]}},
        {"source": {"file": "tweak.xml"}, "compose": {"mode": "override"}},
    ])
    m = _model(report)
    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    for prefix, pos in (("mug1:", [0.30, 0.10, 0.05]),
                        ("mug2:", [0.42, -0.08, 0.05]),
                        ("mug3:", [0.54, 0.10, 0.05])):
        body = m.body(prefix + "mug")
        assert body.jntnum[0] == 1  # each instance fully independent (free)
        assert d.xpos[body.id] == pytest.approx(pos)
    assert m.geom("mug2:cup").rgba == pytest.approx([1, 0, 0, 1])
    assert m.geom("mug1:cup").rgba == pytest.approx([0.9, 0.9, 0.9, 1])


def test_duplicate_attach_prefix_rejected(tmp_path):
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    mug = _write_env(tmp_path, "mug", MUG_XML)
    layer = {"source": {"path": str(mug)},
             "compose": {"mode": "attach", "prefix": "m:", "at": "world"}}
    with pytest.raises(ComposeError) as e:
        _compose(tmp_path, [_merge(scene), layer, dict(layer)])
    assert e.value.layer == 2 and "duplicate attach prefix" in str(e.value)


def test_nameless_mesh_gets_pinned_and_prefixed(tmp_path):
    """The 2F-85 trap: a name-less mesh must keep its derived name (stem)
    while its FILE is renamed under the layer prefix -- otherwise the geom
    reference orphans at compile."""
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    gripper = _write_gripper(tmp_path)
    report = _compose(tmp_path, [
        _merge(scene),
        {"source": {"path": str(gripper), "pin": {"env": "marvin/mini-gripper", "version": 1}},
         "compose": {"mode": "attach", "prefix": "grip:", "at": "world",
                     "pos": [0.45, 0, 0.35], "joint": "free-anchored"}},
    ])
    m = _model(report)  # geom->mesh reference survived staging + attach
    assert m.mesh("grip:pad") is not None
    staged = sorted(p.name for p in (report.dir / "meshes").iterdir())
    assert staged == ["grip_pad.stl"]


# ─── override ────────────────────────────────────────────────────────


def test_override_attribute_coverage(tmp_path):
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    robot = _write_env(tmp_path, "robot", ROBOT_XML)
    patch = tmp_path / "patch.xml"
    patch.write_text("""
<mujoco>
  <!-- sparse MJCF; comments with -- dashes are stripped before parsing -->
  <option impratio="20"/>
  <asset><material name="gridmat" rgba="0.5 0.4 0.3 1"/></asset>
  <worldbody>
    <light name="key" diffuse="0.2 0.2 0.3" active="false"/>
    <body name="obj/mug" pos="0.4 0 0.05" quat="0 0 0 1"/>
    <geom name="rob:link1_geom" rgba="1 0 0 1" friction="0.5"/>
    <geom name="floor" size="3 3 0.1" contype="2" conaffinity="2"/>
    <joint name="rob:shoulder" damping="2.5" armature="0.01" range="-1 1" stiffness="3" frictionloss="0.2"/>
  </worldbody>
</mujoco>
""")
    report = _compose(tmp_path, [
        _merge(scene),
        {"source": {"path": str(robot)},
         "compose": {"mode": "attach", "prefix": "rob:", "at": "world",
                     "pos": [0.45, -0.12, 0.3], "joint": "free-anchored"}},
        {"source": {"file": "patch.xml"}, "compose": {"mode": "override"}},
    ])
    m = _model(report)
    assert m.opt.impratio == 20  # attach layer said 10; later wins
    assert m.opt.cone == mujoco.mjtCone.mjCONE_ELLIPTIC  # unstated: kept
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


def test_override_strict_miss_carries_layer_index(tmp_path):
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    patch = tmp_path / "typo.xml"
    patch.write_text('<mujoco><worldbody><geom name="floof" rgba="1 0 0 1"/></worldbody></mujoco>')
    with pytest.raises(ComposeError) as e:
        _compose(tmp_path, [
            _merge(scene),
            {"source": {"file": "typo.xml"}, "compose": {"mode": "override"}},
        ])
    assert e.value.layer == 1
    assert "no geom named 'floof'" in str(e.value)


def test_delete_drops_subtree_and_is_reversible(tmp_path):
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    override = {"source": {"file": "empty.xml"},
                "compose": {"mode": "override",
                            "delete": [{"elem": "body", "name": "fixture/shelf"}]}}
    (tmp_path / "empty.xml").write_text("<mujoco/>")

    report = _compose(tmp_path, [_merge(scene), override], out="deleted")
    m = _model(report)
    with pytest.raises(KeyError):
        m.body("fixture/shelf")
    with pytest.raises(KeyError):
        m.geom("fixture/shelf_geom")  # the subtree went with it

    # non-destructive at the STACK level: drop the layer, the body is back
    report2 = _compose(tmp_path, [_merge(scene)], out="restored")
    assert _model(report2).body("fixture/shelf") is not None


def test_delete_missing_name_is_error(tmp_path):
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    (tmp_path / "empty.xml").write_text("<mujoco/>")
    with pytest.raises(ComposeError) as e:
        _compose(tmp_path, [
            _merge(scene),
            {"source": {"file": "empty.xml"},
             "compose": {"mode": "override",
                         "delete": [{"elem": "body", "name": "fixture/plant"}]}},
        ])
    assert e.value.layer == 1 and "fixture/plant" in str(e.value)


# ─── stack schema + resolution ───────────────────────────────────────


def test_env_source_rejected_with_guidance(tmp_path):
    with pytest.raises(ComposeError) as e:
        _compose(tmp_path, [
            {"source": {"env": "fortyfive/scene-berry@3"}, "compose": {"mode": "merge"}},
        ])
    assert e.value.layer == 0
    assert "resolve refs first" in str(e.value)


def test_stack_validation_errors(tmp_path):
    with pytest.raises(ComposeError, match="schema"):
        load_stack({"schema": "dreamlake.env-layers/v1", "layers": []})
    with pytest.raises(ComposeError) as e:
        load_stack({"schema": SCHEMA, "layers": [
            {"source": {"path": "/x"}, "compose": {"mode": "merge", "prefix": "a:"}}]})
    assert e.value.layer == 0 and "unknown compose keys" in str(e.value)
    with pytest.raises(ComposeError) as e:
        load_stack({"schema": SCHEMA, "layers": [
            {"source": {"path": "/x"},
             "compose": {"mode": "attach", "prefix": "a:", "joint": "loose"}}]})
    assert e.value.layer == 0 and "joint" in str(e.value)
    with pytest.raises(ComposeError) as e:
        load_stack({"schema": SCHEMA, "layers": [
            {"source": {"path": "/x"},
             "compose": {"mode": "attach", "prefix": "a:",
                         "instances": [{"prefix": "b:"}]}}]})
    assert "instances" in str(e.value)


def test_keyframes_kept_iff_merge_only(tmp_path):
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    mug = _write_env(tmp_path, "mug", MUG_XML)
    merge_only = _compose(tmp_path, [_merge(scene)], out="merge-only")
    m = _model(merge_only)
    assert m.nkey == 1
    assert m.key("home").qpos == pytest.approx([0.3, 0.1, 0.05, 1, 0, 0, 0])

    with_attach = _compose(tmp_path, [
        _merge(scene),
        {"source": {"path": str(mug)},
         "compose": {"mode": "attach", "prefix": "m:", "at": "world"}},
    ], out="with-attach")
    assert _model(with_attach).nkey == 0


def test_pinned_layers_json_shape(tmp_path):
    scene = _write_env(tmp_path, "scene", SCENE_XML)
    mug = _write_env(tmp_path, "mug", MUG_XML)
    report = _compose(tmp_path, [
        _merge(scene, pin={"env": "marvin/mini-scene", "version": 3}),
        {"source": {"path": str(mug)},
         "compose": {"mode": "attach", "prefix": "m:", "at": "world",
                     "pos": [0.3, 0.1, 0.05], "joint": "free"}},
    ], name="kitchen-test")
    pinned = json.loads((report.dir / "dreamlake.layers.json").read_text())
    assert pinned["schema"] == SCHEMA
    assert pinned["substrate"] == "mujoco"
    assert pinned["entry"] == "scene.xml"
    assert pinned["name"] == "kitchen-test"
    assert pinned["layers"][0]["source"] == {"env": "marvin/mini-scene@3"}
    assert pinned["layers"][1]["source"] == {"path": str(mug), "unpinned": True}
    assert pinned["layers"][1]["compose"]["prefix"] == "m:"
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
        {"source": {"path": str(gripper)},
         "compose": {"mode": "attach", "prefix": "grip:", "at": "world",
                     "pos": [0.45, 0, 0.35], "joint": "free-anchored"}},
    ], out="inner")
    mug = _write_env(tmp_path, "mug", MUG_XML)
    outer = _compose(tmp_path, [
        _merge(inner.dir),  # the composed env, whole
        {"source": {"path": str(mug)},
         "compose": {"mode": "attach", "prefix": "m:", "at": "world",
                     "pos": [0.3, -0.2, 0.05], "joint": "free"}},
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
        {"source": {"path": str(urdf)},
         "compose": {"mode": "attach", "prefix": "bot:", "at": "world",
                     "pos": [0.45, -0.3, 0.3], "joint": "free"}},
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
        {"source": {"path": str(urdf)},
         "compose": {"mode": "attach", "prefix": "f:", "at": "world",
                     "pos": [0, 0.5, 0.3], "joint": "free"}},
    ], out="free")
    m = _model(free)
    assert m.body("f:base_link").jntnum[0] == 1  # reconciled, not doubled

    rigid = _compose(tmp_path, [
        _merge(scene),
        {"source": {"path": str(urdf)},
         "compose": {"mode": "attach", "prefix": "f:", "at": "world",
                     "pos": [0, 0.5, 0.3], "joint": "rigid"}},
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
    assert Path(report["entry"]) == out / "scene.xml"
    assert set(report["stats"]) == {"nbody", "njnt", "nu"}
    assert report["warnings"] == []
    assert (out / "dreamlake.layers.json").is_file()
    assert (out / "meshes").is_dir()


def test_main_failure_reports_layer_and_exits_1(tmp_path):
    stack = _stack(tmp_path, [
        {"source": {"env": "ns/thing@1"}, "compose": {"mode": "merge"}}])
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
