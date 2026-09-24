"""Tests for dreamlake.assets_tools (manifest + importers + thumbnails).

Self-contained: importer smokes run on tiny synthetic repos built in
tmp_path (a minimal one-geom MJCF + a dummy PNG) -- no dependency on a
real asset_library checkout. Render tests skip cleanly when mujoco is
not installed (it is only a tooling extra); yml round-trip checks skip
without pyyaml (a dev-env transitive, never a base dep).
"""

import hashlib
import inspect
import json
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from dreamlake.assets_tools import (
    SCHEMA,
    Asset,
    AssetFile,
    LibraryInfo,
    LibraryManifest,
    ManifestError,
    dump_yaml,
    file_entry,
    hash_file,
    load_manifest,
    render_thumbnail,
    save_thumbnail,
    stage_thumbnail,
    validate,
    write_manifest,
)
from dreamlake.assets_tools import embed as embed_mod
from dreamlake.assets_tools import render_thumbnails as batch
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


# ─── manifest: generated[] (the reserved .dreamlake/ artifacts) ──────


def test_manifest_generated_roundtrip(tmp_path):
    # the wire shape the CLI builds: generated thumbnails declared at
    # the top level, referenced by asset.thumbnail, absent from files[]
    manifest = _manifest(tmp_path)
    manifest.generated = [
        ".dreamlake/thumbnails/bot_a.webp",
        ".dreamlake/thumbnails/bot_b.webp",
        ".dreamlake/vectors.json",
        ".dreamlake/vectors.f32",
    ]
    manifest.assets[0].thumbnail = ".dreamlake/thumbnails/bot_a.webp"
    manifest.assets[1].thumbnail = ".dreamlake/thumbnails/bot_b.webp"
    path = write_manifest(manifest, tmp_path)
    loaded = load_manifest(path)
    assert loaded.generated == manifest.generated
    assert loaded.to_json() == manifest.to_json()
    doc = json.loads(path.read_text())
    assert doc["generated"] == manifest.generated
    # no .dreamlake/ path anywhere in files[]
    for asset in doc["assets"]:
        assert not any(
            f["path"].startswith(".dreamlake/") for f in asset["files"])


def test_validate_rejects_dreamlake_files_path(tmp_path):
    manifest = _manifest(tmp_path)
    manifest.assets[0].files[0].path = ".dreamlake/thumbnails/x.webp"
    with pytest.raises(ManifestError, match="reserved"):
        validate(manifest)


def test_validate_generated_rules(tmp_path):
    manifest = _manifest(tmp_path)
    manifest.generated = ["thumbnails/x.webp"]  # not under .dreamlake/
    with pytest.raises(ManifestError, match="must start with"):
        validate(manifest)

    manifest = _manifest(tmp_path)
    manifest.generated = [".dreamlake/a.webp", ".dreamlake/a.webp"]
    with pytest.raises(ManifestError, match="duplicate"):
        validate(manifest)

    manifest = _manifest(tmp_path)
    manifest.generated = [".dreamlake/manifest.json"]  # the manifest itself
    with pytest.raises(ManifestError, match="reserved"):
        validate(manifest)

    manifest = _manifest(tmp_path)
    manifest.generated = [".dreamlake/../escape"]  # path rules still apply
    with pytest.raises(ManifestError, match=r"\.\."):
        validate(manifest)


