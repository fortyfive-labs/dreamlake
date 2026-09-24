"""Deterministic ``dreamlake.yml`` emission (no YAML dependency).

The split authoring model keeps the user's source directory clean: the
asset files plus ONE optional ``dreamlake.yml`` carrying the metadata
the CLI cannot infer. Everything generated (wire manifest, thumbnails,
vectors) is produced at push time into a cache dir and uploaded under
the reserved ``files/.dreamlake/`` prefix -- never written into the
source tree.

Document shape (all keys optional; only keys with values are written)::

    library:
      title / description / provider / homepage / license / tags
      upstream: {repo, commit}
    defaults:
      category            # hoisted when uniform across all assets
    assets:
      <id>:
        title / description / category / tags / license / attribution
        entry
        entryPoints: {<name>: {kind, file}}
        upstream: {id, url}

pyyaml is NOT a base dependency of this package (only a transitive dep
of the ``embed``/``search``/``dev`` extras), so :func:`dump_yaml` is a
tiny emitter instead: block-style mappings and sequences, insertion
(key) order preserved, scalars written plain when unambiguous and
double-quoted via ``json.dumps`` otherwise (YAML's double-quoted style
is a superset of JSON string syntax, so the output is valid YAML that
any real parser reads back verbatim).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

DREAMLAKE_YML = "dreamlake.yml"

#: strings safe to write as plain (unquoted) YAML scalars: leading
#: alphanumeric, then a conservative charset with no ``:`` or ``#``
#: (the two characters that can terminate a plain scalar mid-string)
_PLAIN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _,;'()./+-]*")

#: plain-looking strings a YAML 1.1 parser would type-coerce anyway
_AMBIGUOUS = {
    "true", "false", "yes", "no", "on", "off", "null", "none", "~",
}
_NUMBER_RE = re.compile(r"[-+]?(\d[\d_]*\.?\d*|\.\d+)([eE][-+]?\d+)?")


def _scalar(value: Any) -> str:
    """One YAML scalar token; strings quoted only when they must be."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if not isinstance(value, str):
        raise TypeError(f"unsupported scalar type: {type(value).__name__}")
    if (
        _PLAIN_RE.fullmatch(value)
        and not value.endswith(" ")
        and value.lower() not in _AMBIGUOUS
        and not _NUMBER_RE.fullmatch(value)
    ):
        return value
    # json.dumps produces a valid YAML double-quoted scalar (same
    # escape rules); ensure_ascii=False keeps titles like "Pokémon"
    # readable in the UTF-8 file.
    return json.dumps(value, ensure_ascii=False)


def _emit_map(doc: dict, indent: int, lines: list[str]) -> None:
    pad = "  " * indent
    for key, value in doc.items():
        k = _scalar(key)
        if isinstance(value, dict):
            if not value:
                lines.append(f"{pad}{k}: {{}}")
            else:
                lines.append(f"{pad}{k}:")
                _emit_map(value, indent + 1, lines)
        elif isinstance(value, (list, tuple)):
            if not value:
                lines.append(f"{pad}{k}: []")
            else:
                lines.append(f"{pad}{k}:")
                for item in value:
                    if isinstance(item, (dict, list, tuple)):
                        raise TypeError(
                            "sequences of collections are not supported "
                            "in dreamlake.yml")
                    lines.append(f"{pad}  - {_scalar(item)}")
        else:
            lines.append(f"{pad}{k}: {_scalar(value)}")


def dump_yaml(doc: dict) -> str:
    """Serialize ``doc`` to deterministic block-style YAML text."""
    if not isinstance(doc, dict):
        raise TypeError("dump_yaml expects a dict document")
    if not doc:
        return "{}\n"
    lines: list[str] = []
    _emit_map(doc, 0, lines)
    return "\n".join(lines) + "\n"


def prune(doc: dict) -> dict:
    """Drop ``None``/empty-collection values, preserving key order.

    ``dreamlake.yml`` only ever writes keys that have values.
    """
    return {
        k: v for k, v in doc.items()
        if v is not None and v != "" and v != [] and v != {}
    }


def hoist_defaults(
    assets: dict[str, dict], keys: tuple[str, ...] = ("category",),
) -> dict:
    """Move per-asset keys uniform across ALL assets into ``defaults``.

    Mutates ``assets`` (the hoisted key is removed from every asset) and
    returns the ``defaults`` mapping (possibly empty). A key hoists only
    when every asset carries the same non-``None`` value for it.
    """
    defaults: dict[str, Any] = {}
    if not assets:
        return defaults
    for key in keys:
        values = {a.get(key) for a in assets.values()}
        if len(values) == 1:
            value = next(iter(values))
            if value is not None:
                defaults[key] = value
                for asset in assets.values():
                    asset.pop(key, None)
    return defaults


def write_dreamlake_yml(doc: dict, out_dir: str | Path) -> Path:
    """Write ``<out_dir>/dreamlake.yml``; returns its path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / DREAMLAKE_YML
    path.write_text(dump_yaml(doc), encoding="utf-8")
    return path
