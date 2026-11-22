"""Write the small bundle corpus used by the SDK tests and the hash vectors.

Usage: ``python scripts/make_bundle_fixtures.py tests/fixtures/bundles/client-corpus``

The layout is ``<ns>/<bundle>/v<N>/<files>``. Each version directory is the
full file tree of that bundle version. Replace this corpus with the shared
corpus (dreamlake-workspace ``fixtures/bundles/``) when it is available.
"""

import json
import shutil
import sys
from pathlib import Path


def fmt(value):
    # The stored form (contract v1.6). Equal to JSON.stringify(sortKeys(v),
    # null, 2) + "\n" for these ASCII keys without floats.
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write(base: Path, files: dict) -> None:
    if base.exists():
        shutil.rmtree(base)
    for path, content in files.items():
        target = base / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode() if isinstance(content, str) else content)


def main(root: Path) -> None:
    # ---- fortyfive/ycb-objects: name prefix, non-ASCII path, missing prefix
    ycb = root / "fortyfive" / "ycb-objects"
    bundle = fmt({"title": "YCB objects (test)", "description": "Small object set.",
                  "tags": ["test"], "license": "CC-BY-4.0", "attribution": "fixture"})

    def mug(n):
        return f'<mujoco model="mug-{n}"><asset><mesh file="mesh.obj"/></asset></mujoco>\n'

    v1 = {
        "bundle.json": bundle,
        "objects/mug-a/model.xml": mug("a"),
        "objects/mug-a/mesh.obj": "v 0 0 0\n",
        "objects/mug-b/model.xml": mug("b"),
        "objects/mug-b/mesh.obj": "v 0 0 1\n",
        "objects/bowl-1/model.xml": '<mujoco model="bowl"/>\n',
        "objects/mügé-ü/model.xml": '<mujoco model="mügé"/>\n',
        "objects/mugs.txt": "a file whose name starts with mug but not mug-\n",
        "entries/mug-a.json": fmt({"kind": "object", "title": "Mug A", "uses": ["objects/mug-a/"],
                                   "model": "objects/mug-a/model.xml"}),
        "entries/mugs.json": fmt({"kind": "object", "title": "All mugs", "uses": ["objects/mug-*"]}),
        "entries/mugge.json": fmt({"kind": "object", "title": "Non-ASCII path",
                                   "uses": ["objects/mügé-ü/"], "model": "objects/mügé-ü/model.xml"}),
        "entries/ghost.json": fmt({"kind": "object", "title": "Missing prefixes",
                                   "uses": ["missing/dir/", "objects/zzz-*"]}),
        "entries/all.json": fmt({"kind": "object", "title": "Whole bundle (default uses)"}),
    }
    v2 = dict(v1)
    v2["objects/bowl-1/model.xml"] = '<mujoco model="bowl" version="2"/>\n'
    v3 = dict(v2)
    v3["objects/mug-c/model.xml"] = mug("c")
    for n, files in [(1, v1), (2, v2), (3, v3)]:
        write(ycb / f"v{n}", files)

    # ---- fortyfive/astribot-s1: refs, formatting change, entry gap
    s1 = root / "fortyfive" / "astribot-s1"
    bundle = fmt({"title": "Astribot S1 (test)", "description": "Robot and parts.",
                  "license": "proprietary"})
    base = {"kind": "robot", "title": "S1 base", "uses": ["shared/s1-meshes/", "robots/s1-base/"],
            "model": "robots/s1-base/s1.xml", "slots": {"right_wrist": "body:right_link7"}}
    c1 = {
        "bundle.json": bundle,
        "shared/s1-meshes/base.stl": b"solid base\x00\x01\x02\n",
        "shared/s1-meshes/link1.stl": b"solid link1 v1\n",
        "robots/s1-base/s1.xml":
            '<mujoco model="s1"><compiler meshdir="../../shared/s1-meshes"/>'
            '<asset><mesh file="base.stl"/><mesh file="link1.stl"/></asset></mujoco>\n',
        "parts/wuji-right/wuji.xml":
            '<mujoco model="wuji"><asset><mesh file="meshes/palm.stl"/></asset></mujoco>\n',
        "parts/wuji-right/meshes/palm.stl": "palm v1\n",
        "entries/s1-base.json": fmt(base),
        "entries/wuji-right.json": fmt({"kind": "part", "title": "Wuji right hand",
                                        "uses": ["parts/wuji-right/"], "model": "parts/wuji-right/wuji.xml",
                                        "mount": {"site": "attach"}}),
    }
    c2 = dict(c1)
    c2["parts/wuji-right/meshes/palm.stl"] = "palm v2\n"
    c2["robots/s1-dual/dual.json"] = fmt({"note": "composition only"})
    c2["entries/s1-dual-wuji.json"] = fmt({
        "kind": "robot", "title": "S1 with a mug set", "uses": ["robots/s1-dual/"],
        # Declared order is not the sorted order. @2#mugs normalizes to @1#mugs.
        "refs": ["fortyfive/ycb-objects@2#mugs", "fortyfive/ycb-objects@1#mug-a", "#wuji-right"],
    })
    c3 = dict(c2)
    c3["shared/s1-meshes/link1.stl"] = b"solid link1 v3\n"
    c4 = dict(c3)
    # A manifest value change (stored manifests have one form, so only a
    # value change gives new manifest bytes).
    c4["entries/s1-base.json"] = fmt(dict(base, description="Torso, arms and head."))
    c5 = dict(c4)
    del c5["entries/wuji-right.json"]
    # The local ref to the removed entry goes too.
    dual = json.loads(c5["entries/s1-dual-wuji.json"])
    dual["refs"] = [r for r in dual["refs"] if not r.startswith("#")]
    c5["entries/s1-dual-wuji.json"] = fmt(dual)
    c6 = dict(c5)
    c6["entries/wuji-right.json"] = c4["entries/wuji-right.json"]
    for n, files in [(1, c1), (2, c2), (3, c3), (4, c4), (5, c5), (6, c6)]:
        write(s1 / f"v{n}", files)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