def test_thumbnail_in_generated_entry_still_files_only(tmp_path):
    manifest = _manifest(tmp_path)
    manifest.generated = [".dreamlake/thumbnails/bot_a.webp"]
    manifest.assets[0].thumbnail = ".dreamlake/thumbnails/bot_a.webp"
    validate(manifest)  # thumbnail may live in generated[]

    # entry and entryPoints stay files-only
    manifest.assets[0].entry = ".dreamlake/thumbnails/bot_a.webp"
    with pytest.raises(ManifestError, match="not listed in files"):
        validate(manifest)
    manifest = _manifest(tmp_path)
    manifest.generated = [".dreamlake/thumbnails/bot_a.webp"]
    manifest.assets[0].entry_points = {
        "model": {"kind": "robot",
                  "file": ".dreamlake/thumbnails/bot_a.webp"}}
    with pytest.raises(ManifestError, match="not listed in files"):
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
    yaml = pytest.importorskip("yaml")
    out = tmp_path / "lib"
    doc = menagerie.build_library(menagerie_repo, out)

    # source + dreamlake.yml, NOTHING generated
    assert not (out / "assets.json").exists()
    assert not (out / "thumbnails").exists()
    assert sorted(p.name for p in out.iterdir()) == [
        "dreamlake.yml", "tiny_bot"]

    # the emitted yml parses back to exactly the returned document
    parsed = yaml.safe_load(
        (out / "dreamlake.yml").read_text(encoding="utf-8"))
    assert parsed == doc

    lib = doc["library"]
    assert lib["title"] == "MuJoCo Menagerie"
    assert lib["provider"] == "Google DeepMind"
    assert lib["upstream"]["repo"] == menagerie.REPO_URL
    assert "name" not in lib  # the push target names the library

    assert list(doc["assets"]) == ["tiny_bot"]
    asset = doc["assets"]["tiny_bot"]
    assert asset["title"] == "Tiny Bot"
    assert asset["license"] == "Apache-2.0"
    assert asset["entry"] == "tiny_bot/scene.xml"  # scene preferred
    assert asset["entryPoints"] == {
        "scene": {"kind": "scene", "file": "tiny_bot/scene.xml"},
        "tiny_bot": {"kind": "robot", "file": "tiny_bot/tiny_bot.xml"},
    }
    assert "thumbnail" not in asset  # thumbnails are render-time now

    # source files copied verbatim; the shipped preview PNG stays a
    # plain source file (no re-encode, no thumbnails/ dir), include
    # fragments ship even though they are not entry points
    assert (out / "tiny_bot" / "tiny_bot.xml").read_text() == MINIMAL_MJCF
    assert (out / "tiny_bot" / "assets" / "arm.obj").exists()
    assert (out / "tiny_bot" / "fragment.xml").exists()
    assert (out / "tiny_bot" / "tiny_bot.png").exists()


def test_menagerie_subset_unknown_name(menagerie_repo, tmp_path):
    with pytest.raises(FileNotFoundError, match="nosuch"):
        menagerie.build_library(
            menagerie_repo, tmp_path / "lib", subset=["nosuch"])


def test_menagerie_cli_main(menagerie_repo, tmp_path):
    out = tmp_path / "lib"
    rc = menagerie.main(
        [str(menagerie_repo), str(out), "--subset", "tiny_bot", "--link"])
    assert rc == 0
    assert (out / "dreamlake.yml").exists()
    assert not (out / "assets.json").exists()


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
    assert (out / "dreamlake.yml").exists()
    assert not (out / "assets.json").exists()


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
    yaml = pytest.importorskip("yaml")
    out = tmp_path / "lib"
    doc = gso.build_library(gso_repo, out)

    lib = doc["library"]
    assert lib["title"] == "MuJoCo Scanned Objects"
    assert lib["license"] == "CC-BY-4.0"
    assert lib["provider"] == "Google Scanned Objects"
    assert "name" not in lib

    # the uniform category hoists into defaults
    assert doc["defaults"] == {"category": "object"}
    assert list(doc["assets"]) == ["Toy_Fire_Truck"]
    asset = doc["assets"]["Toy_Fire_Truck"]
    assert asset["title"] == "Toy Fire Truck"
    assert "category" not in asset
    assert asset["entry"] == "Toy_Fire_Truck/model.xml"

    # source + dreamlake.yml, nothing generated
    assert not (out / "assets.json").exists()
    assert not (out / "thumbnails").exists()
    for rel in ("model.xml", "model.obj", "model_collision_0.obj",
                "texture.png"):
        assert (out / "Toy_Fire_Truck" / rel).exists()
    parsed = yaml.safe_load(
        (out / "dreamlake.yml").read_text(encoding="utf-8"))
    assert parsed == doc


