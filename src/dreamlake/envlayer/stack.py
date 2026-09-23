"""Parse + validate ``dreamlake.env-layers/v2`` stack files.

Two forms share the schema:

* **authored** -- layer sources may be registry refs (``{"env": "ns/name"}``
  or ``ns/name@3``) and versions may float;
* **resolved** -- every source is a local ``{"path": "<abs>"}`` (optionally
  carrying ``"pin": {"env": "ns/name", "version": N}`` recording where the
  path was pulled from) or a ``{"file": "..."}`` inside the stack directory.

``load_stack`` accepts both; the composition engine additionally calls
:func:`require_resolved`, which rejects ``env`` sources -- resolution
(registry -> cache -> path) is the CLI's job, the engine is network-free.

Every validation error is a :class:`ComposeError` carrying the offending
layer index (``None`` for stack-level problems).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = "dreamlake.env-layers/v2"
SUBSTRATE = "mujoco"

MODES = ("merge", "attach", "override")
JOINT_MODES = ("rigid", "free", "free-anchored")

#: Element kinds ``compose.delete`` may name (all have MjSpec finders and a
#: ``spec.delete`` overload).
DELETE_KINDS = (
    "body", "geom", "joint", "site", "camera", "light", "material",
    "mesh", "texture", "actuator", "sensor", "tendon", "equality", "key",
)

_SOURCE_KEYS = {"env", "path", "file", "pin"}
_COMPOSE_KEYS = {
    "merge": {"mode"},
    "attach": {"mode", "prefix", "at", "pos", "quat", "joint", "instances"},
    "override": {"mode", "delete"},
}
_INSTANCE_KEYS = {"prefix", "pos", "quat"}


class ComposeError(Exception):
    """A stack validation or composition failure.

    ``layer`` is the zero-based index of the offending layer, or ``None``
    when the problem is not attributable to a single layer.
    """

    def __init__(self, message: str, layer: int | None = None):
        super().__init__(message)
        self.layer = layer


@dataclass(frozen=True)
class Instance:
    """One placement of an attach layer's source."""

    prefix: str
    pos: tuple[float, float, float] = (0.0, 0.0, 0.0)
    quat: tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0)  # wxyz


@dataclass(frozen=True)
class Layer:
    """One validated stack entry (raw dicts retained for re-embedding)."""

    index: int
    source: dict
    compose: dict

    @property
    def mode(self) -> str:
        return self.compose["mode"]

    @property
    def instances(self) -> list[Instance]:
        """The attach placements (a single-placement attach normalized to
        a one-element list)."""
        if "instances" in self.compose:
            return [
                Instance(
                    prefix=inst["prefix"],
                    pos=tuple(inst.get("pos", (0.0, 0.0, 0.0))),
                    quat=tuple(inst.get("quat", (1.0, 0.0, 0.0, 0.0))),
                )
                for inst in self.compose["instances"]
            ]
        return [
            Instance(
                prefix=self.compose["prefix"],
                pos=tuple(self.compose.get("pos", (0.0, 0.0, 0.0))),
                quat=tuple(self.compose.get("quat", (1.0, 0.0, 0.0, 0.0))),
            )
        ]

    def label(self) -> str:
        """Human-readable identity for warnings/errors."""
        pin = self.source.get("pin")
        if pin:
            return f"{pin.get('env')}@{pin.get('version')}"
        for key in ("env", "path", "file"):
            if key in self.source:
                return str(self.source[key])
        return f"layer {self.index}"


@dataclass(frozen=True)
class Stack:
    """A parsed, schema-validated layer stack."""

    entry: str
    layers: list[Layer]
    name: str | None = None
    substrate: str = SUBSTRATE
    #: Directory the stack file was loaded from (resolves ``file`` sources);
    #: ``None`` when the stack came in as a plain dict.
    base_dir: Path | None = None
    raw: dict = field(default_factory=dict)

    @property
    def has_attach(self) -> bool:
        return any(layer.mode == "attach" for layer in self.layers)


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


def _validate_source(source, i: int) -> dict:
    if not isinstance(source, dict):
        raise _err(f'"source" must be an object, got {type(source).__name__}', i)
    unknown = set(source) - _SOURCE_KEYS
    if unknown:
        raise _err(f"unknown source keys {sorted(unknown)}", i)
    forms = [k for k in ("env", "path", "file") if k in source]
    if len(forms) != 1:
        raise _err(
            'source must carry exactly one of "env", "path", "file", '
            f"got {forms or 'none'}", i,
        )
    key = forms[0]
    if not isinstance(source[key], str) or not source[key]:
        raise _err(f'source "{key}" must be a non-empty string', i)
    pin = source.get("pin")
    if pin is not None:
        if key != "path":
            raise _err('"pin" is only meaningful on a "path" source', i)
        if (
            not isinstance(pin, dict)
            or not isinstance(pin.get("env"), str)
            or not isinstance(pin.get("version"), int)
        ):
            raise _err(
                'source "pin" must be {"env": "ns/name", "version": N}', i)
    return source


