"""Bundle client against the shared corpus (stored form) behind the fake server."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from dreamlake.bundles import hashing as h
from dreamlake.bundles import jsonc

from .bundle_fake_server import SHARED_CORPUS, FakeBundleServer


@pytest.fixture
def corpus_bundles(tmp_path):
    return FakeBundleServer(SHARED_CORPUS, page_size=50).bundles(tmp_path / "cache")


@pytest.fixture
def bundles(tmp_path):
    return FakeBundleServer().bundles(tmp_path / "cache")


def files_under(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def closure(view: Path) -> dict[str, list[str]]:
    """{bundle@version: [paths without manifests]} over a view and its refs."""
    out: dict[str, set] = {}
    todo = [view]
    while todo:
        v = todo.pop()
        record = json.loads((v.parent / f"{v.name}.json").read_text())
        key = f"{record['bundle']}@{record['version']}"
        paths = {f["path"] for f in record["files"]
                 if f["path"] != "bundle.json" and not f["path"].startswith("entries/")}
        out.setdefault(key, set()).update(paths)
        todo.extend(Path(p) for p in record["refs"].values())
    return {k: sorted(v) for k, v in out.items() if v}


def test_fetch_closure_matches_corpus_expectation(corpus_bundles):
    """The ``fetch`` expectation of corpus/fortyfive/rizon4s-claw-desk/expected.json."""
    view = corpus_bundles.fetch("fortyfive/rizon4s-claw-desk@1#rizon4s-claw-pick-mug-gen")
    assert closure(view) == {
        "fortyfive/rizon4s-claw-desk@1": [
            "objects/mug-ace-coffee-mug-kristen-16oz/model.obj",
            "objects/mug-ace-coffee-mug-kristen-16oz/model.xml",
            "objects/mug-ycb-025-mug/model.obj",
            "objects/mug-ycb-025-mug/model.xml",
        ],
        "fortyfive/rizon-desk@1": ["rizon4s-desk/desk.xml"],
        "fortyfive/flexiv@1": [
            "rizon4s/LICENSE",
            "rizon4s/assets/link0/link0.obj",
            "rizon4s/assets/link0/material.mtl",
            "rizon4s/assets/link3r/link3r_0.obj",
            "rizon4s/assets/link3r/link3r_1.obj",
            "rizon4s/flexiv_rizon4s.xml",
        ],
        "fortyfive/flexiv@2": [
            "claw/collision/Adapter_Flange_V1_0.stl",
            "claw/hand.xml",
            "claw/meshes/Adapter_Flange_V1.stl",
            "claw/meshes/B3B_EH.stl",
            "claw/meshes/DC15_A01_CASE_B_DUMMY.stl",
        ],
    }


def test_normalized_refs(corpus_bundles):
    assert corpus_bundles.resolve("fortyfive/astribot-s1@8#s1-dual-wuji").ref == "fortyfive/astribot-s1@7#s1-dual-wuji"
    assert corpus_bundles.resolve("fortyfive/astribot-s1@5#wuji-right").ref == "fortyfive/astribot-s1@3#wuji-right"


@pytest.mark.parametrize("ref", [
    "fortyfive/flexiv@3#rizon4s-wuji-right",   # local and cross-bundle refs
    "fortyfive/astribot-s1@9#s1-dual-wuji",    # local refs
    "fortyfive/s1-tabletop@1",                 # whole bundle
])
def test_fetch_verifies_hashes(corpus_bundles, ref):
    assert corpus_bundles.fetch(ref).is_dir()


def test_check_local_with_local_refs(bundles, tmp_path):
    src = tmp_path / "src"
    (src / "entries").mkdir(parents=True)
    (src / "meshes").mkdir()
    (src / "other").mkdir()
    (src / "bundle.json").write_text('{"title": "t"}')
    (src / "meshes" / "m.stl").write_text("m")
    (src / "other" / "x.txt").write_text("x")
    (src / "entries" / "b.json").write_text('{"kind": "part", "uses": ["meshes/"]}')
    (src / "entries" / "a.json").write_text('{"kind": "robot", "uses": [], "refs": ["#b"]}  // comment')
    report = bundles.check(f"{src}#a", keep=True, compile_model=False)
    try:
        assert report.ok, report.to_json()
        assert files_under(report.root) == ["bundle.json", "entries/a.json", "entries/b.json", "meshes/m.stl"]
        files = {p: (src / p).read_bytes() for p in ["meshes/m.stl", "other/x.txt"]}
        files.update({p: jsonc.to_stored((src / p).read_bytes())
                      for p in ["bundle.json", "entries/a.json", "entries/b.json"]})
        _root, entries = h.compute_version(files)
        assert report.effective_hash == h.to_wire(entries["a"][0])
    finally:
        shutil.rmtree(report.root)
