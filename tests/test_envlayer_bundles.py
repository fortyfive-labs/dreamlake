"""``bundle:ns/name@v#entry`` srcs in env-layer stacks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dreamlake.envlayer import (
    ComposeError,
    load_stack,
    require_resolved,
    resolve_bundle_srcs,
)

from .bundle_fake_server import FakeBundleServer

SCHEMA = "dreamlake.env-layers/v3"


def stack(*layers):
    return {"schema": SCHEMA, "layers": list(layers)}


def test_bundle_src_is_a_registry_ref():
    s = load_stack(stack({"tag": "Merge", "src": "bundle:fortyfive/astribot-s1@3#s1-base"}))
    with pytest.raises(ComposeError, match="unresolved"):
        require_resolved(s)
    load_stack(stack({"tag": "Merge", "src": "bundle:ns/b@latest#e"}))
    load_stack(stack({"tag": "Merge", "src": "bundle:ns/b#e"}))


@pytest.mark.parametrize("src", ["bundle:ns/b@3", "bundle:ns@3#e", "bundle:ns/b@0#e", "bundle:ns/b@3#e/x"])
def test_bad_bundle_src(src):
    with pytest.raises(ComposeError, match="bundle entry ref"):
        load_stack(stack({"tag": "Merge", "src": src}))


def test_bundle_pin_accepted():
    load_stack(stack({"tag": "Merge", "src": "/tmp/x.xml", "pin": "bundle:ns/b@2#e"}))
    with pytest.raises(ComposeError, match="pin"):
        load_stack(stack({"tag": "Merge", "src": "/tmp/x.xml", "pin": "bundle:ns/b@latest#e"}))


def test_resolve_with_fake_fetch(tmp_path):
    view = tmp_path / "view"
    (view / "entries").mkdir(parents=True)
    (view / "robots").mkdir()
    (view / "robots" / "r.xml").write_text("<mujoco/>")
    (view / "entries" / "r.json").write_text('{"kind": "robot", "model": "robots/r.xml"}\n')
    local = tmp_path / "local.xml"
    local.write_text("<mujoco/>")
    stack_file = tmp_path / "stack.json"
    stack_file.write_text(json.dumps(stack(
        {"tag": "Merge", "src": "./local.xml"},
        {"tag": "Attach", "src": "bundle:ns/b@5#r", "key": "right", "joint": "rigid"},
    )))
    calls = []

    def fetch(ref):
        calls.append(ref)
        return view, "bundle:ns/b@4#r"

    out = resolve_bundle_srcs(stack_file, fetch=fetch)
    assert calls == ["ns/b@5#r"]
    assert out["layers"][0]["src"] == str(local.resolve())
    assert out["layers"][1]["src"] == str(view / "robots" / "r.xml")
    assert out["layers"][1]["pin"] == "bundle:ns/b@4#r"
    s = load_stack(out)
    require_resolved(s)


def test_resolve_model_outside_uses(tmp_path):
    view = tmp_path / "view"
    (view / "entries").mkdir(parents=True)
    (view / "entries" / "r.json").write_text('{"kind": "robot", "model": "robots/r.xml"}\n')
    with pytest.raises(ComposeError, match="declared uses"):
        resolve_bundle_srcs(stack({"tag": "Merge", "src": "bundle:ns/b@1#r"}),
                            fetch=lambda ref: (view, "bundle:ns/b@1#r"))


def test_resolve_against_server(tmp_path):
    bundles = FakeBundleServer().bundles(tmp_path / "cache")
    out = resolve_bundle_srcs(
        stack({"tag": "Attach", "src": "bundle:fortyfive/astribot-s1@4#wuji-right", "key": "right",
               "joint": "rigid"}),
        bundles=bundles)
    layer = out["layers"][0]
    assert layer["pin"] == "bundle:fortyfive/astribot-s1@2#wuji-right"
    assert Path(layer["src"]).name == "wuji.xml" and Path(layer["src"]).is_file()
    require_resolved(load_stack(out))
