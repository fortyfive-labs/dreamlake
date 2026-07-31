"""Declarative schemas for custom datasets — the ``fields`` wire format.

:class:`Schema` mirrors ``dreamdb.Schema`` one-to-one — same method names,
same parameters, zero translation layer — with exactly two twists it exists
for:

1. ``dreamdb.Schema`` cannot be introspected once built (declarations go in,
   nothing comes back out), so this Schema RECORDS every declaration as a
   serializable ``fields`` list. That list is the wire format shared with
   manifests and the catalog's ``schemaJson``, and it is what the dataset
   stamps into its space meta (:data:`FIELDS_META_KEY`) so ``ds.tracks()``
   can answer without an engine introspection API.
2. Every field is pinned ``required=False``. A required field can never be
   added later without invalidating every existing record, so all-optional
   is what keeps schema evolution (``ds.add_track``) available — the same
   rule the video-annotation preset lives by.

JSON documents are declared the dreamdb way — ``add_image(name,
mime="json")`` — on purpose: one vocabulary end to end, no SDK-only alias
to unlearn when dropping down to ``ds.db``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ._errors import DatasetError, SchemaError

# Track/field names: one flat, lowercase vocabulary. The reserved names are
# load-bearing: "anchor" is the row key of the SDK's row-wise API, and
# "_anchor"/"_time_anchors" are dreamdb's own reserved sample/batch keys — a
# field with any of these names would shadow the time axis itself.
FIELD_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
RESERVED_FIELD_NAMES = ("anchor", "_anchor", "_time_anchors")

# The kind vocabulary — dreamdb's, verbatim (the kind string IS the dreamdb
# method suffix). "audio" is deliberately absent: append_many cannot ingest
# it yet, and a declarable-but-unfillable field would only mislead.
TRACK_KINDS = (
    "video", "image", "embedding",
    "scalar_float", "scalar_int", "scalar_bool",
    "scalar_string", "scalar_categorical", "scalar_timestamp",
)
# What add_track can declare post-create: everything except embedding (the
# LSH index is part of the schema — a create-time-only DreamDB constraint).
EVOLVABLE_KINDS = tuple(k for k in TRACK_KINDS if k != "embedding")

# Space-meta key of the fields mirror — the generic layer's own registry of
# declared tracks, maintained because the engine has no field-enumeration
# API. dreamdb.set_meta only accepts the ``dreamdb.`` prefix, hence not
# ``dreamlake.*``. Known limits (documented contract): fields added through
# the bare ``ds.db`` handle are invisible here, and another process's
# add_track is invisible until ``ds.reload()``.
FIELDS_META_KEY = "dreamdb.dataset.fields"

# The default schemaType stamped on custom datasets. It has no special
# status anywhere — the UI rule is "unknown schemaType → raw view" — it is
# just the value used when the user does not name their own.
CUSTOM_SCHEMA_TYPE = "custom/v1"

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def dumps_compact(obj: Any) -> str:
    """JSON for the wire. Two deliberate deviations from json.dumps
    defaults: no separator whitespace (a large joints doc carries hundreds
    of thousands of separators — the default ", "/": " spaces are real
    bytes on S3), and ``ensure_ascii=False`` (escaping non-ASCII labels to
    \\uXXXX sextuples their size; every consumer is a JSON parser and
    handles UTF-8). Still plain valid JSON — readers are unaffected."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def to_anchor_ns(value: Any, *, what: str = "anchor") -> int:
    """``value`` as absolute int nanoseconds. Accepts int or a tz-aware
    ``datetime``; everything else — naive datetimes above all — is refused
    rather than guessed (a wrong timezone assumption corrupts silently)."""
    if isinstance(value, bool):
        raise DatasetError(f"{what} must be int nanoseconds or a tz-aware datetime, got bool")
    if isinstance(value, int):
        if value < 0:
            raise DatasetError(f"{what} must be >= 0 nanoseconds, got {value}")
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise DatasetError(
                f"{what} datetime is naive — pass a tz-aware datetime "
                f"(e.g. datetime(..., tzinfo=timezone.utc)) or int nanoseconds"
            )
        delta = value - _EPOCH
        # Integer math end to end: float .timestamp() loses ns precision.
        return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000
    raise DatasetError(
        f"{what} must be int nanoseconds or a tz-aware datetime, got {type(value).__name__}"
    )


