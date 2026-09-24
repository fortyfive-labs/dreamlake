"""Tests for dreamlake.assets_tools (manifest + importers + thumbnails).

Self-contained: importer smokes run on tiny synthetic repos built in
tmp_path (a minimal one-geom MJCF + a dummy PNG) -- no dependency on a
real asset_library checkout. Render tests skip cleanly when mujoco is
not installed (it is only a tooling extra).
"""

import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from dreamlake.assets_tools import embed as embed_mod
from dreamlake.assets_tools import (
    SCHEMA,
    Asset,
    AssetFile,
    LibraryInfo,
    LibraryManifest,
    ManifestError,
    file_entry,
    hash_file,
    load_manifest,
    render_thumbnail,
    save_thumbnail,
    validate,
    write_manifest,
)
from dreamlake.assets_tools.importers import menagerie
from dreamlake.assets_tools.importers import mujoco_scanned_objects as gso

MINIMAL_MJCF = (
    '<mujoco><worldbody><geom type="sphere" size="0.1"/></worldbody>'
    "</mujoco>"
)

APACHE_SNIPPET = """\
                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/
"""


def _png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (2, 2), (200, 30, 30)).save(path)


def _asset_file(root: Path, rel: str, content: bytes = b"x") -> AssetFile:
    full = root / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(content)
    return file_entry(root, rel)


def _manifest(tmp_path: Path) -> LibraryManifest:
    shared = _asset_file(tmp_path, "shared/common.obj", b"mesh")
    shared_again = AssetFile(shared.path, shared.size, shared.sha256)
    a = _asset_file(tmp_path, "bot_a/model.xml", MINIMAL_MJCF.encode())
    b = _asset_file(tmp_path, "bot_b/model.xml", MINIMAL_MJCF.encode())
    return LibraryManifest(
        library=LibraryInfo(
            name="test-lib",
            title="Test Library",
            license="Apache-2.0",
            tags=["robots"],
            upstream={"repo": "https://example.com/repo", "commit": "abc"},
        ),
        assets=[
            Asset(
                id="bot_a",
                title="Bot A",
                kind="mjcf",
                format="mjcf",
                entry="bot_a/model.xml",
                entry_points={
                    "model": {"kind": "robot", "file": "bot_a/model.xml"},
                },
                files=[a, shared],
                meta={"dof": 3},
            ),
            Asset(
                id="bot_b",
                kind="mjcf",
                entry="bot_b/model.xml",
                files=[b, shared_again],
            ),
        ],
    )


# ─── manifest: hashing + round-trip ──────────────────────────────────


def test_hash_file_streams_sha256(tmp_path):
    payload = b"dreamlake" * 100_000  # spans several chunks
    path = tmp_path / "blob.bin"
    path.write_bytes(payload)
    assert hash_file(path, chunk_size=4096) == \
        hashlib.sha256(payload).hexdigest()


def test_file_entry_is_posix_relative(tmp_path):
    entry = _asset_file(tmp_path, "sub/dir/f.txt", b"hello")
    assert entry.path == "sub/dir/f.txt"
    assert entry.size == 5
    assert entry.sha256 == hashlib.sha256(b"hello").hexdigest()


def test_manifest_roundtrip(tmp_path):
    manifest = _manifest(tmp_path)
    path = write_manifest(manifest, tmp_path)
    assert path == tmp_path / "assets.json"
    loaded = load_manifest(path)
    assert loaded.to_json() == manifest.to_json()
    doc = json.loads(path.read_text())
    assert doc["schema"] == SCHEMA
    assert doc["library"]["type"] == "3d"  # default emitted
    assert doc["assets"][0]["entryPoints"]["model"]["kind"] == "robot"
    # file entries use the object form
    assert set(doc["assets"][0]["files"][0]) == {"path", "size", "sha256"}


def test_write_manifest_is_deterministic(tmp_path):
    manifest = _manifest(tmp_path)
    first = write_manifest(manifest, tmp_path / "one").read_text()
    second = write_manifest(manifest, tmp_path / "two").read_text()
    assert first == second
    # asset order stays as given (not sorted by id)
    assert first.index('"bot_a"') < first.index('"bot_b"')


