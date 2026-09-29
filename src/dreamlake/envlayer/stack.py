"""Parse + validate ``dreamlake.env-layers/v3`` stack files.

The v3 schema is a **component grammar** (RFC 0007): every stack entry is
one flat object ``{"tag": ..., ...props}`` -- no ``source``/``compose``
wrappers, no mode-specific sub-schemas. Five tags:

* ``Merge``   ``{tag, src}`` -- union the env's MJCF into the stack;
* ``Attach``  ``{tag, src, key, joint, at?, pos?, quat?}`` -- graft the
  env's subtree under the identity root ``key`` (names become
  ``<key>:<name>``);
* ``Update``  ``{tag, key, ...attrs}`` -- inline sparse opinions: every
  prop besides ``tag``/``key`` is an MJCF attribute opinion on the
  addressed element;
* ``Remove``  ``{tag, key}`` -- delete the addressed element + subtree;
* ``Patch``   ``{tag, src}`` -- sparse-MJCF opinions from a file or env.

``src`` is ONE string, ESM-style: a bare ``ns/name[@v]`` is a registry env
ref; a string starting with ``./``, ``../`` or ``/`` is a local path (env
directory or single MJCF file). Element addresses (``Update.key``,
``Remove.key``, ``Attach.at``) share one syntax: ``kind:name``, or a bare
``name`` when it is unambiguous across kinds; ``option`` alone addresses
the singleton, ``visual:<sub>`` a visual sub-block.

Two forms share the schema:

* **authored** -- ``src`` may be a registry ref and versions may float;
* **resolved** -- every ``src`` is a local path (optionally carrying
  ``"pin": "ns/name@N"`` recording where it was pulled from).

``load_stack`` accepts both; the composition engine additionally calls
:func:`require_resolved`, which rejects registry refs -- resolution
(registry -> cache -> path) is the CLI's job, the engine is network-free.

Every validation error is a :class:`ComposeError` carrying the offending
layer index (``None`` for stack-level problems).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = "dreamlake.env-layers/v3"
#: The superseded schema id, recognized only to point at the migration map.
SCHEMA_V2 = "dreamlake.env-layers/v2"
SUBSTRATE = "mujoco"

TAGS = ("Merge", "Attach", "Update", "Remove", "Patch")
JOINT_MODES = ("rigid", "free", "free-anchored")

#: Named-element kinds ``Update`` may address (all have MjSpec finders and
#: settable attributes).
UPDATE_KINDS = ("body", "geom", "joint", "site", "camera", "light", "material")
#: Singleton kinds addressed by kind alone (``option``) or by sub-block
#: (``visual:<sub>``).
SINGLETON_KINDS = ("option", "visual")
#: Element kinds ``Remove`` may address (all have MjSpec finders and a
#: ``spec.delete`` overload).
DELETE_KINDS = (
    "body", "geom", "joint", "site", "camera", "light", "material",
    "mesh", "texture", "actuator", "sensor", "tendon", "equality", "key",
)

#: Update props that are identity/class plumbing, never opinions.
RESERVED_UPDATE_PROPS = ("name", "class", "childclass")

_TAG_KEYS = {
    "Merge": {"tag", "src", "pin", "unpinned"},
    "Attach": {"tag", "src", "key", "at", "pos", "quat", "joint",
               "pin", "unpinned"},
    "Remove": {"tag", "key"},
    "Patch": {"tag", "src", "pin", "unpinned"},
    # Update deliberately has no allow-list: its props ARE the opinions.
}

_ENV_REF_RE = re.compile(r"^[^\s/@]+/[^\s/@]+(@\d+)?$")
_PIN_RE = re.compile(r"^[^\s/@]+/[^\s/@]+@\d+$")

_V2_MIGRATION = (
    "the v2 {source, compose} schema is superseded by the v3 component "
    "grammar; migrate mechanically: merge -> {\"tag\": \"Merge\", \"src\"}; "
    "attach+prefix -> {\"tag\": \"Attach\", \"src\", \"key\"} (key without "
    "the trailing colon; one Attach entry per former instance); override "
    "env/file sources -> {\"tag\": \"Patch\", \"src\"}; small sparse-XML "
    "opinions -> inline {\"tag\": \"Update\", \"key\", ...attrs}; "
    "compose.delete -> {\"tag\": \"Remove\", \"key\": \"kind:name\"}; "
    "{\"env\"|\"path\"|\"file\"} sources -> one src string (local paths "
    "start with ./)"
)


class ComposeError(Exception):
    """A stack validation or composition failure.

    ``layer`` is the zero-based index of the offending layer, or ``None``
    when the problem is not attributable to a single layer.
    """

    def __init__(self, message: str, layer: int | None = None):
        super().__init__(message)
        self.layer = layer


def src_is_local(src: str) -> bool:
    """The ESM-style rule: local paths announce themselves with ``./``,
    ``../`` or an absolute prefix; every bare string is a registry ref."""
    return src.startswith(("./", "../")) or Path(src).is_absolute()


def parse_address(
    key: str, kinds: tuple[str, ...], singletons: tuple[str, ...] = (),
) -> tuple[str | None, str | None]:
    """Split an element address into ``(kind, name)``.

    ``"body:obj/mug"`` -> ``("body", "obj/mug")``; a bare name returns
    ``(None, name)`` (the caller probes kinds and errors on ambiguity);
    a singleton (``"option"``) returns ``(kind, None)``. Attach-made names
    contain colons (``right:palm``) -- only a *known kind* before the first
    colon is a qualifier, anything else is part of the name.
    """
    if key in singletons:
        return key, None
    head, sep, rest = key.partition(":")
    if sep and rest and head in (*kinds, *singletons):
        return head, rest
    return None, key


@dataclass(frozen=True)
class Layer:
    """One validated stack entry (the raw component retained for
    re-embedding into the pinned artifact copy)."""

    index: int
    data: dict

    @property
    def tag(self) -> str:
        return self.data["tag"]

    @property
    def src(self) -> str | None:
        return self.data.get("src")

    @property
    def pin(self) -> str | None:
        """``"ns/name@N"`` on resolved stacks whose src came from the
        registry."""
        return self.data.get("pin")

    @property
    def key(self) -> str | None:
        return self.data.get("key")

    @property
    def at(self) -> str:
        return self.data.get("at", "world")

    @property
    def pos(self) -> tuple[float, float, float]:
        return tuple(self.data.get("pos", (0.0, 0.0, 0.0)))

    @property
    def quat(self) -> tuple[float, float, float, float]:
        return tuple(self.data.get("quat", (1.0, 0.0, 0.0, 0.0)))  # wxyz

    @property
    def joint(self) -> str:
        return self.data["joint"]

    @property
    def props(self) -> dict:
        """An Update's opinions: every prop besides the component plumbing."""
        return {k: v for k, v in self.data.items() if k not in ("tag", "key")}

    def label(self) -> str:
        """Human-readable identity for warnings/errors."""
        if self.pin:
            return self.pin
        if self.src is not None:
            return self.src
        if self.key is not None:
            return self.key
        return f"layer {self.index}"