def _validate_attach(compose: dict, i: int) -> None:
    at = compose.get("at", "world")
    if not isinstance(at, str) or (
        at != "world"
        and not (at.startswith("body:") and len(at) > 5)
        and not (at.startswith("site:") and len(at) > 5)
    ):
        raise _err(
            f'attach "at" must be "world", "body:<name>" or "site:<name>", '
            f"got {at!r}", i,
        )
    joint = compose.get("joint", "rigid")
    if joint not in JOINT_MODES:
        raise _err(
            f'attach "joint" must be one of {list(JOINT_MODES)}, got {joint!r}', i)

    if "instances" in compose:
        for key in ("prefix", "pos", "quat"):
            if key in compose:
                raise _err(
                    f'attach "instances" replaces "{key}" -- move it into '
                    "the instance entries", i,
                )
        instances = compose["instances"]
        if not isinstance(instances, list) or not instances:
            raise _err('attach "instances" must be a non-empty list', i)
        for j, inst in enumerate(instances):
            if not isinstance(inst, dict):
                raise _err(f"instances[{j}] must be an object", i)
            unknown = set(inst) - _INSTANCE_KEYS
            if unknown:
                raise _err(f"instances[{j}]: unknown keys {sorted(unknown)}", i)
            if not isinstance(inst.get("prefix"), str) or not inst["prefix"]:
                raise _err(
                    f'instances[{j}] needs a non-empty "prefix"', i)
            if "pos" in inst:
                _check_vec(inst["pos"], 3, f'instances[{j}] "pos"', i)
            if "quat" in inst:
                _check_vec(inst["quat"], 4, f'instances[{j}] "quat"', i)
    else:
        if not isinstance(compose.get("prefix"), str) or not compose["prefix"]:
            raise _err('attach needs a non-empty "prefix" (or "instances")', i)
        if "pos" in compose:
            _check_vec(compose["pos"], 3, 'attach "pos"', i)
        if "quat" in compose:
            _check_vec(compose["quat"], 4, 'attach "quat"', i)


def _validate_override(compose: dict, i: int) -> None:
    delete = compose.get("delete")
    if delete is None:
        return
    if not isinstance(delete, list):
        raise _err('override "delete" must be a list of {"elem", "name"}', i)
    for j, entry in enumerate(delete):
        if (
            not isinstance(entry, dict)
            or set(entry) != {"elem", "name"}
            or entry["elem"] not in DELETE_KINDS
            or not isinstance(entry["name"], str)
            or not entry["name"]
        ):
            raise _err(
                f'delete[{j}] must be {{"elem": <kind>, "name": <name>}} with '
                f"elem one of {list(DELETE_KINDS)}, got {entry!r}", i,
            )


def load_stack(stack: dict | str | Path, base_dir: Path | None = None) -> Stack:
    """Parse and validate a stack (authored or resolved form).

    ``stack`` is a dict, or a path to a JSON file (whose directory then
    resolves relative ``file`` sources).
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
    seen_prefixes: dict[str, int] = {}
    for i, raw in enumerate(raw_layers):
        if not isinstance(raw, dict):
            raise _err(f"layer must be an object, got {type(raw).__name__}", i)
        source = _validate_source(raw.get("source"), i)
        compose = raw.get("compose")
        if not isinstance(compose, dict):
            raise _err('layer needs a "compose" object', i)
        mode = compose.get("mode")
        if mode not in MODES:
            raise _err(
                f'compose "mode" must be one of {list(MODES)}, got {mode!r}', i)
        unknown = set(compose) - _COMPOSE_KEYS[mode]
        if unknown:
            raise _err(
                f"unknown compose keys for mode {mode!r}: {sorted(unknown)}", i)
        if mode == "attach":
            _validate_attach(compose, i)
        elif mode == "override":
            _validate_override(compose, i)
        layer = Layer(index=i, source=source, compose=compose)
        if mode == "attach":
            for inst in layer.instances:
                prior = seen_prefixes.get(inst.prefix)
                if prior is not None:
                    raise _err(
                        f"duplicate attach prefix {inst.prefix!r} (already "
                        f"used by layer {prior}) -- attach names would clash",
                        i,
                    )
                seen_prefixes[inst.prefix] = i
        layers.append(layer)

    return Stack(
        entry=entry,
        layers=layers,
        name=name,
        substrate=substrate,
        base_dir=base_dir,
        raw=data,
    )


def require_resolved(stack: Stack) -> None:
    """Reject registry-ref sources: the engine composes RESOLVED stacks only."""
    for layer in stack.layers:
        if "env" in layer.source:
            raise _err(
                f"source {{\"env\": {layer.source['env']!r}}} is unresolved -- "
                "resolve refs first: the engine composes only resolved stacks "
                'where every source is a local {"path": ...} (the DreamLake '
                "CLI pulls registry refs into the cache and rewrites them)",
                layer.index,
            )
