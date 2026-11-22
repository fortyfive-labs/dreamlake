"""Bundle hashing against the shared vectors (fixtures/bundles/hash-vectors.json).

The server and the CLI run the same vectors. ``fixtures/bundles/corpus`` is the
stored form of the shared corpus (dreamlake-ai/dreamlake-workspace, branch
feat/bundle-corpus, ``fixtures/bundles``), made with
``python3 fixtures/bundles/tools/corpus.py emit <dir>``. If the vectors file
and the corpus disagree, regenerate with::

    python -m dreamlake.bundles.vectors tests/fixtures/bundles/corpus \
        -o tests/fixtures/bundles/hash-vectors.json
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from dreamlake.bundles import hashing as h
from dreamlake.bundles import jsonc
from dreamlake.bundles.vectors import build, dumps

HERE = Path(__file__).parent / "fixtures" / "bundles"
VECTORS = json.loads((HERE / "hash-vectors.json").read_text(encoding="utf-8"))


def b64(s: str) -> bytes:
    return base64.b64decode(s)


def test_vectors_match_corpus():
    """The committed vectors are what the corpus gives now."""
    fresh = dumps(build(HERE / "corpus", VECTORS["source"]))
    assert fresh == (HERE / "hash-vectors.json").read_text(encoding="utf-8")


def test_vectors_cover_the_required_cases():
    names = {t["name"] for t in VECTORS["trees"]}
    assert any("empty" in n for n in names)
    prefixes = VECTORS["trees"][1]["prefixes"]
    assert prefixes["missing/"] == VECTORS["emptyHash"]
    assert "a/b/mug-*" in prefixes and "é/" in prefixes
    histories = {b["bundle"]: b["versions"] for b in VECTORS["bundles"]}
    assert max(len(v) for v in histories.values()) >= 3
    refs = {r for v in histories["fortyfive/astribot-s1"] for e in v["entries"].values() for r in e["resolvedRefs"]}
    assert any(r.startswith("#") for r in refs)
    cross = {r for b in histories.values() for v in b for e in v["entries"].values() for r in e["resolvedRefs"]}
    assert any(not r.startswith("#") for r in cross)
    hashes = {m["effectiveHash"] for m in VECTORS["manifestBytes"]["manifests"]}
    assert len(hashes) == 3
    assert VECTORS["jsonc"] and VECTORS["storedForm"]


def test_empty_hash():
    assert VECTORS["emptyHash"] == "sha256:" + hashlib.sha256(b"").hexdigest()


@pytest.mark.parametrize("case", VECTORS["files"], ids=lambda c: c["name"])
def test_file_hash(case):
    assert h.to_wire(h.file_hash(b64(case["contentBase64"]))) == case["sha256"]


@pytest.mark.parametrize("case", VECTORS["trees"], ids=lambda c: c["name"])
def test_tree(case):
    files = {f["path"]: b64(f["contentBase64"]) for f in case["files"]}
    tree = h.Tree.from_files(files)
    assert h.to_wire(tree.root_hash) == case["rootHash"]
    for d, want in case["dirs"].items():
        assert h.dir_preimage(tree.children(d)) == b64(want["preimageBase64"])
        assert h.to_wire(tree.dir_hash(d)) == want["hash"]
    for item, want in case["prefixes"].items():
        assert h.to_wire(tree.prefix_hash(item)) == want, item


def test_local_walk_ignores_empty_dirs(tmp_path):
    case = next(t for t in VECTORS["trees"] if t["emptyDirs"])
    for f in case["files"]:
        (tmp_path / f["path"]).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f["path"]).write_bytes(b64(f["contentBase64"]))
    for d in case["emptyDirs"]:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    assert h.to_wire(h.Tree.from_dir(tmp_path).root_hash) == case["rootHash"]


def test_symlink_rejected(tmp_path):
    (tmp_path / "a").write_text("a")
    (tmp_path / "b").symlink_to(tmp_path / "a")
    with pytest.raises(h.BundleHashError):
        h.Tree.from_dir(tmp_path)


def test_dir_hash_by_hand():
    """One value computed without the module, from the rule text."""
    a = hashlib.sha256(b"A").hexdigest()
    b = hashlib.sha256(b"B").hexdigest()
    sub = hashlib.sha256(f"x\0f\0{b}\n".encode()).hexdigest()
    # "Z" (0x5a) sorts before "a" (0x61); "é" (0xc3 0xa9) sorts last.
    root = hashlib.sha256(f"Z\0f\0{a}\na\0d\0{sub}\né\0f\0{a}\n".encode()).hexdigest()
    tree = h.Tree.from_files({"a/x": b"B", "Z": b"A", "é": b"A"})
    assert tree.root_hash == root
    assert tree.prefix_hash("a/") == sub
    assert tree.prefix_hash("/") == root


def test_effective_hash_by_hand():
    manifest = b'{\n  "kind": "object",\n  "uses": [\n    "m-*",\n    "/"\n  ]\n}\n'
    tree = h.Tree.from_files({"m-1": b"1", "n": b"2"})
    m1 = hashlib.sha256(b"1").hexdigest()
    m_prefix = hashlib.sha256(f"m-1\0f\0{m1}\n".encode()).hexdigest()
    refs = {"b/z@1#e": "sha256:" + "f" * 64, "a/z@10#e": "a" * 64}
    pre = (b"entry\0" + hashlib.sha256(manifest).hexdigest().encode() + b"\n"
           + f"m-*\0{m_prefix}\n/\0{tree.root_hash}\n".encode()
           + f"a/z@10#e\0{'a' * 64}\nb/z@1#e\0{'f' * 64}\n".encode())
    assert h.effective_preimage(manifest, tree, refs) == pre
    assert h.effective_hash(manifest, tree, refs) == hashlib.sha256(pre).hexdigest()


def test_manifest_bytes_vectors():
    mb = VECTORS["manifestBytes"]
    tree = h.Tree.from_files({f["path"]: b64(f["contentBase64"]) for f in mb["tree"]})
    for m in mb["manifests"]:
        raw = b64(m["contentBase64"])
        assert h.is_stored_form(raw) == m["storedForm"]
        assert h.effective_preimage(raw, tree, mb["refHashes"]) == b64(m["effectivePreimageBase64"])
        assert h.to_wire(h.effective_hash(raw, tree, mb["refHashes"])) == m["effectiveHash"]


@pytest.mark.parametrize("bundle", VECTORS["bundles"], ids=lambda b: b["bundle"])
def test_bundle_histories(bundle):
    """Recompute every version from the vector bytes alone. Refs use the
    resolvedRefs table, as a server would after it resolves them."""
    history = []
    for v in bundle["versions"]:
        files = {f["path"]: b64(VECTORS["blobs"][f["sha256"]]) for f in v["files"]}
        for f in v["files"]:
            assert h.to_wire(h.file_hash(files[f["path"]])) == f["sha256"]
        tree = h.Tree.from_files(files)
        assert h.to_wire(tree.root_hash) == v["rootHash"]
        effs = {}
        for name, e in v["entries"].items():
            raw = files[e["manifestPath"]]
            assert e["storedForm"] and h.is_stored_form(raw)
            manifest = h.load_manifest(raw)
            assert h.manifest_uses(manifest) == e["uses"]
            for item, want in e["usesHashes"].items():
                assert h.to_wire(tree.prefix_hash(item)) == want
            assert h.effective_preimage(raw, tree, e["resolvedRefs"]) == b64(e["effectivePreimageBase64"])
            effs[name] = h.effective_hash(raw, tree, e["resolvedRefs"])
            assert h.to_wire(effs[name]) == e["effectiveHash"]
        history.append((v["version"], effs))
    for v in bundle["versions"]:
        for name, e in v["entries"].items():
            assert h.displayed_version(history, v["version"], name) == e["displayedVersion"]


@pytest.mark.parametrize("case", VECTORS["displayedVersions"], ids=lambda c: c["name"])
def test_displayed_versions(case):
    hist = [(v["version"], v["entries"]) for v in case["history"]]
    for exp in case["expect"]:
        assert h.displayed_version(hist, exp["version"], exp["entry"]) == exp["displayedVersion"]


def test_displayed_version_errors():
    with pytest.raises(h.BundleHashError):
        h.displayed_version([(1, {})], 2, "e")
    with pytest.raises(h.BundleHashError):
        h.displayed_version([(1, {})], 1, "e")


@pytest.mark.parametrize("item", VECTORS["usesItems"]["valid"])
def test_uses_valid(item):
    assert h.check_uses_item(item) == item


@pytest.mark.parametrize("item", VECTORS["usesItems"]["invalid"])
def test_uses_invalid(item):
    with pytest.raises(h.BundleHashError):
        h.check_uses_item(item)


@pytest.mark.parametrize("ref", VECTORS["refs"]["valid"])
def test_refs_valid(ref):
    parsed = h.parse_ref(ref)
    assert str(parsed) == ref.removeprefix("bundle:")


@pytest.mark.parametrize("ref", VECTORS["refs"]["invalid"])
def test_refs_invalid(ref):
    with pytest.raises(h.BundleHashError):
        h.parse_ref(ref)


def test_manifest_refs_need_version_and_entry():
    with pytest.raises(h.BundleHashError):
        h.manifest_refs({"refs": ["ns/b#e"]})
    with pytest.raises(h.BundleHashError):
        h.manifest_refs({"refs": [3]})
    with pytest.raises(h.BundleHashError):
        h.manifest_uses({"uses": ["a/", 1]})
    assert h.manifest_uses({}) == ["/"]


def test_wire_form():
    hx = "A" * 64
    assert h.to_wire(hx) == "sha256:" + "a" * 64
    assert h.to_hex("sha256:" + "a" * 64) == "a" * 64
    with pytest.raises(h.BundleHashError):
        h.to_hex("sha256:xyz")


def test_bad_tree_paths():
    for bad in ["/a", "a//b", "a/../b", "a\\b", ""]:
        with pytest.raises(h.BundleHashError):
            h.Tree({bad: "a" * 64})
    with pytest.raises(h.BundleHashError):
        h.Tree({"a": "a" * 64, "a/b": "b" * 64})


@pytest.mark.parametrize("case", VECTORS["jsonc"], ids=lambda c: c["name"])
def test_jsonc_vectors(case):
    if case.get("error"):
        with pytest.raises(jsonc.JsoncError):
            jsonc.loads(case["text"])
    else:
        assert jsonc.loads(case["text"]) == case["value"]


@pytest.mark.parametrize("case", VECTORS["storedForm"], ids=lambda c: c["name"])
def test_stored_form_vectors(case):
    assert jsonc.to_stored(case["source"]).decode() == case["stored"]


def test_stored_form_rules():
    a = jsonc.to_stored('{"b": 1.0, "a": [3, 1]}')
    b = jsonc.to_stored('{\n  // c\n  "a": [3, 1], "b": 1\n}')
    assert a == b == b'{\n  "a": [\n    3,\n    1\n  ],\n  "b": 1\n}\n'
    assert jsonc.js_number(1e21) == "1e+21"
    assert jsonc.js_number(1e-7) == "1e-7"
    assert jsonc.js_number(0.1 + 0.2) == "0.30000000000000004"
    assert jsonc.js_number(123e-20) == "1.23e-18"
    assert jsonc.js_key_order({"b": 0, "10": 0, "2": 0, "01": 0, "a": 0}) == ["2", "10", "01", "a", "b"]