def test_gso_sanitizes_non_ascii_ids(gso_repo, tmp_path):
    yaml = pytest.importorskip("yaml")
    # the real dataset ships two Pokémon_* dirs whose names violate
    # the asset-id charset; ids (and paths) are transliterated, the
    # original name preserved in upstream.id
    obj = gso_repo / "models" / "Pokémon_Yellow"
    obj.mkdir()
    (obj / "model.xml").write_text(MINIMAL_MJCF)
    out = tmp_path / "lib"
    doc = gso.build_library(gso_repo, out)
    assert set(doc["assets"]) == {"Toy_Fire_Truck", "Pokemon_Yellow"}
    poke = doc["assets"]["Pokemon_Yellow"]
    assert poke["title"] == "Pokémon Yellow"
    assert poke["upstream"] == {"id": "Pokémon_Yellow"}
    assert poke["entry"] == "Pokemon_Yellow/model.xml"
    assert (out / "Pokemon_Yellow" / "model.xml").exists()
    # non-ASCII titles survive the emit -> parse round trip
    parsed = yaml.safe_load(
        (out / "dreamlake.yml").read_text(encoding="utf-8"))
    assert parsed == doc


def test_gso_cli_main(gso_repo, tmp_path, capsys):
    out = tmp_path / "lib"
    rc = gso.main([str(gso_repo), str(out)])
    assert rc == 0
    assert "1 assets" in capsys.readouterr().out
    assert (out / "dreamlake.yml").exists()


# ─── dreamlake.yml emission ──────────────────────────────────────────


def test_dump_yaml_roundtrip():
    yaml = pytest.importorskip("yaml")
    doc = {
        "library": {
            "title": "Lib: tricky title",
            "description": 'She said "hi"\nsecond line',
            "homepage": "https://example.com/repo",
            "license": "CC-BY-4.0",
            "tags": ["robots", "mujoco", "3.14", "true", "白菜"],
            "upstream": {"repo": "https://x.example/z", "commit": "0123abc"},
        },
        "defaults": {"category": "object"},
        "assets": {
            "2_of_Jenga_Classic_Game": {
                "title": "2 of Jenga Classic Game",
                "description": "Ends with a period.",
                "entry": "2_of_Jenga_Classic_Game/model.xml",
            },
            "Pokemon_Yellow": {
                "title": "Pokémon Yellow",
                "upstream": {"id": "Pokémon_Yellow"},
                "entryPoints": {
                    "scene": {
                        "kind": "scene",
                        "file": "Pokemon_Yellow/scene.xml",
                    },
                },
            },
            "edge_cases": {"title": "x", "tags": []},
        },
    }
    assert yaml.safe_load(dump_yaml(doc)) == doc


def test_dump_yaml_quotes_ambiguous_scalars():
    yaml = pytest.importorskip("yaml")
    doc = {"a": "true", "b": "3.14", "c": "1_000", "d": "no",
           "e": "trailing space ", "f": "0123abc", "g": "with: colon",
           "h": "#comment-ish"}
    parsed = yaml.safe_load(dump_yaml(doc))
    assert parsed == doc
    for value in parsed.values():
        assert isinstance(value, str)  # nothing type-coerced


# ─── thumbnails ──────────────────────────────────────────────────────


def test_pillow_ships_webp():
    # save_thumbnail leans on Pillow's built-in webp codec; modern
    # wheels always carry it, so no extra dependency is declared
    from PIL import features
    assert features.check("webp")


def test_thumbnail_defaults_are_640():
    # the CLI contract: 640 max dimension, supersampled at 2x = 1280
    from dreamlake.assets_tools.thumbnails import THUMBNAIL_MAX_DIM
    assert THUMBNAIL_MAX_DIM == 640
    assert inspect.signature(
        save_thumbnail).parameters["max_dim"].default == 640
    assert inspect.signature(
        render_thumbnail).parameters["size"].default == 640


def test_save_thumbnail_shrinks_big_rgba(tmp_path):
    # a render-like 1280x1280 RGBA (the 2x supersample of the default
    # 640): noisy opaque disc on a transparent background (noise so
    # neither codec gets a free lunch)
    rng = np.random.default_rng(42)
    rgba = rng.integers(0, 256, (1280, 1280, 4), dtype=np.uint8)
    yy, xx = np.mgrid[:1280, :1280]
    rgba[..., 3] = np.where(
        (xx - 640) ** 2 + (yy - 640) ** 2 <= 550 ** 2, 255, 0)
    src = Image.fromarray(rgba, "RGBA")
    as_png = tmp_path / "big.png"
    src.save(as_png)  # what the old pipeline shipped

    out = tmp_path / "thumbs" / "big.webp"
    save_thumbnail(src, out)
    with Image.open(out) as img:
        assert img.format == "WEBP"
        assert img.mode == "RGBA"  # alpha preserved
        assert img.size == (640, 640)  # default max_dim
    assert out.stat().st_size < as_png.stat().st_size / 4