def sequence_anchors(n: int, *, start: int = 0, step: int = 1) -> List[int]:
    """Deterministic anchors for sequential data with no timestamps of its
    own: anchor == row index (``start + i*step``). To continue an existing
    dataset: ``start=ds.anchors()[-1] + 1``. Explicit on purpose — the SDK
    never infers anchors from arrival order (not stable across re-runs)."""
    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        raise DatasetError(f"sequence_anchors needs a non-negative int count, got {n!r}")
    if not isinstance(step, int) or isinstance(step, bool) or step <= 0:
        raise DatasetError(f"sequence_anchors step must be a positive int, got {step!r}")
    base = to_anchor_ns(start, what="sequence_anchors start")
    return [base + i * step for i in range(n)]


def validate_field_name(name: Any) -> None:
    if not isinstance(name, str) or not FIELD_RE.match(name):
        raise SchemaError(
            f"field name {name!r} must be 1-64 chars of lowercase letters, "
            f"digits or '_', starting with a letter or digit"
        )
    if name in RESERVED_FIELD_NAMES:
        raise SchemaError(
            f"field name '{name}' is reserved — 'anchor' is the row key of the "
            f"row-wise API and '_anchor'/'_time_anchors' belong to dreamdb itself"
        )


class Schema:
    """A recorded ``dreamdb.Schema``: same ``add_*`` names and parameters,
    fail-early validation, and a ``fields`` list you can serialize.

    Methods chain (they return ``self``), exactly like dreamdb's."""

    def __init__(self) -> None:
        self._fields: List[Dict[str, Any]] = []

    # ---- shared declaration plumbing ------------------------------------

    def _names(self) -> set:
        return {f["name"] for f in self._fields}

    def _reject_required(self, required: bool, name: str) -> None:
        if required:
            raise SchemaError(
                f"field '{name}': required=True is not supported — a required "
                f"field can never be added later, so every field is optional "
                f"(that is what keeps ds.add_track available). Omit required."
            )

    def _declare(self, spec: Dict[str, Any]) -> "Schema":
        validate_field_name(spec.get("name"))
        if spec["name"] in self._names():
            raise SchemaError(f"duplicate field '{spec['name']}'")
        self._fields.append({k: v for k, v in spec.items() if v is not None})
        return self

    # ---- the dreamdb.Schema surface, verbatim ---------------------------

    def add_video(self, name: str, mime: str = "mp4", required: bool = False,
                  chunk_size: Optional[int] = None,
                  pack_items: Optional[int] = None) -> "Schema":
        self._reject_required(required, name)
        if not mime or not isinstance(mime, str):
            raise SchemaError(f"video field '{name}' needs a mime string (e.g. 'h264')")
        return self._declare({"name": name, "type": "video", "mime": mime,
                              "chunk_size": chunk_size, "pack_items": pack_items})

    def add_image(self, name: str, mime: str = "jpeg", required: bool = False,
                  chunk_size: Optional[int] = None,
                  pack_items: Optional[int] = None) -> "Schema":
        self._reject_required(required, name)
        if not mime or not isinstance(mime, str):
            raise SchemaError(f"image field '{name}' needs a mime string (e.g. 'jpeg', 'json')")
        return self._declare({"name": name, "type": "image", "mime": mime,
                              "chunk_size": chunk_size, "pack_items": pack_items})

    def add_audio(self, name: str, *args, **kwargs) -> "Schema":
        raise SchemaError(
            f"audio field '{name}': audio is not supported yet — dreamdb's "
            f"append_many cannot ingest it, so declaring it would only "
            f"produce a field you can never fill. Store the bytes on an "
            f"image field (with an audio mime) if you must."
        )

    def add_embedding(self, name: str, dim: int,
                      algorithm: str = "dreamdb.lsh-cosine",
                      required: bool = False,
                      lsh_bits: Optional[int] = None,
                      compressor: Optional[str] = None,
                      spatial_index: Optional[str] = None,
                      rerank: bool = False) -> "Schema":
        self._reject_required(required, name)
        if not isinstance(dim, int) or isinstance(dim, bool) or dim <= 0:
            raise SchemaError(f"embedding field '{name}' needs a positive int dim, got {dim!r}")
        return self._declare({
            "name": name, "type": "embedding", "dim": dim,
            "algorithm": None if algorithm == "dreamdb.lsh-cosine" else algorithm,
            "lsh_bits": lsh_bits, "compressor": compressor,
            "spatial_index": spatial_index, "rerank": rerank or None,
        })

    def add_scalar_float(self, name: str, required: bool = False) -> "Schema":
        self._reject_required(required, name)
        return self._declare({"name": name, "type": "scalar_float"})

    def add_scalar_int(self, name: str, required: bool = False) -> "Schema":
        self._reject_required(required, name)
        return self._declare({"name": name, "type": "scalar_int"})

    def add_scalar_bool(self, name: str, required: bool = False) -> "Schema":
        self._reject_required(required, name)
        return self._declare({"name": name, "type": "scalar_bool"})

    def add_scalar_string(self, name: str, required: bool = False) -> "Schema":
        self._reject_required(required, name)
        return self._declare({"name": name, "type": "scalar_string"})

    def add_scalar_categorical(self, name: str, required: bool = False) -> "Schema":
        self._reject_required(required, name)
        return self._declare({"name": name, "type": "scalar_categorical"})

    def add_scalar_timestamp(self, name: str, required: bool = False) -> "Schema":
        self._reject_required(required, name)
        return self._declare({"name": name, "type": "scalar_timestamp"})

    # ---- wire format round-trip -----------------------------------------

    @classmethod
    def from_fields(cls, fields: Any) -> "Schema":
        """A Schema from the ``fields`` wire format (list of ``{"name",
        "type", ...}`` dicts — the same block manifests carry). Validation is
        the add_* methods' — from_fields routes through them."""
        if not isinstance(fields, list):
            raise SchemaError(f"fields must be a list of field dicts, got {type(fields).__name__}")
        sch = cls()
        for i, f in enumerate(fields):
            if not isinstance(f, dict) or "name" not in f or "type" not in f:
                raise SchemaError(f"fields[{i}] needs at least 'name' and 'type'")
            kind = f["type"]
            if kind == "audio":
                sch.add_audio(f["name"])  # raises with the full explanation
            elif kind == "video":
                if "mime" not in f:
                    raise SchemaError(f"video field '{f['name']}' needs a mime")
                sch.add_video(f["name"], mime=f["mime"],
                              chunk_size=f.get("chunk_size"), pack_items=f.get("pack_items"))
            elif kind == "image":
                if "mime" not in f:
                    raise SchemaError(f"image field '{f['name']}' needs a mime")
                sch.add_image(f["name"], mime=f["mime"],
                              chunk_size=f.get("chunk_size"), pack_items=f.get("pack_items"))
            elif kind == "embedding":
                if "dim" not in f:
                    raise SchemaError(f"embedding field '{f['name']}' needs an int dim")
                sch.add_embedding(f["name"], f["dim"],
                                  algorithm=f.get("algorithm") or "dreamdb.lsh-cosine",
                                  lsh_bits=f.get("lsh_bits"), compressor=f.get("compressor"),
                                  spatial_index=f.get("spatial_index"),
                                  rerank=bool(f.get("rerank")))
            elif kind in TRACK_KINDS:
                getattr(sch, f"add_{kind}")(f["name"])
            else:
                raise SchemaError(
                    f"fields[{i}] has unknown type '{kind}' — one of {list(TRACK_KINDS)}"
                )
        return sch

    def to_fields(self) -> List[Dict[str, Any]]:
        """The declarations as the wire format (deep-copied)."""
        return [dict(f) for f in self._fields]

    # ---- compilation -----------------------------------------------------

    def _compile(self):
        """The equivalent ``dreamdb.Schema``, built fresh. Internal: the
        compiled object is write-only, callers keep using this one."""
        from dreamlake import db as _db

        dreamdb = _db._dreamdb()
        out = dreamdb.Schema()
        for f in self._fields:
            kind = f["type"]
            if kind in ("video", "image"):
                kwargs: Dict[str, Any] = {"mime": f["mime"], "required": False}
                if f.get("chunk_size") is not None:
                    kwargs["chunk_size"] = f["chunk_size"]
                if f.get("pack_items") is not None:
                    kwargs["pack_items"] = f["pack_items"]
                getattr(out, f"add_{kind}")(f["name"], **kwargs)
            elif kind == "embedding":
                out.add_embedding(
                    f["name"], f["dim"],
                    algorithm=f.get("algorithm") or "dreamdb.lsh-cosine",
                    required=False, lsh_bits=f.get("lsh_bits"),
                    compressor=f.get("compressor"),
                    spatial_index=f.get("spatial_index"),
                    rerank=bool(f.get("rerank")),
                )
            else:
                getattr(out, f"add_{kind}")(f["name"], required=False)
        return out

    def __repr__(self) -> str:
        return f"Schema({[f['name'] for f in self._fields]})"