@dataclass(frozen=True)
class Stack:
    """A parsed, schema-validated layer stack."""

    entry: str
    layers: list[Layer]
    name: str | None = None
    substrate: str = SUBSTRATE
    #: Directory the stack file was loaded from (resolves relative local
    #: srcs); ``None`` when the stack came in as a plain dict.
    base_dir: Path | None = None
    raw: dict = field(default_factory=dict)

    @property
    def has_attach(self) -> bool:
        return any(layer.tag == "Attach" for layer in self.layers)


def _err(msg: str, layer: int | None = None) -> ComposeError:
    prefix = f"layer {layer}: " if layer is not None else ""
    return ComposeError(f"{prefix}{msg}", layer)


def _check_vec(value, n: int, what: str, i: int) -> tuple[float, ...]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != n
        or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
    ):
        raise _err(f"{what} must be a list of {n} numbers, got {value!r}", i)
    return tuple(float(v) for v in value)


def _validate_src(layer: dict, i: int) -> None:
    src = layer.get("src")
    if not isinstance(src, str) or not src:
        raise _err(f'{layer["tag"]} needs a non-empty "src" string', i)
    if not src_is_local(src):
        if not _ENV_REF_RE.match(src):
            raise _err(
                f"src {src!r} is neither a registry ref (ns/name[@v]) nor a "
                'local path -- local paths must start with "./", "../" or '
                '"/"', i)
    pin = layer.get("pin")
    if pin is not None and (not isinstance(pin, str) or not _PIN_RE.match(pin)):
        raise _err(f'"pin" must be "ns/name@N", got {pin!r}', i)
    unpinned = layer.get("unpinned")
    if unpinned is not None and not isinstance(unpinned, bool):
        raise _err(f'"unpinned" must be a boolean, got {unpinned!r}', i)