def test_save_thumbnail_never_upscales(tmp_path):
    out = tmp_path / "small.webp"
    save_thumbnail(Image.new("RGBA", (100, 80), (10, 200, 30, 255)), out)
    with Image.open(out) as img:
        assert img.format == "WEBP"
        assert img.size == (100, 80)


def _synthetic_render(size=400, box=(120, 90), origin=(30, 40)):
    """A transparent 'render': one opaque box, deliberately off-center
    so staging has to recrop and recenter it."""
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(Image.new("RGBA", box, (200, 60, 40, 255)), origin)
    return canvas


def test_stage_param_defaults_off():
    # staging is OPT-IN: user-provided preview images (photos with
    # real backgrounds) flow through save_thumbnail and must never
    # grow a fake contact shadow
    assert inspect.signature(
        save_thumbnail).parameters["stage"].default is False


def test_save_thumbnail_stage_adds_contact_shadow(tmp_path):
    out = tmp_path / "staged.webp"
    save_thumbnail(_synthetic_render(), out, stage=True)
    a = np.asarray(Image.open(out).convert("RGBA"))[..., 3]
    assert a.shape[0] == a.shape[1]  # square staged canvas
    side = a.shape[0]

    # recentered horizontally at a consistent scale (~78% of width)
    cols = np.flatnonzero((a == 255).any(axis=0))
    assert 0.66 <= (cols[-1] - cols[0] + 1) / side <= 0.82
    assert abs((cols[0] + cols[-1] + 1) / 2 - side / 2) <= side * 0.03

    # bottom anchored on the ground line, headroom left for the shadow
    rows = np.flatnonzero((a == 255).any(axis=1))
    bottom = rows[-1]
    assert 0.80 <= bottom / side <= 0.90

    # the contact shadow: a patch of SEMI-transparent alpha below the
    # object -- soft (peak near STAGE_SHADOW_OPACITY), never opaque
    below = a[bottom + 2:, :]
    semi = below[(below > 20) & (below < 200)]
    assert semi.size > 50
    assert 0.20 <= below.max() / 255 <= 0.45
    assert not (below > 220).any()
    # and only under the object: top corners stay fully transparent
    assert a[:10, :10].max() == 0
    assert a[:10, -10:].max() == 0


def test_save_thumbnail_unstaged_has_no_shadow(tmp_path):
    # the same synthetic render WITHOUT stage=True: canvas untouched,
    # not a single semi-transparent pixel appears below the object
    out = tmp_path / "plain.webp"
    img = _synthetic_render()
    save_thumbnail(img, out)
    with Image.open(out) as saved:
        assert saved.size == img.size  # no recrop to a staged square
        a = np.asarray(saved.convert("RGBA"))[..., 3]
    assert a[131:, :].max() == 0  # nothing below the box (40 + 90 + 1)


def test_save_thumbnail_stage_skips_images_without_alpha(tmp_path):
    # belt and braces: even if a caller passes stage=True with a
    # photo-like RGB image, staging cannot apply (no alpha footprint)
    out = tmp_path / "photo.webp"
    save_thumbnail(Image.new("RGB", (300, 200), (90, 120, 150)), out,
                   stage=True)
    with Image.open(out) as img:
        assert img.size == (300, 200)  # not squared: staging skipped


def test_stage_thumbnail_passthrough_when_fully_transparent():
    empty = Image.new("RGBA", (50, 40), (0, 0, 0, 0))
    staged = stage_thumbnail(empty)
    assert staged.size == (50, 40)  # nothing to stage


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
        a = np.asarray(img)[..., 3]
    # the render path STAGES: the sphere sits on the shared ground
    # line with a soft (semi-transparent, never opaque) contact
    # shadow baked below it
    rows = np.flatnonzero((a == 255).any(axis=1))
    bottom = rows[-1]
    assert 0.78 <= bottom / a.shape[0] <= 0.92
    below = a[bottom + 2:, :]
    assert ((below > 20) & (below < 200)).sum() > 10
    assert not (below > 220).any()