# ─── manifest: validation rejections ─────────────────────────────────


def test_validate_rejects_duplicate_id(tmp_path):
    manifest = _manifest(tmp_path)
    manifest.assets[1].id = "bot_a"
    with pytest.raises(ManifestError, match="duplicate id"):
        validate(manifest)


def test_validate_rejects_entry_not_in_files(tmp_path):
    manifest = _manifest(tmp_path)
    manifest.assets[0].entry = "bot_a/other.xml"
    with pytest.raises(ManifestError, match="not listed in files"):
        validate(manifest)


@pytest.mark.parametrize("bad", ["/abs/path", "../escape", "a/../b",
                                 "win\\path", "a//b", ""])
def test_validate_rejects_bad_paths(tmp_path, bad):
    manifest = _manifest(tmp_path)
    manifest.assets[0].files[0].path = bad
    with pytest.raises(ManifestError):
        validate(manifest)


def test_validate_rejects_bad_sha256(tmp_path):
    manifest = _manifest(tmp_path)
    manifest.assets[0].files[0].sha256 = "ABC123"  # not 64 lowercase hex
    with pytest.raises(ManifestError, match="sha256"):
        validate(manifest)


def test_validate_rejects_bad_ids_and_names(tmp_path):
    manifest = _manifest(tmp_path)
    manifest.assets[0].id = "_leading_underscore"
    with pytest.raises(ManifestError, match="does not match"):
        validate(manifest)
    manifest = _manifest(tmp_path)
    manifest.library.name = "UpperCase"
    with pytest.raises(ManifestError, match="library.name"):
        validate(manifest)


def test_validate_rejects_unknown_kind(tmp_path):
    manifest = _manifest(tmp_path)
    manifest.assets[0].kind = "hologram"
    with pytest.raises(ManifestError, match="kind"):
        validate(manifest)


def test_validate_rejects_shared_path_mismatch(tmp_path):
    manifest = _manifest(tmp_path)
    shared = manifest.assets[1].files[1]
    assert shared.path == "shared/common.obj"
    shared.sha256 = "0" * 64
    with pytest.raises(ManifestError, match="identical"):
        validate(manifest)


def test_validate_rejects_thumbnail_not_in_files(tmp_path):
    manifest = _manifest(tmp_path)
    manifest.assets[0].thumbnail = "thumbnails/bot_a.png"
    with pytest.raises(ManifestError, match="thumbnail"):
        validate(manifest)


def test_validate_rejects_bad_entrypoint(tmp_path):
    manifest = _manifest(tmp_path)
    manifest.assets[0].entry_points = {
        "model": {"kind": "vehicle", "file": "bot_a/model.xml"}}
    with pytest.raises(ManifestError, match="kind"):
        validate(manifest)
    manifest = _manifest(tmp_path)
    manifest.assets[0].entry_points = {
        "model": {"kind": "robot", "file": "bot_a/missing.xml"}}
    with pytest.raises(ManifestError, match="not listed in files"):
        validate(manifest)


def test_validate_rejects_oversize_meta(tmp_path):
    manifest = _manifest(tmp_path)
    manifest.assets[0].meta = {"blob": "x" * 9000}
    with pytest.raises(ManifestError, match="meta"):
        validate(manifest)


def test_load_rejects_unknown_keys(tmp_path):
    manifest = _manifest(tmp_path)
    path = write_manifest(manifest, tmp_path)
    doc = json.loads(path.read_text())

    for mutate in (
        lambda d: d.update(extra=1),
        lambda d: d["library"].update(sponsor="acme"),
        lambda d: d["assets"][0].update(color="red"),
        lambda d: d["assets"][0]["files"][0].update(md5="nope"),
    ):
        broken = json.loads(json.dumps(doc))
        mutate(broken)
        path.write_text(json.dumps(broken))
        with pytest.raises(ManifestError, match="unknown key"):
            load_manifest(path)


