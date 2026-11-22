"""Bundle client against an in-memory server (REST shapes of contract v1.1)."""

from __future__ import annotations

import json
import os

import pytest

from dreamlake.bundles import BundleError, jsonc
from dreamlake.bundles import hashing as h

from .bundle_fake_server import FakeBundleServer


@pytest.fixture
def server():
    return FakeBundleServer()


@pytest.fixture
def bundles(server, tmp_path):
    return server.bundles(tmp_path / "cache")


def files_under(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def test_resolve_latest_and_normalize(bundles, server):
    r = bundles.resolve("fortyfive/astribot-s1#wuji-right")
    assert r.version == 6 and r.displayed_version == 6
    r = bundles.resolve("fortyfive/astribot-s1@4#wuji-right")
    assert r.version == 4 and r.displayed_version == 2
    assert r.ref == "fortyfive/astribot-s1@2#wuji-right"
    assert r.effective_hash == h.to_wire(server.result("fortyfive/astribot-s1", 2).entries["wuji-right"].effective_hash)
    assert r.root_hash == h.to_wire(server.result("fortyfive/astribot-s1", 4).root_hash)
    assert r.uses == ("parts/wuji-right/",)
    whole = bundles.resolve("fortyfive/ycb-objects@latest")
    assert whole.entry is None and whole.version == 3 and whole.ref == "fortyfive/ycb-objects@3"


def test_resolve_missing(bundles):
    with pytest.raises(BundleError) as e:
        bundles.resolve("fortyfive/nope#x")
    assert e.value.status == 404
    with pytest.raises(BundleError):
        bundles.resolve("fortyfive/astribot-s1@1#nope")


def test_lock_file(bundles, server, tmp_path):
    path = bundles.lock(["fortyfive/astribot-s1@4#wuji-right"], tmp_path / "dreamlake.lock")
    data = json.loads(path.read_text())
    assert path.read_text() == jsonc.stored_form(data)
    [rec] = data["bundles"]
    assert rec["resolved"] == "fortyfive/astribot-s1@2#wuji-right"
    assert rec["version"] == 2
    assert rec["rootHash"] == h.to_wire(server.result("fortyfive/astribot-s1", 2).root_hash)
    # A second call keeps the old record and adds the new one.
    bundles.lock(["fortyfive/ycb-objects@3#mugs"], path)
    data = json.loads(path.read_text())
    assert [r["ref"] for r in data["bundles"]] == ["fortyfive/astribot-s1@4#wuji-right", "fortyfive/ycb-objects@3#mugs"]
    assert data["bundles"][1]["version"] == 3


def test_fetch_declared_prefixes_only(bundles, server):
    view = bundles.fetch("fortyfive/astribot-s1@3#wuji-right")
    assert files_under(view) == ["bundle.json", "entries/wuji-right.json",
                                 "parts/wuji-right/meshes/palm.stl", "parts/wuji-right/wuji.xml"]
    assert view.name == h.to_hex(bundles.resolve("fortyfive/astribot-s1@3#wuji-right").effective_hash)
    # Blobs outside the declared prefixes were not downloaded.
    shared = h.file_hash(server.files("fortyfive/astribot-s1", 3)["shared/s1-meshes/base.stl"])
    assert shared not in server.blob_gets
    # Read-only blobs; the view links to them.
    blob = bundles.cache_dir / "blobs" / "blake3" / h.file_hash(b"palm v2\n")
    assert not os.access(blob, os.W_OK) or os.geteuid() == 0
    record = json.loads((view.parent / f"{view.name}.json").read_text())
    assert record["lazy"] is False and record["ref"] == "fortyfive/astribot-s1@2#wuji-right"


def test_fetch_name_prefix_and_non_ascii(bundles):
    view = bundles.fetch("fortyfive/ycb-objects@3#mugs")
    assert files_under(view) == ["bundle.json", "entries/mugs.json", "objects/mug-a/mesh.obj",
                                 "objects/mug-a/model.xml", "objects/mug-b/mesh.obj",
                                 "objects/mug-b/model.xml", "objects/mug-c/model.xml"]
    view = bundles.fetch("fortyfive/ycb-objects@1#mugge")
    assert "objects/mügé-ü/model.xml" in files_under(view)
    view = bundles.fetch("fortyfive/ycb-objects@1#ghost")
    assert files_under(view) == ["bundle.json", "entries/ghost.json"]


def test_fetch_cache_is_shared_across_versions(bundles, server):
    a = bundles.fetch("fortyfive/astribot-s1@2#wuji-right")
    gets = len(server.blob_gets)
    b = bundles.fetch("fortyfive/astribot-s1@4#wuji-right")
    assert a == b
    assert len(server.blob_gets) == gets


def test_fetch_refs_closure(bundles):
    view = bundles.fetch("fortyfive/astribot-s1@2#s1-dual-wuji")
    record = json.loads((view.parent / f"{view.name}.json").read_text())
    assert sorted(record["refs"]) == ["#wuji-right", "fortyfive/ycb-objects@1#mug-a", "fortyfive/ycb-objects@1#mugs"]
    # The local ref closure is in the same view.
    assert "parts/wuji-right/wuji.xml" in files_under(view)
    assert "robots/s1-dual/dual.json" in files_under(view)
    for path in record["refs"].values():
        assert (bundles.cache_dir / "views" / os.path.basename(path)).is_dir()


def test_fetch_dest(bundles, tmp_path):
    out = bundles.fetch("fortyfive/ycb-objects@2#mug-a", dest=tmp_path / "out")
    assert out == tmp_path / "out"
    assert files_under(out) == ["bundle.json", "entries/mug-a.json", "objects/mug-a/mesh.obj",
                                "objects/mug-a/model.xml"]


def test_fetch_lazy_then_ensure(bundles, server):
    view = bundles.fetch("fortyfive/astribot-s1@1#s1-base", lazy=True)
    assert files_under(view) == ["bundle.json", "entries/s1-base.json"]
    [p] = bundles.ensure(view, "shared/s1-meshes/base.stl")
    assert p.read_bytes() == b"solid base\x00\x01\x02\n"
    with pytest.raises(BundleError):
        bundles.ensure(view, "parts/wuji-right/wuji.xml")
    full = bundles.fetch("fortyfive/astribot-s1@1#s1-base")
    assert full == view
    assert "robots/s1-base/s1.xml" in files_under(full)


def test_fetch_detects_tampered_blob(bundles, server):
    server.tamper[h.file_hash(b"palm v1\n")] = b"evil\n"
    with pytest.raises(BundleError) as e:
        bundles.fetch("fortyfive/astribot-s1@1#wuji-right")
    assert e.value.code == "integrity_failed"


def test_fetch_detects_wrong_effective_hash(bundles, server):
    original = server._entry_json

    def wrong(bundle, version, name):
        out = original(bundle, version, name)
        out["effectiveHash"] = "blake3:" + "0" * 64
        return out

    server._entry_json = wrong
    with pytest.raises(BundleError) as e:
        bundles.fetch("fortyfive/ycb-objects@1#mug-a")
    assert e.value.code == "effective_hash_mismatch"


def test_check_remote_ok(bundles):
    report = bundles.check("fortyfive/astribot-s1@3#s1-base", compile_model=False)
    assert report.ok, report.to_json()
    assert not report.root.exists()


def test_check_remote_smoke(bundles):
    report = bundles.check("fortyfive/ycb-objects@1#mug-a", smoke="test -f objects/mug-a/mesh.obj",
                           compile_model=False)
    assert report.ok and report.smoke_exit == 0
    report = bundles.check("fortyfive/ycb-objects@1#mug-a", smoke="test -f objects/mug-b/mesh.obj",
                           compile_model=False)
    assert not report.ok and report.smoke_exit != 0


def test_check_local_reports_undeclared(bundles, tmp_path):
    src = tmp_path / "src"
    (src / "entries").mkdir(parents=True)
    (src / "bundle.json").write_text('{"title": "t"}\n')
    (src / "robot").mkdir()
    (src / "meshes").mkdir()
    (src / "robot" / "r.xml").write_text(
        '<mujoco><compiler meshdir="../meshes"/><asset><mesh file="a.stl"/><mesh file="gone.stl"/></asset></mujoco>')
    (src / "meshes" / "a.stl").write_text("a")
    (src / "entries" / "r.json").write_text(
        '{\n  // source manifests may have comments\n  "kind": "robot", "uses": ["robot/"], "model": "robot/r.xml"\n}\n')
    report = bundles.check(f"{src}#r", compile_model=False)
    assert not report.ok
    assert report.missing == ["meshes/a.stl", "meshes/gone.stl"]
    assert report.undeclared == ["meshes/a.stl"]
    # The effective hash uses the stored form of the manifest.
    stored = jsonc.to_stored((src / "entries" / "r.json").read_bytes())
    tree = h.Tree.from_files({"robot/r.xml": (src / "robot" / "r.xml").read_bytes()})
    assert report.effective_hash == h.to_wire(h.effective_hash(stored, tree, {}))

    (src / "entries" / "r.json").write_text('{"kind": "robot", "uses": ["robot/", "meshes/"], "model": "robot/r.xml"}')
    report = bundles.check(f"{src}#r", compile_model=False)
    assert report.missing == ["meshes/gone.stl"] and report.undeclared == []


def test_client_property():
    from dreamlake.api._client import DreamLakeClient

    client = DreamLakeClient(dl_url="https://api.test", token="t")
    assert client.bundles.client is client


def test_unsafe_download_url(bundles, server):
    import dreamlake.bundles.client as mod

    original = mod.Bundles.presign

    def http_urls(self, *a, **k):
        return [dict(u, url=u["url"].replace("https://", "http://")) for u in original(self, *a, **k)]

    mod.Bundles.presign = http_urls
    try:
        with pytest.raises(BundleError) as e:
            bundles.fetch("fortyfive/ycb-objects@1#mug-a")
        assert e.value.code == "unsafe_download_url"
    finally:
        mod.Bundles.presign = original