def test_render_thumbnail_bad_model_returns_false(tmp_path):
    pytest.importorskip("mujoco")
    xml = tmp_path / "broken.xml"
    xml.write_text("<mujoco><worldbody><geom type=")
    with pytest.warns(UserWarning, match="thumbnail render failed"):
        assert render_thumbnail(xml, tmp_path / "t.webp") is False


# ─── batch renderer (render_thumbnails) ──────────────────────────────


@pytest.fixture
def render_jobs_file(tmp_path):
    """Good jobs bracketing a broken one, plus a urdf, a missing entry,
    and an illegal id -- the failure-isolation menu."""
    src = tmp_path / "src"
    (src / "bot_one").mkdir(parents=True)
    (src / "bot_one" / "model.xml").write_text(MINIMAL_MJCF)
    (src / "bot_two").mkdir(parents=True)
    (src / "bot_two" / "model.xml").write_text(MINIMAL_MJCF)
    (src / "broken.xml").write_text("<mujoco><worldbody><geom type=")
    jobs = [
        {"id": "bot_one", "entry": str(src / "bot_one" / "model.xml"),
         "kind": "mjcf"},
        {"id": "broken_bot", "entry": str(src / "broken.xml"),
         "kind": "mjcf"},
        {"id": "bot_two", "entry": str(src / "bot_two" / "model.xml"),
         "kind": "mjcf", "category": "object"},
        {"id": "wheelie", "entry": str(src / "bot_one" / "model.xml"),
         "kind": "urdf"},
        {"id": "ghost", "entry": str(src / "missing.xml"), "kind": "mjcf"},
        {"id": "bad/../id", "entry": str(src / "bot_one" / "model.xml"),
         "kind": "mjcf"},
    ]
    jobs_path = tmp_path / "jobs.json"
    jobs_path.write_text(json.dumps(jobs))
    return jobs_path


def test_render_thumbnails_batch(render_jobs_file, tmp_path, capsys):
    pytest.importorskip("mujoco")
    out_dir = tmp_path / "thumbs"
    rc = batch.main(["--jobs", str(render_jobs_file),
                     "--out-dir", str(out_dir), "--size", "64"])
    assert rc == 0  # per-job failures never abort the batch

    captured = capsys.readouterr()
    assert captured.out.count("\n") == 1  # EXACTLY one stdout line
    report = json.loads(captured.out)
    assert set(report) == {"rendered", "skipped", "failed"}

    # failure isolation: the broken job sits BETWEEN the good ones,
    # both still render
    assert report["rendered"] == ["bot_one", "bot_two"]
    assert [s["id"] for s in report["skipped"]] == ["wheelie"]
    assert "urdf" in report["skipped"][0]["reason"]
    failed = {f["id"]: f["reason"] for f in report["failed"]}
    assert set(failed) == {"broken_bot", "ghost", "bad/../id"}
    assert "entry not found" in failed["ghost"]
    assert failed["bad/../id"] == "invalid asset id"

    for asset_id in report["rendered"]:
        with Image.open(out_dir / f"{asset_id}.webp") as img:
            assert img.format == "WEBP"
            assert img.size == (64, 64)
    assert not (out_dir / "broken_bot.webp").exists()
    # progress/warnings live on stderr only
    assert "broken_bot" in captured.err


def test_render_thumbnails_without_mujoco(
        render_jobs_file, tmp_path, capsys, monkeypatch):
    monkeypatch.setitem(sys.modules, "mujoco", None)  # import -> error
    rc = batch.main(["--jobs", str(render_jobs_file),
                     "--out-dir", str(tmp_path / "thumbs")])
    assert rc == 0  # still a clean batch report, everything in failed[]
    report = json.loads(capsys.readouterr().out)
    assert report["rendered"] == []
    failed = {f["id"]: f["reason"] for f in report["failed"]}
    assert "mujoco is not installed" in failed["bot_one"]
    assert [s["id"] for s in report["skipped"]] == ["wheelie"]