def test_load_rejects_string_file_entries(tmp_path):
    manifest = _manifest(tmp_path)
    path = write_manifest(manifest, tmp_path)
    doc = json.loads(path.read_text())
    doc["assets"][0]["files"] = ["bot_a/model.xml"]  # object form required
    path.write_text(json.dumps(doc))
    with pytest.raises(ManifestError, match="object form"):
        load_manifest(path)


# ─── menagerie importer ──────────────────────────────────────────────


@pytest.fixture
def menagerie_repo(tmp_path):
    repo = tmp_path / "menagerie"
    bot = repo / "tiny_bot"
    bot.mkdir(parents=True)
    (bot / "tiny_bot.xml").write_text(MINIMAL_MJCF)
    (bot / "scene.xml").write_text(
        '<mujoco><include file="tiny_bot.xml"/></mujoco>')
    (bot / "fragment.xml").write_text(  # include fragment: no entry point
        '<mujoco><keyframe><key name="home" qpos="0"/></keyframe></mujoco>')
    (bot / "README.md").write_text("# Tiny Bot Description (MJCF)\n\nHi.\n")
    (bot / "LICENSE").write_text(APACHE_SNIPPET)
    _png(bot / "tiny_bot.png")
    (bot / "assets").mkdir()
    (bot / "assets" / "arm.obj").write_bytes(b"v 0 0 0\n")
    # a non-model directory: no top-level XML, must be skipped
    docs = repo / "docs"
    docs.mkdir()
    (docs / "notes.md").write_text("not a model\n")
    return repo


def test_menagerie_import(menagerie_repo, tmp_path):
    out = tmp_path / "lib"
    manifest = menagerie.build_library(menagerie_repo, out)

    assert [a.id for a in manifest.assets] == ["tiny_bot"]
    asset = manifest.assets[0]
    assert asset.title == "Tiny Bot"
    assert asset.kind == "mjcf"
    assert asset.license == "Apache-2.0"
    assert asset.entry == "tiny_bot/scene.xml"  # scene preferred
    assert asset.entry_points == {
        "scene": {"kind": "scene", "file": "tiny_bot/scene.xml"},
        "tiny_bot": {"kind": "robot", "file": "tiny_bot/tiny_bot.xml"},
    }
    # shipped preview re-encoded to thumbnails/<id>.webp, listed in
    # files with the WebP's real size + sha256 (not the source PNG's)
    assert asset.thumbnail == "thumbnails/tiny_bot.webp"
    paths = {f.path for f in asset.files}
    assert "thumbnails/tiny_bot.webp" in paths
    assert "tiny_bot/assets/arm.obj" in paths
    assert "tiny_bot/fragment.xml" in paths  # shipped even if not an entry
    thumb_path = out / "thumbnails" / "tiny_bot.webp"
    with Image.open(thumb_path) as img:
        assert img.format == "WEBP"
        assert img.size == (2, 2)  # small preview: never upscaled
    thumb_entry = next(
        f for f in asset.files if f.path == "thumbnails/tiny_bot.webp")
    assert thumb_entry.size == thumb_path.stat().st_size
    assert thumb_entry.sha256 == hashlib.sha256(
        thumb_path.read_bytes()).hexdigest()

    # files copied verbatim, hashes match the source bytes
    assert (out / "tiny_bot" / "tiny_bot.xml").read_text() == MINIMAL_MJCF
    entry = next(f for f in asset.files
                 if f.path == "tiny_bot/tiny_bot.xml")
    assert entry.sha256 == hashlib.sha256(
        MINIMAL_MJCF.encode()).hexdigest()

    # the written directory is a valid library
    loaded = load_manifest(out / "assets.json")
    assert loaded.library.name == "mujoco-menagerie"
    assert loaded.library.upstream["repo"] == menagerie.REPO_URL


def test_menagerie_subset_unknown_name(menagerie_repo, tmp_path):
    with pytest.raises(FileNotFoundError, match="nosuch"):
        menagerie.build_library(
            menagerie_repo, tmp_path / "lib", subset=["nosuch"])