def _validate_attach(layer: dict, i: int) -> None:
    key = layer.get("key")
    if not isinstance(key, str) or not key:
        raise _err('Attach needs a non-empty "key" (the identity root: '
                   '"right" makes right:palm)', i)
    if ":" in key or any(c.isspace() for c in key):
        raise _err(
            f'Attach "key" must not contain ":" or whitespace, got {key!r} '
            "(the separator is added by the composer)", i)
    at = layer.get("at", "world")
    if not isinstance(at, str) or (
        at != "world"
        and not (at.startswith("body:") and len(at) > 5)
        and not (at.startswith("site:") and len(at) > 5)
    ):
        raise _err(
            f'Attach "at" must be "world", "body:<name>" or "site:<name>", '
            f"got {at!r}", i,
        )
    joint = layer.get("joint")
    if joint not in JOINT_MODES:
        raise _err(
            f'Attach needs "joint", one of {list(JOINT_MODES)}, got '
            f"{joint!r}", i)
    if "pos" in layer:
        _check_vec(layer["pos"], 3, 'Attach "pos"', i)
    if "quat" in layer:
        _check_vec(layer["quat"], 4, 'Attach "quat"', i)


def _validate_update(layer: dict, i: int) -> None:
    key = layer.get("key")
    if not isinstance(key, str) or not key:
        raise _err('Update needs a non-empty "key" (the element address: '
                   '"obj/mug", "body:obj/mug", "option", "visual:<sub>")', i)
    if key == "visual":
        raise _err(
            'Update key "visual" needs a sub-block: "visual:<sub>" '
            '(e.g. "visual:headlight")', i)
    props = {k: v for k, v in layer.items() if k not in ("tag", "key")}
    if not props:
        raise _err(
            "Update carries no opinions -- every prop besides tag/key is an "
            "MJCF attribute to set", i)
    for name in RESERVED_UPDATE_PROPS:
        if name in props:
            raise _err(
                f"Update cannot set {name!r} (identity/class plumbing, not "
                "an opinion)", i)
    for attr, value in props.items():
        ok = (
            isinstance(value, (str, bool, int, float))
            or (
                isinstance(value, (list, tuple)) and value
                and all(
                    isinstance(v, (int, float)) and not isinstance(v, bool)
                    for v in value
                )
            )
        )
        if not ok:
            raise _err(
                f"Update {key!r} attr {attr!r}: unsupported value {value!r} "
                "(want number, bool, string, or a list of numbers)", i)