def test_render_thumbnails_unreadable_jobs(tmp_path, capsys):
    rc = batch.main(["--jobs", str(tmp_path / "nope.json"),
                     "--out-dir", str(tmp_path / "o")])
    assert rc == 1
    captured = capsys.readouterr()
    assert "error:" in captured.err
    assert captured.out == ""  # fatal errors never fake a report

    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert batch.main(
        ["--jobs", str(bad), "--out-dir", str(tmp_path / "o")]) == 1
    not_a_list = tmp_path / "obj.json"
    not_a_list.write_text('{"id": "x"}')
    assert batch.main(
        ["--jobs", str(not_a_list), "--out-dir", str(tmp_path / "o")]) == 1


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


# ─── embeddings: manifest mode (the CLI's push-time step) ────────────


@pytest.fixture
def wire_manifest(tmp_path):
    """A built wire manifest decoupled from the source layout:
    bot_a: CLI-rendered thumbnail under .dreamlake/thumbnails/;
    bot_b: user-authored preview inside the source tree;
    bot_c: no thumbnail, title only."""
    files_root = tmp_path / "src"
    thumbs_dir = tmp_path / "cli-cache" / "thumbnails"
    build_dir = tmp_path / "cli-cache" / "build"
    thumbs_dir.mkdir(parents=True)

    xml_a = _asset_file(files_root, "bot_a/model.xml", MINIMAL_MJCF.encode())
    xml_b = _asset_file(files_root, "bot_b/model.xml", MINIMAL_MJCF.encode())
    xml_c = _asset_file(files_root, "bot_c/model.xml", MINIMAL_MJCF.encode())

    # the batch renderer's output: <thumbs-dir>/<id>.webp -- declared
    # in top-level generated[], NEVER in the asset's files[]
    Image.new("RGB", (2, 2), (200, 30, 30)).save(thumbs_dir / "bot_a.webp")
    # a user-authored preview, a plain source file
    Image.new("RGB", (2, 2), (30, 30, 200)).save(
        files_root / "bot_b" / "preview.png")
    preview_b = file_entry(files_root, "bot_b/preview.png")

    manifest = LibraryManifest(
        library=LibraryInfo(name="wire-lib"),
        generated=[
            ".dreamlake/thumbnails/bot_a.webp",
            ".dreamlake/vectors.json",
            ".dreamlake/vectors.f32",
        ],
        assets=[
            Asset(id="bot_a", title="Bot A", description="A red robot",
                  tags=["robots", "red"], kind="mjcf",
                  files=[xml_a],
                  thumbnail=".dreamlake/thumbnails/bot_a.webp"),
            Asset(id="bot_b", kind="mjcf", files=[xml_b, preview_b],
                  thumbnail="bot_b/preview.png"),
            Asset(id="bot_c", title="Bot C", kind="mjcf", files=[xml_c]),
        ],
    )
    write_manifest(manifest, build_dir)
    return build_dir / "assets.json", files_root, thumbs_dir


def test_embed_manifest_mode_roundtrip(wire_manifest, tmp_path, monkeypatch):
    manifest_path, files_root, thumbs_dir = wire_manifest
    out_dir = tmp_path / "out"
    fake = _FakeClip().install(monkeypatch)
    stats = embed_mod.embed_manifest(
        manifest_path, files_root=files_root, thumbs_dir=thumbs_dir,
        out_dir=out_dir, cache_dir=tmp_path / "vec-cache")

    # NEW names (no assets. prefix), in --out-dir; nothing lands next
    # to the manifest or in the source tree
    assert (out_dir / "vectors.json").exists()
    assert (out_dir / "vectors.f32").exists()
    assert not (manifest_path.parent / "assets.vectors.json").exists()
    assert not list(files_root.rglob("*vectors*"))

    doc = json.loads((out_dir / "vectors.json").read_text())
    assert doc["schema"] == "dreamlake.assets.vectors/v1"  # same format
    assert doc["model"] == "open_clip/ViT-L-14/openai"
    assert doc["dim"] == 8
    assert [it["id"] for it in doc["items"]] == ["bot_a", "bot_b", "bot_c"]
    a, b, c = doc["items"]
    assert (a["image"], a["text"]) == (0, 1)
    assert (b["image"], b["text"]) == (2, None)  # no title/desc/tags
    assert (c["image"], c["text"]) == (None, 3)  # no thumbnail

    raw = (out_dir / "vectors.f32").read_bytes()
    mat = np.frombuffer(raw, dtype="<f4").reshape(4, 8)
    exp_rows = [
        # .dreamlake/thumbnails/* resolves to <thumbs-dir>/<id>.webp
        fake.expected_image(thumbs_dir / "bot_a.webp"),
        fake.expected_text("Bot A. A red robot. robots, red"),
        # anything else resolves under --files-root
        fake.expected_image(files_root / "bot_b" / "preview.png"),
        fake.expected_text("Bot C"),
    ]
    assert np.array_equal(mat, np.stack(exp_rows))
    assert stats["json"] == str(out_dir / "vectors.json")
    assert stats["f32"] == str(out_dir / "vectors.f32")
    assert stats["rows"] == 4 and stats["dim"] == 8


