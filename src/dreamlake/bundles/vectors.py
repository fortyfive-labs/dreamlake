"""Make the shared hash test vectors (``hash-vectors.json``) from a corpus.

The corpus layout is ``<ns>/<bundle>/v<N>/<files>``: each version directory
holds the full file tree of that bundle version. Run::

    python -m dreamlake.bundles.vectors <corpus dir> -o hash-vectors.json

The server and the CLI copy the output file and test their own hashing code
against it. Every byte string in the file is base64; ``text`` fields are only
for people.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from . import hashing as h
from . import jsonc

SCHEMA = "dreamlake.bundle-hash-vectors/v1"
CONTRACT = "bundle contract v2.0 (BLAKE3-256; stored JSON manifests v1.6; effective hash over stored manifest bytes)"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _file_record(path: str, data: bytes) -> dict[str, Any]:
    out: dict[str, Any] = {"path": path, "contentBase64": _b64(data),
                           "hash": h.to_wire(h.file_hash(data)), "size": len(data)}
    try:
        out["text"] = data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    return out


def _read_tree(root: Path) -> dict[str, bytes]:
    files = {}
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            full = Path(dirpath) / name
            files[full.relative_to(root).as_posix()] = full.read_bytes()
    return dict(sorted(files.items(), key=lambda kv: kv[0].encode()))


def load_corpus(root: Path) -> dict[str, list[tuple[int, dict[str, bytes]]]]:
    """``{"ns/bundle": [(version, files), ...]}`` from a corpus directory."""
    out: dict[str, list[tuple[int, dict[str, bytes]]]] = {}
    for ns in sorted(p for p in root.iterdir() if p.is_dir()):
        for bundle in sorted(p for p in ns.iterdir() if p.is_dir()):
            versions = []
            for vdir in bundle.iterdir():
                m = re.fullmatch(r"v([1-9][0-9]*)", vdir.name)
                if m and vdir.is_dir():
                    versions.append((int(m.group(1)), _read_tree(vdir)))
            if versions:
                out[f"{ns.name}/{bundle.name}"] = sorted(versions)
    return out


class CorpusResolver:
    """Resolve refs against the computed histories of a corpus.

    Versions are computed in order and on demand, so a ref can name an
    earlier version of any bundle, also of its own bundle.
    """

    def __init__(self, corpus: dict[str, list[tuple[int, dict[str, bytes]]]]):
        self.corpus = corpus
        self.done: dict[str, list[h.VersionResult]] = {}
        self._active: set[tuple[str, int]] = set()

    def version(self, bundle: str, version: int) -> h.VersionResult:
        if bundle not in self.corpus:
            raise h.BundleHashError(f"ref to unknown bundle {bundle}")
        done = self.done.setdefault(bundle, [])
        for v, files in self.corpus[bundle]:
            if any(r.version == v for r in done):
                if v == version:
                    break
                continue
            if (bundle, v) in self._active:
                raise h.BundleHashError(f"ref cycle at {bundle}@{v}")
            self._active.add((bundle, v))
            try:
                root, entries = h.compute_version(files, self)
            finally:
                self._active.discard((bundle, v))
            history = [(r.version, {n: e.effective_hash for n, e in r.entries.items()}) for r in done]
            history.append((v, {n: e[0] for n, e in entries.items()}))
            done.append(h.VersionResult(v, root, {
                n: h.EntryResult(n, eff, h.displayed_version(history, v, n), refs)
                for n, (eff, refs) in entries.items()}))
            if v == version:
                break
        found = next((r for r in done if r.version == version), None)
        if found is None:
            raise h.BundleHashError(f"no version {bundle}@{version}")
        return found

    def history(self, bundle: str) -> list[h.VersionResult]:
        last = self.corpus[bundle][-1][0]
        self.version(bundle, last)
        return sorted(self.done[bundle], key=lambda r: r.version)

    def __call__(self, ref: h.Ref) -> tuple[str, str]:
        if not ref.pinned:
            raise h.BundleHashError(f"vectors need pinned refs, got {ref}")
        result = self.version(ref.bundle, ref.version)
        if ref.entry not in result.entries:
            raise h.BundleHashError(f"ref target does not exist: {ref}")
        entry = result.entries[ref.entry]
        return h.canonical_ref(ref.bundle, entry.displayed_version, ref.entry), entry.effective_hash


def _tree_case(name: str, files: dict[str, bytes], prefixes: list[str],
               empty_dirs: list[str] | None = None) -> dict[str, Any]:
    tree = h.Tree.from_files(files)
    dirs = sorted({"/".join(p.split("/")[:i]) for p in files for i in range(len(p.split("/")))},
                  key=lambda d: d.encode())
    return {
        "name": name,
        "files": [_file_record(p, b) for p, b in files.items()],
        "emptyDirs": empty_dirs or [],
        "rootHash": h.to_wire(tree.root_hash),
        "dirs": {d: {"hash": h.to_wire(tree.dir_hash(d)),
                     "preimageBase64": _b64(h.dir_preimage(tree.children(d)))} for d in dirs},
        "prefixes": {p: h.to_wire(tree.prefix_hash(p)) for p in prefixes},
    }


def _empty_dir_case() -> dict[str, Any]:
    # Check that a local walk ignores empty directories, then record the case.
    files = {"a/keep.txt": b"keep\n", "b.txt": b"b\n"}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for p, data in files.items():
            (root / p).parent.mkdir(parents=True, exist_ok=True)
            (root / p).write_bytes(data)
        (root / "empty").mkdir()
        (root / "a" / "nested-empty" / "deeper").mkdir(parents=True)
        walked = h.Tree.from_dir(root)
    case = _tree_case("empty directories are not part of the tree", files,
                      ["/", "empty/", "a/", "a/nested-empty/"],
                      empty_dirs=["empty/", "a/nested-empty/deeper/"])
    assert walked.root_hash == h.Tree.from_files(files).root_hash
    return case


def build(corpus_root: Path, source: str) -> dict[str, Any]:
    corpus = load_corpus(corpus_root)
    resolver = CorpusResolver(corpus)
    bundles = []
    blobs: dict[str, str] = {}
    for bundle, versions in corpus.items():
        history = resolver.history(bundle)
        out_versions = []
        for (version, files), result in zip(versions, history):
            tree = h.Tree.from_files(files)
            entries = {}
            for data in files.values():
                blobs.setdefault(h.to_wire(h.file_hash(data)), _b64(data))
            for name, entry in result.entries.items():
                raw = files[h.manifest_path(name)]
                manifest = h.load_manifest(raw)
                entries[name] = {
                    "manifestPath": h.manifest_path(name),
                    "manifestHash": h.to_wire(h.file_hash(raw)),
                    "storedForm": h.is_stored_form(raw),
                    "uses": h.manifest_uses(manifest),
                    "usesHashes": {u: h.to_wire(tree.prefix_hash(u)) for u in h.manifest_uses(manifest)},
                    "refs": manifest.get("refs") or [],
                    "resolvedRefs": {r: h.to_wire(x) for r, x in sorted(entry.refs.items())},
                    "effectivePreimageBase64": _b64(h.effective_preimage(raw, tree, entry.refs)),
                    "effectiveHash": h.to_wire(entry.effective_hash),
                    "displayedVersion": entry.displayed_version,
                }
            out_versions.append({
                "version": version,
                "files": [{"path": p, "hash": h.to_wire(h.file_hash(b)), "size": len(b)}
                          for p, b in files.items()],
                "rootHash": h.to_wire(result.root_hash),
                "entries": entries,
            })
        bundles.append({"bundle": bundle, "versions": out_versions})

    files_cases = [
        {"name": n, "contentBase64": _b64(b), "hash": h.to_wire(h.file_hash(b)), "size": len(b)}
        for n, b in [("empty file", b""), ("ascii", b"hello\n"), ("binary", bytes(range(256))),
                     ("utf-8 text", "mügé ü 杯子\n".encode())]
    ]
    nested = {
        "a/b/c/deep.txt": b"deep\n",
        "a/b/mug-1/model.xml": b"<mujoco/>\n",
        "a/b/mug-2.txt": b"mug file\n",
        "a/b/mugs/x.txt": b"x\n",
        "a/b/plate/y.txt": b"y\n",
        "a/z.txt": b"z\n",
        "b": b"file at root\n",
        "B": b"upper case sorts first\n",
        "é/ü.txt": b"non-ASCII path\n",
        "e/f.txt": b"e\n",
        "杯子/x": b"cjk\n",
    }
    trees = [
        _tree_case("single file", {"only.txt": b"only\n"}, ["/", "only*", "nothing/"]),
        _tree_case("nested directories, sort order, name prefixes", nested, [
            "/", "a/", "a/b/", "a/b/c/", "a/b/mug-*", "a/b/mug*", "a/b/*", "*", "é/", "杯子/",
            "a/b/zzz-*", "missing/", "a/b/c/deep.txt/", "b/",
        ]),
        _empty_dir_case(),
    ]
    uses_items = {
        "valid": ["/", "a/", "a/b/", "a/b/mug-*", "mug-*", "*", "é/ü/"],
        "invalid": ["", "a", "a/b", "/a/", "a/**", "**", "a/*/b/", "a/*b", "a/?/", "a/[x]/",
                    "../a/", "a/../b/", "./a/", "a//b/", "a\\b/"],
    }
    refs = {
        "valid": ["ns/b", "ns/b@1", "ns/b@12#e", "ns/b@latest#e", "ns/b#e", "bundle:ns/b@3#e"],
        "invalid": ["ns", "ns/b@0#e", "ns/b@01#e", "ns/b@1.2", "ns/b/c@1#e", "ns/b@1#e/f", "ns/b@1#", " ns/b@1"],
    }
    displayed = [
        {"name": "gap in stored versions",
         "history": [{"version": 1, "entries": {"e": "blake3:" + "a" * 64}},
                     {"version": 3, "entries": {"e": "blake3:" + "a" * 64}},
                     {"version": 7, "entries": {"e": "blake3:" + "a" * 64, "f": "blake3:" + "b" * 64}}],
         "expect": [{"version": 7, "entry": "e", "displayedVersion": 1},
                    {"version": 7, "entry": "f", "displayedVersion": 7}]},
        {"name": "change back to an old hash starts a new run",
         "history": [{"version": 1, "entries": {"e": "blake3:" + "a" * 64}},
                     {"version": 2, "entries": {"e": "blake3:" + "b" * 64}},
                     {"version": 3, "entries": {"e": "blake3:" + "a" * 64}},
                     {"version": 4, "entries": {"e": "blake3:" + "a" * 64}}],
         "expect": [{"version": 4, "entry": "e", "displayedVersion": 3},
                    {"version": 2, "entry": "e", "displayedVersion": 2},
                    {"version": 1, "entry": "e", "displayedVersion": 1}]},
    ]
    for case in displayed:
        hist = [(v["version"], v["entries"]) for v in case["history"]]
        for exp in case["expect"]:
            assert h.displayed_version(hist, exp["version"], exp["entry"]) == exp["displayedVersion"]

    # The effective hash is over the manifest bytes: the same value in three
    # byte forms gives three hashes. Only the stored form is accepted by the
    # server; the other forms are here to pin the formula.
    mtree = {"objects/mug-a/model.xml": b"<mujoco/>\n", "objects/mug-b/model.xml": b"<mujoco/>\n"}
    value = {"kind": "object", "uses": ["objects/mug-*", "missing/"], "title": "Mugs"}
    forms = [jsonc.stored_form(value).encode(),
             b'{"kind":"object","uses":["objects/mug-*","missing/"],"title":"Mugs"}\n',
             b'{\n  // a comment\n  "kind": "object", "uses": ["objects/mug-*", "missing/"], "title": "Mugs"\n}\n']
    mt = h.Tree.from_files(mtree)
    manifest_bytes = {
        "tree": [_file_record(p, b) for p, b in mtree.items()],
        "refHashes": {"fortyfive/zz@2#b": "blake3:" + "1" * 64, "fortyfive/aa@10#a": "blake3:" + "2" * 64},
        "manifests": [],
    }
    for raw in forms:
        manifest_bytes["manifests"].append({
            "contentBase64": _b64(raw), "text": raw.decode(), "storedForm": h.is_stored_form(raw),
            "manifestHash": h.to_wire(h.file_hash(raw)),
            "effectivePreimageBase64": _b64(h.effective_preimage(raw, mt, manifest_bytes["refHashes"])),
            "effectiveHash": h.to_wire(h.effective_hash(raw, mt, manifest_bytes["refHashes"])),
        })
    jsonc_cases = []
    for case in jsonc.PARITY_CASES:
        out = {"name": case["name"], "text": case["text"]}
        if case.get("error"):
            out["error"] = True
        else:
            out["value"] = jsonc.loads(case["text"])
        jsonc_cases.append(out)
    stored_cases = [{"name": c["name"], "source": c["source"], "stored": jsonc.to_stored(c["source"]).decode()}
                    for c in jsonc.STORED_FORM_CASES]

    return {
        "schema": SCHEMA,
        "contract": CONTRACT,
        "source": source,
        "emptyHash": h.to_wire(h.EMPTY_HEX),
        "notes": [
            "Hashes are blake3:<lowercase hex> (BLAKE3-256). Preimages are base64.",
            "dir line = <name> NUL <f|d> NUL <child hex> LF, children sorted by UTF-8 bytes of name.",
            "A tree is a set of file paths; empty directories (emptyDirs) are not in it.",
            "A dir prefix that is missing or names a file hashes to emptyHash.",
            "effective = BLAKE3('entry' NUL <manifest file hex> LF + uses lines in declared order "
            "+ ref lines sorted by UTF-8 bytes of the canonical ref ns/b@W#e, W = displayed version).",
            "Ref lines use resolvedRefs; the server resolves refs against the referenced bundle history. "
            "A local ref '#e' (same bundle version) has the canonical string '#e'.",
            "bundles[].versions[].files list path, hash and size; the bytes are in blobs (hash -> base64).",
            "jsonc: source-manifest parse parity (value or error). storedForm: source -> stored bytes "
            "(JSON.stringify(sortKeys(v), null, 2) + LF); Python output, check with Node.",
            "manifestBytes: one value in three byte forms gives three effective hashes; only storedForm "
            "bytes are accepted in a bundle version.",
        ],
        "files": files_cases,
        "trees": trees,
        "usesItems": uses_items,
        "refs": refs,
        "displayedVersions": displayed,
        "manifestBytes": manifest_bytes,
        "jsonc": jsonc_cases,
        "storedForm": stored_cases,
        "bundles": bundles,
        "blobs": dict(sorted(blobs.items())),
    }


def dumps(vectors: dict[str, Any]) -> str:
    return json.dumps(vectors, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m dreamlake.bundles.vectors")
    parser.add_argument("corpus", type=Path)
    parser.add_argument("-o", "--out", type=Path, required=True)
    parser.add_argument("--source", default="dreamlake SDK tests/fixtures/bundles/corpus")
    args = parser.parse_args(argv)
    args.out.write_text(dumps(build(args.corpus, args.source)), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