def _validate_remove(layer: dict, i: int) -> None:
    key = layer.get("key")
    if not isinstance(key, str) or not key:
        raise _err('Remove needs a non-empty "key" (the element address: '
                   '"fixture/plant" or "body:fixture/plant")', i)
    kind, _name = parse_address(key, DELETE_KINDS)
    head, sep, _rest = key.partition(":")
    if kind is None and sep and head in SINGLETON_KINDS:
        raise _err(f"Remove cannot address {head!r} (singletons are not "
                   "removable)", i)


def load_stack(stack: dict | str | Path, base_dir: Path | None = None) -> Stack:
    """Parse and validate a stack (authored or resolved form).

    ``stack`` is a dict, or a path to a JSON file (whose directory then
    resolves relative local srcs).
    """
    if isinstance(stack, (str, Path)):
        path = Path(stack)
        if not path.is_file():
            raise ComposeError(f"no such stack file: {path}")
        base_dir = path.parent.resolve()
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            raise ComposeError(f"stack file {path} is not valid JSON: {e}") from e
    else:
        data = stack

    if not isinstance(data, dict):
        raise ComposeError(f"stack must be a JSON object, got {type(data).__name__}")
    schema = data.get("schema")
    if schema == SCHEMA_V2:
        raise ComposeError(f'stack "schema" is {SCHEMA_V2!r}: {_V2_MIGRATION}')
    if schema != SCHEMA:
        raise ComposeError(
            f'stack "schema" must be "{SCHEMA}", got {schema!r}')
    substrate = data.get("substrate", SUBSTRATE)
    if substrate != SUBSTRATE:
        raise ComposeError(
            f"unsupported substrate {substrate!r}: this engine composes "
            f'"{SUBSTRATE}" stacks only')
    entry = data.get("entry", "scene.xml")
    if not isinstance(entry, str) or not entry or "/" in entry or "\\" in entry:
        raise ComposeError(f'"entry" must be a bare file name, got {entry!r}')
    name = data.get("name")
    if name is not None and not isinstance(name, str):
        raise ComposeError(f'"name" must be a string, got {name!r}')

    raw_layers = data.get("layers")
    if not isinstance(raw_layers, list) or not raw_layers:
        raise ComposeError('stack needs a non-empty "layers" list')

    layers: list[Layer] = []
    seen_keys: dict[str, int] = {}
    for i, raw in enumerate(raw_layers):
        if not isinstance(raw, dict):
            raise _err(
                f"layer must be a component object {{\"tag\": ...}}, got "
                f"{type(raw).__name__}", i)
        tag = raw.get("tag")
        if tag not in TAGS:
            raise _err(f'"tag" must be one of {list(TAGS)}, got {tag!r}', i)
        if tag != "Update":
            unknown = set(raw) - _TAG_KEYS[tag]
            if unknown:
                raise _err(f"unknown {tag} keys {sorted(unknown)}", i)
        if tag in ("Merge", "Attach", "Patch"):
            _validate_src(raw, i)
        if tag == "Attach":
            _validate_attach(raw, i)
            prior = seen_keys.get(raw["key"])
            if prior is not None:
                raise _err(
                    f"duplicate Attach key {raw['key']!r} (already used by "
                    "layer {}) -- attach names would clash".format(prior), i)
            seen_keys[raw["key"]] = i
        elif tag == "Update":
            _validate_update(raw, i)
        elif tag == "Remove":
            _validate_remove(raw, i)
        layers.append(Layer(index=i, data=raw))

    return Stack(
        entry=entry,
        layers=layers,
        name=name,
        substrate=substrate,
        base_dir=base_dir,
        raw=data,
    )


def require_resolved(stack: Stack) -> None:
    """Reject registry-ref srcs: the engine composes RESOLVED stacks only."""
    for layer in stack.layers:
        if layer.src is not None and not src_is_local(layer.src):
            raise _err(
                f"src {layer.src!r} is unresolved -- resolve refs first: the "
                "engine composes only resolved stacks where every src is a "
                "local path (the DreamLake CLI pulls registry refs into the "
                "cache and rewrites them)",
                layer.index,
            )