def test_embed_manifest_mode_shares_content_cache(
        wire_manifest, tmp_path, monkeypatch):
    manifest_path, files_root, thumbs_dir = wire_manifest
    cache = tmp_path / "vec-cache"
    _FakeClip().install(monkeypatch)
    embed_mod.embed_manifest(
        manifest_path, files_root=files_root, thumbs_dir=thumbs_dir,
        out_dir=tmp_path / "out1", cache_dir=cache)

    # second run into a fresh out-dir: pure cache hits, encoder unloaded
    monkeypatch.setattr(embed_mod, "_load_encoder", _bomb_loader)
    stats = embed_mod.embed_manifest(
        manifest_path, files_root=files_root, thumbs_dir=thumbs_dir,
        out_dir=tmp_path / "out2", cache_dir=cache)
    assert stats["images"] == {
        "embedded": 0, "cached": 2, "missing": 0, "failed": 0}
    assert stats["texts"] == {
        "embedded": 0, "cached": 2, "empty": 1, "failed": 0}
    assert (tmp_path / "out1" / "vectors.f32").read_bytes() == \
        (tmp_path / "out2" / "vectors.f32").read_bytes()


def test_embed_manifest_cli_stdout_contract(
        wire_manifest, tmp_path, monkeypatch, capsys):
    manifest_path, files_root, thumbs_dir = wire_manifest
    _FakeClip().install(monkeypatch)
    out_dir = tmp_path / "out"
    rc = embed_mod.main([
        "--manifest", str(manifest_path),
        "--files-root", str(files_root),
        "--thumbs-dir", str(thumbs_dir),
        "--out-dir", str(out_dir),
        "--device", "cpu",
        "--cache-dir", str(tmp_path / "vec-cache"),
    ])
    assert rc == 0
    captured = capsys.readouterr()
    # stdout: EXACTLY one JSON line -- the stats dict the CLI parses
    assert captured.out.count("\n") == 1
    stats = json.loads(captured.out)
    assert stats["rows"] == 4 and stats["dim"] == 8
    assert stats["assets"] == 3
    assert stats["model"] == "open_clip/ViT-L-14/openai"
    assert stats["json"] == str(out_dir / "vectors.json")
    assert stats["f32"] == str(out_dir / "vectors.f32")
    assert stats["images"] == {
        "embedded": 2, "cached": 0, "missing": 0, "failed": 0}
    assert stats["texts"] == {
        "embedded": 2, "cached": 0, "empty": 1, "failed": 0}
    # human chatter stays on stderr
    assert "wrote" in captured.err


def test_embed_cli_mode_validation(tmp_path):
    with pytest.raises(SystemExit):  # neither mode
        embed_mod.main([])
    with pytest.raises(SystemExit):  # both modes
        embed_mod.main([str(tmp_path), "--manifest",
                        str(tmp_path / "m.json")])
    with pytest.raises(SystemExit):  # manifest mode missing its dirs
        embed_mod.main(["--manifest", str(tmp_path / "m.json")])
    with pytest.raises(SystemExit):  # manifest-only flags in legacy mode
        embed_mod.main([str(tmp_path), "--out-dir", str(tmp_path / "o")])