def test_menagerie_cli_main(menagerie_repo, tmp_path):
    out = tmp_path / "lib"
    rc = menagerie.main(
        [str(menagerie_repo), str(out), "--subset", "tiny_bot", "--link"])
    assert rc == 0
    assert (out / "assets.json").exists()


def test_menagerie_module_entrypoint(menagerie_repo, tmp_path):
    src_root = Path(__file__).resolve().parents[1] / "src"
    out = tmp_path / "lib"
    proc = subprocess.run(
        [sys.executable, "-m", "dreamlake.assets_tools.import_menagerie",
         str(menagerie_repo), str(out)],
        capture_output=True, text=True, check=False,
        env={"PYTHONPATH": str(src_root), "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0, proc.stderr
    assert (out / "assets.json").exists()


# ─── mujoco_scanned_objects importer ─────────────────────────────────


@pytest.fixture
def gso_repo(tmp_path):
    repo = tmp_path / "gso"
    obj = repo / "models" / "Toy_Fire_Truck"
    obj.mkdir(parents=True)
    (obj / "model.xml").write_text(MINIMAL_MJCF)
    (obj / "model.obj").write_bytes(b"v 0 0 0\n")
    (obj / "model_collision_0.obj").write_bytes(b"v 0 0 0\n")
    _png(obj / "texture.png")
    return repo


def test_gso_import(gso_repo, tmp_path):
    out = tmp_path / "lib"
    manifest = gso.build_library(gso_repo, out)

    assert manifest.library.name == "mujoco-scanned-objects"
    assert manifest.library.license == "CC-BY-4.0"
    assert manifest.library.provider == "Google Scanned Objects"
    assert [a.id for a in manifest.assets] == ["Toy_Fire_Truck"]
    asset = manifest.assets[0]
    assert asset.title == "Toy Fire Truck"
    assert asset.category == "object"
    assert asset.entry == "Toy_Fire_Truck/model.xml"
    assert asset.thumbnail is None  # no --thumbnails, GSO ships none
    assert {f.path for f in asset.files} == {
        "Toy_Fire_Truck/model.xml",
        "Toy_Fire_Truck/model.obj",
        "Toy_Fire_Truck/model_collision_0.obj",
        "Toy_Fire_Truck/texture.png",
    }
    load_manifest(out / "assets.json")  # round-trips strictly


def test_gso_sanitizes_non_ascii_ids(gso_repo, tmp_path):
    # the real dataset ships two Pokémon_* dirs whose names violate
    # the asset-id charset; ids (and paths) are transliterated, the
    # original name preserved in upstream.id
    obj = gso_repo / "models" / "Pokémon_Yellow"
    obj.mkdir()
    (obj / "model.xml").write_text(MINIMAL_MJCF)
    out = tmp_path / "lib"
    manifest = gso.build_library(gso_repo, out)
    ids = {a.id for a in manifest.assets}
    assert ids == {"Toy_Fire_Truck", "Pokemon_Yellow"}
    poke = next(a for a in manifest.assets if a.id == "Pokemon_Yellow")
    assert poke.title == "Pokémon Yellow"
    assert poke.upstream == {"id": "Pokémon_Yellow"}
    assert poke.entry == "Pokemon_Yellow/model.xml"
    assert (out / "Pokemon_Yellow" / "model.xml").exists()
    load_manifest(out / "assets.json")


def test_gso_cli_main(gso_repo, tmp_path, capsys):
    out = tmp_path / "lib"
    rc = gso.main([str(gso_repo), str(out)])
    assert rc == 0
    assert "1 assets" in capsys.readouterr().out


# ─── thumbnails ──────────────────────────────────────────────────────


def test_pillow_ships_webp():
    # save_thumbnail leans on Pillow's built-in webp codec; modern
    # wheels always carry it, so no extra dependency is declared
    from PIL import features
    assert features.check("webp")


def test_save_thumbnail_shrinks_big_rgba(tmp_path):
    # a render-like 512x512 RGBA: noisy opaque disc on a transparent
    # background (noise so neither codec gets a free lunch)
    rng = np.random.default_rng(42)
    rgba = rng.integers(0, 256, (512, 512, 4), dtype=np.uint8)
    yy, xx = np.mgrid[:512, :512]
    rgba[..., 3] = np.where(
        (xx - 256) ** 2 + (yy - 256) ** 2 <= 220 ** 2, 255, 0)
    src = Image.fromarray(rgba, "RGBA")
    as_png = tmp_path / "big.png"
    src.save(as_png)  # what the old pipeline shipped

    out = tmp_path / "thumbs" / "big.webp"
    save_thumbnail(src, out)
    with Image.open(out) as img:
        assert img.format == "WEBP"
        assert img.mode == "RGBA"  # alpha preserved
        assert img.size == (320, 320)
    assert out.stat().st_size < as_png.stat().st_size / 4


def test_save_thumbnail_never_upscales(tmp_path):
    out = tmp_path / "small.webp"
    save_thumbnail(Image.new("RGBA", (100, 80), (10, 200, 30, 255)), out)
    with Image.open(out) as img:
        assert img.format == "WEBP"
        assert img.size == (100, 80)


def test_render_skips_without_mujoco(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "mujoco", None)  # import -> ImportError
    xml = tmp_path / "m.xml"
    xml.write_text(MINIMAL_MJCF)
    with pytest.warns(UserWarning, match="mujoco is not installed"):
        assert render_thumbnail(xml, tmp_path / "t.webp") is False
    assert not (tmp_path / "t.webp").exists()


def test_render_thumbnail(tmp_path):
    pytest.importorskip("mujoco")
    xml = tmp_path / "m.xml"
    xml.write_text(MINIMAL_MJCF)
    out = tmp_path / "thumbs" / "m.webp"
    assert render_thumbnail(xml, out, size=64) is True
    with Image.open(out) as img:
        assert img.format == "WEBP"
        assert img.size == (64, 64)  # rendered at 128, downscaled
        assert img.mode == "RGBA"


def test_render_thumbnail_bad_model_returns_false(tmp_path):
    pytest.importorskip("mujoco")
    xml = tmp_path / "broken.xml"
    xml.write_text("<mujoco><worldbody><geom type=")
    with pytest.warns(UserWarning, match="thumbnail render failed"):
        assert render_thumbnail(xml, tmp_path / "t.webp") is False


def test_gso_import_with_thumbnails(gso_repo, tmp_path):
    pytest.importorskip("mujoco")
    out = tmp_path / "lib"
    manifest = gso.build_library(gso_repo, out, thumbnails=True, size=64)
    asset = manifest.assets[0]
    assert asset.thumbnail == "thumbnails/Toy_Fire_Truck.webp"
    assert (out / "thumbnails" / "Toy_Fire_Truck.webp").exists()
    assert asset.thumbnail in {f.path for f in asset.files}
    load_manifest(out / "assets.json")


# ─── embeddings sidecar (embed) ──────────────────────────────────────


class _FakeClip:
    """Deterministic ``_load_encoder`` stand-in: 8-dim vectors derived
    from content hashes (un-normalized, like the real encoder), with
    load/call counting for the cache assertions."""

    def __init__(self, dim: int = 8):
        self.dim = dim
        self.loads = 0
        self.image_calls: list[Path] = []
        self.text_calls: list[str] = []

    def _vec(self, seed: bytes) -> np.ndarray:
        digest = hashlib.sha256(seed).digest()
        return np.frombuffer(
            digest[:self.dim], np.uint8).astype(np.float32) + 1.0

    def _normalized(self, seed: bytes) -> np.ndarray:
        """The row the writer must emit, mirroring its float math."""
        v = self._vec(seed)
        return (v / float(np.linalg.norm(v))).astype(np.float32)

    def expected_image(self, path: Path) -> np.ndarray:
        return self._normalized(Path(path).read_bytes())

    def expected_text(self, text: str) -> np.ndarray:
        return self._normalized(text.encode())

    def install(self, monkeypatch) -> "_FakeClip":
        def loader(model, pretrained, device):
            self.loads += 1

            def encode_image(path):
                self.image_calls.append(Path(path))
                return self._vec(Path(path).read_bytes())

            def encode_text(text):
                self.text_calls.append(text)
                return self._vec(text.encode())

            return encode_image, encode_text

        monkeypatch.setattr(embed_mod, "_load_encoder", loader)
        return self


def _bomb_loader(*_args):
    raise AssertionError("encoder loaded despite warm cache")


@pytest.fixture
def embed_lib(tmp_path):
    """bot_a: thumbnail + full text; bot_b: thumbnail, no text
    metadata; bot_c: no thumbnail, title only."""
    lib = tmp_path / "embed-lib"

    def thumb(rel: str, color) -> AssetFile:
        path = lib / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (2, 2), color).save(path)
        return file_entry(lib, rel)

    xml_a = _asset_file(lib, "bot_a/model.xml", MINIMAL_MJCF.encode())
    xml_b = _asset_file(lib, "bot_b/model.xml", MINIMAL_MJCF.encode())
    xml_c = _asset_file(lib, "bot_c/model.xml", MINIMAL_MJCF.encode())
    thumb_a = thumb("thumbnails/bot_a.png", (200, 30, 30))
    thumb_b = thumb("thumbnails/bot_b.png", (30, 30, 200))
    manifest = LibraryManifest(
        library=LibraryInfo(name="embed-lib"),
        assets=[
            Asset(id="bot_a", title="Bot A", description="A red robot",
                  tags=["robots", "red"], kind="mjcf",
                  files=[xml_a, thumb_a],
                  thumbnail="thumbnails/bot_a.png"),
            Asset(id="bot_b", kind="mjcf", files=[xml_b, thumb_b],
                  thumbnail="thumbnails/bot_b.png"),
            Asset(id="bot_c", title="Bot C", kind="mjcf", files=[xml_c]),
        ],
    )
    write_manifest(manifest, lib)
    return lib


def test_embed_sidecar_matches_server_contract(
        embed_lib, tmp_path, monkeypatch):
    fake = _FakeClip().install(monkeypatch)
    stats = embed_mod.embed_library(embed_lib, cache_dir=tmp_path / "cache")

    doc = json.loads((embed_lib / "assets.vectors.json").read_text())
    assert doc["schema"] == "dreamlake.assets.vectors/v1"
    assert doc["model"] == "open_clip/ViT-L-14/openai"
    assert doc["dim"] == 8
    # every asset present, manifest order; row indices in
    # assignment order; null image/text where the input is absent
    assert [it["id"] for it in doc["items"]] == ["bot_a", "bot_b", "bot_c"]
    a, b, c = doc["items"]
    assert (a["image"], a["text"]) == (0, 1)
    assert (b["image"], b["text"]) == (2, None)  # no title/desc/tags
    assert (c["image"], c["text"]) == (None, 3)  # no thumbnail

    # matrix: rows*dim little-endian float32, L2-normalized, row i at
    # byte i*dim*4 -- exactly what parseVectorsSidecar reads
    raw = (embed_lib / "assets.vectors.f32").read_bytes()
    assert len(raw) == 4 * 8 * 4
    mat = np.frombuffer(raw, dtype="<f4").reshape(4, 8)
    assert np.allclose(np.linalg.norm(mat, axis=1), 1.0, atol=1e-6)
    exp_rows = [
        fake.expected_image(embed_lib / "thumbnails/bot_a.png"),
        fake.expected_text("Bot A. A red robot. robots, red"),
        fake.expected_image(embed_lib / "thumbnails/bot_b.png"),
        fake.expected_text("Bot C"),
    ]
    assert np.array_equal(mat, np.stack(exp_rows))
    # byte-level little-endian proof, independent of numpy's reader
    assert raw[:4] == struct.pack("<f", exp_rows[0][0])
    off = 2 * 8 * 4  # row 2, element 0
    assert raw[off:off + 4] == struct.pack("<f", exp_rows[2][0])

    assert fake.loads == 1
    assert fake.text_calls == ["Bot A. A red robot. robots, red", "Bot C"]
    assert stats["rows"] == 4 and stats["dim"] == 8
    assert stats["images"] == {
        "embedded": 2, "cached": 0, "missing": 0, "failed": 0}
    assert stats["texts"] == {
        "embedded": 2, "cached": 0, "empty": 1, "failed": 0}


def test_embed_second_run_is_all_cache_hits(embed_lib, tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    fake = _FakeClip().install(monkeypatch)
    embed_mod.embed_library(embed_lib, cache_dir=cache)
    assert fake.loads == 1

    first = (embed_lib / "assets.vectors.f32").read_bytes()
    # second run: every vector cached -> the encoder must not even load
    monkeypatch.setattr(embed_mod, "_load_encoder", _bomb_loader)
    stats = embed_mod.embed_library(embed_lib, cache_dir=cache)
    assert stats["images"] == {
        "embedded": 0, "cached": 2, "missing": 0, "failed": 0}
    assert stats["texts"] == {
        "embedded": 0, "cached": 2, "empty": 1, "failed": 0}
    assert (embed_lib / "assets.vectors.f32").read_bytes() == first


def test_embed_cache_survives_manifest_regeneration(
        embed_lib, tmp_path, monkeypatch):
    # THE load-bearing property: the manifest is regenerated wholesale
    # (one asset deleted), but unchanged assets never re-run the model.
    cache = tmp_path / "cache"
    _FakeClip().install(monkeypatch)
    embed_mod.embed_library(embed_lib, cache_dir=cache)

    manifest = load_manifest(embed_lib / "assets.json")
    manifest.assets = [a for a in manifest.assets if a.id != "bot_b"]
    write_manifest(manifest, embed_lib)

    monkeypatch.setattr(embed_mod, "_load_encoder", _bomb_loader)
    embed_mod.embed_library(embed_lib, cache_dir=cache)

    doc = json.loads((embed_lib / "assets.vectors.json").read_text())
    assert [it["id"] for it in doc["items"]] == ["bot_a", "bot_c"]
    a, c = doc["items"]
    # rows re-packed contiguously after the deletion
    assert (a["image"], a["text"], c["image"], c["text"]) == (0, 1, None, 2)
    assert len((embed_lib / "assets.vectors.f32").read_bytes()) == 3 * 8 * 4


def test_embed_force_reencodes(embed_lib, tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    fake = _FakeClip().install(monkeypatch)
    embed_mod.embed_library(embed_lib, cache_dir=cache)
    stats = embed_mod.embed_library(embed_lib, cache_dir=cache, force=True)
    assert stats["images"]["embedded"] == 2
    assert stats["images"]["cached"] == 0
    assert len(fake.image_calls) == 4  # 2 per run


def test_embed_missing_extra_message(embed_lib, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "open_clip", None)
    monkeypatch.setitem(sys.modules, "torch", None)
    with pytest.raises(ImportError, match=r"dreamlake\[embed\]"):
        embed_mod.embed_library(embed_lib, cache_dir=tmp_path / "cache")


def test_embed_nothing_to_embed(tmp_path):
    lib = tmp_path / "lib"
    xml = _asset_file(lib, "bot/model.xml", MINIMAL_MJCF.encode())
    write_manifest(
        LibraryManifest(assets=[Asset(id="bot", kind="mjcf", files=[xml])]),
        lib)
    # no thumbnail, no text: fails BEFORE touching the encoder
    with pytest.raises(ValueError, match="nothing to embed"):
        embed_mod.embed_library(lib, cache_dir=tmp_path / "cache")


def test_embed_cli_main(embed_lib, tmp_path, monkeypatch, capsys):
    _FakeClip().install(monkeypatch)
    rc = embed_mod.main([
        str(embed_lib), "--device", "cpu",
        "--cache-dir", str(tmp_path / "cache")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "4 rows" in out and "dim 8" in out
    assert (embed_lib / "assets.vectors.json").exists()
    assert (embed_lib / "assets.vectors.f32").exists()


def test_embed_cli_error(tmp_path, capsys):
    rc = embed_mod.main([str(tmp_path / "does-not-exist")])
    assert rc == 1
    assert "error:" in capsys.readouterr().err
