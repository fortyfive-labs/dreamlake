"""Resolve ``bundle:ns/name@v#entry`` srcs of a layer stack.

The composition engine is network-free and composes only resolved stacks.
:func:`resolve_bundle_srcs` is the step before it: for each bundle src it
fetches the entry (declared prefixes only) into the bundle cache, and
writes the local path of the entry ``model`` file as ``src`` and the
normalized ref ``bundle:ns/name@W#entry`` as ``pin``. Other srcs do not
change.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from .stack import ComposeError, load_stack, src_is_bundle


def resolve_bundle_srcs(
    stack: dict | str | Path,
    *,
    bundles=None,
    fetch: Callable[[str], tuple[Path, str]] | None = None,
) -> dict:
    """Return a copy of the stack (as a dict) with bundle srcs resolved.

    ``bundles`` is a :class:`dreamlake.bundles.Bundles` (default client from
    the environment). ``fetch`` replaces the network step in tests: it takes
    the ref and returns ``(local view directory, pin)``.

    The local src is the entry ``model`` file in the view. An entry without
    ``model`` resolves to the view directory (the engine then reads it in
    the standard env shape). Relative local srcs of a stack file become
    absolute, because the result has no base directory.
    """
    if isinstance(stack, (str, Path)):
        path = Path(stack)
        data = json.loads(path.read_text())
        base_dir = path.parent.resolve()
    else:
        data = stack
        base_dir = None
    load_stack(copy.deepcopy(data), base_dir)  # validate first
    out = copy.deepcopy(data)
    if fetch is None:
        fetch = _default_fetch(bundles)
    for i, layer in enumerate(out.get("layers", [])):
        src = layer.get("src")
        if not isinstance(src, str):
            continue
        if src_is_bundle(src):
            try:
                view, pin = fetch(src[len("bundle:"):])
            except Exception as e:
                raise ComposeError(f"layer {i}: cannot fetch {src}: {e}", i) from e
            layer["src"] = str(_entry_file(Path(view), pin, i))
            layer["pin"] = pin
            layer.pop("unpinned", None)
        elif base_dir is not None and src.startswith(("./", "../")):
            layer["src"] = str((base_dir / src).resolve())
    return out


def _entry_file(view: Path, pin: str, i: int) -> Path:
    from dreamlake.bundles import hashing as h

    entry = h.parse_ref(pin).entry
    manifest_file = view / h.manifest_path(entry)
    if not manifest_file.is_file():
        raise ComposeError(f"layer {i}: {pin}: no manifest {h.manifest_path(entry)} in {view}", i)
    manifest: dict[str, Any] = h.load_manifest(manifest_file.read_bytes())
    model = manifest.get("model")
    if model is None:
        return view
    if not isinstance(model, str):
        raise ComposeError(f"layer {i}: {pin}: model must be a string", i)
    target = view.joinpath(*PurePosixPath(h.check_path(model)).parts)
    if not target.is_file():
        raise ComposeError(
            f"layer {i}: {pin}: model {model} is not in the fetched view "
            "(is it inside a declared uses prefix?)", i)
    return target


def _default_fetch(bundles) -> Callable[[str], tuple[Path, str]]:
    def run(ref: str) -> tuple[Path, str]:
        from dreamlake.bundles import Bundles

        client = bundles if bundles is not None else Bundles()
        res = client.resolve(ref)
        if res.entry is None:
            raise ComposeError(f"bundle src needs #entry: {ref}")
        return client.fetch(res), "bundle:" + res.ref

    return run
