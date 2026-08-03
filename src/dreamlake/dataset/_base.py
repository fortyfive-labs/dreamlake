"""The generic ``Dataset`` — DreamLake platform datasets of ANY schema.

Every DreamLake dataset is a catalog row plus a dreamdb space; what varies
is the ``schemaType`` string the row carries. Known types get a preset
subclass with rich methods (``VideoAnnotationDataset`` for
``video.annotation/v2``); everything else — user-defined schemas above all
— gets THIS class: declare tracks, append rows or ranges, read them back.
``Dataset.open`` dispatches on the catalog's schemaType and an unknown type
NEVER refuses, it degrades to this generic handle — the same rule the web
viewer applies (unknown → raw view).

Names are ``"name"`` (the login's own namespace) or ``"namespace/name"``
(an org the caller belongs to); the server authorizes per request.

Write semantics are append-only and write-once per (anchor, track) — see
``_track``. Concurrency: one writer per dataset (every append advances the
``main`` ref; concurrent writers lose updates). Credentials are a 12h lease
brokered at open; a stale handle raises with ``ds.reload()`` as the fix.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, Iterable, List, Optional, Sequence, Type

from ._errors import DatasetError, SchemaError
from ._fields import (
    CUSTOM_SCHEMA_TYPE,
    EVOLVABLE_KINDS,
    FIELDS_META_KEY,
    Schema,
    to_anchor_ns,
    validate_field_name,
)
from ._track import Track

# schemaType → preset subclass. Populated by __init_subclass__; private —
# no public for_type until a real consumer exists.
_REGISTRY: Dict[str, Type["Dataset"]] = {}


@dataclass(frozen=True)
class DatasetInfo:
    """One catalog listing — the server's summary shape plus the namespace
    it was listed from. (schemaJson/bucket exist only on the detail GET.)"""

    name: str
    namespace: str
    schema_type: str
    visibility: str


class Dataset:
    """A DreamLake platform dataset with a user-defined schema.

    Obtain instances through the classmethods, never the constructor::

        ds = Dataset.create("sensor-logs")              # empty, add tracks as you go
        ds = Dataset.create("clips", schema=sch, schema_type="acme.clips/v1")
        ds = Dataset.ensure("sensor-logs")              # open-or-create
        ds = Dataset.open("acme/sensor-logs")           # org namespace; dispatches by schemaType
    """

    # The dispatch key a preset subclass claims. None on this generic base.
    SCHEMA_TYPE: ClassVar[Optional[str]] = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        st = cls.__dict__.get("SCHEMA_TYPE")
        if st:
            _REGISTRY[st] = cls

    def __init__(self, inner, *, namespace: Optional[str], name: Optional[str],
                 row: Optional[Dict[str, Any]] = None):
        self._ds = inner
        # Plain attributes, not properties — the preset subclass assigns
        # them too (its platform/self-hosted duality sets name directly).
        self.namespace = namespace
        self.name = name
        self._row = row or {}
        self._fields: List[Dict[str, Any]] = self._load_fields_mirror()

    # ---- construction ----------------------------------------------------

    @classmethod
    def create(cls, name: str, *, schema: Optional[Schema] = None,
               schema_type: Optional[str] = None,
               visibility: str = "private") -> "Dataset":
        """Create a custom-schema dataset. ``schema=None`` starts empty —
        declare tracks as you go with :meth:`add_track` (embeddings are the
        exception: they exist only if declared here, in ``schema=``).
        ``schema_type`` is your own dispatch label for the catalog/UI;
        defaults to ``"custom/v1"``. Presets are created through their own
        class (e.g. ``VideoAnnotationDataset.create``), not here."""
        from dreamlake import _platform

        if cls.SCHEMA_TYPE is not None:
            raise DatasetError(
                f"{cls.__name__}.create has its own signature — this generic "
                f"create is for custom-schema datasets only"
            )
        st = schema_type or CUSTOM_SCHEMA_TYPE
        target = _REGISTRY.get(st)
        if target is not None:
            raise DatasetError(
                f"schema_type '{st}' is the {target.__name__} preset — create it "
                f"with {target.__name__}.create(...), which owns its schema"
            )
        if schema is None:
            schema = Schema()
        if not isinstance(schema, Schema):
            raise DatasetError(
                f"schema must be a dreamlake.dataset.Schema (got "
                f"{type(schema).__name__}) — build one with Schema() or "
                f"Schema.from_fields([...])"
            )
        fields = schema.to_fields()
        try:
            inner = _platform.create_dataset(
                name, schema._compile(), schema_type=st,
                schema_json=json.dumps({"fields": fields}),
                visibility=visibility,
            )
        except _platform.DatasetExistsError as e:
            raise DatasetError(str(e)) from e
        inner.set_meta(FIELDS_META_KEY, json.dumps(fields))
        lease = getattr(inner, "dreamlake_lease", {}) or {}
        row = {"name": lease.get("name"), "namespace": lease.get("namespace"),
               "schemaType": st, "visibility": visibility}
        return cls(inner, namespace=lease.get("namespace"),
                   name=lease.get("name"), row=row)

    @classmethod
    def open(cls, name: str) -> "Dataset":
        """Open by name. Called on this base class, the catalog's schemaType
        picks the class: a registered preset type returns that preset
        (strict, typed); anything else returns a generic handle. Unknown
        never refuses — data written by newer tools stays readable."""
        from dreamlake import _platform

        try:
            row = _platform.get_dataset(name)
        except _platform.DatasetNotFoundError as e:
            raise DatasetError(str(e)) from e
        if cls is Dataset:
            target = _REGISTRY.get(row.get("schemaType"))
            if target is not None:
                return target.open(name)
        return cls._open_with_row(row)

    @classmethod
    def _open_with_row(cls, row: Dict[str, Any]) -> "Dataset":
        from dreamlake import _platform

        inner = _platform.open_dataset(f"{row['namespace']}/{row['name']}", row=row)
        return cls(inner, namespace=row["namespace"], name=row["name"], row=row)

    @classmethod
    def ensure(cls, name: str, *, schema: Optional[Schema] = None,
               schema_type: Optional[str] = None,
               visibility: str = "private") -> "Dataset":
        """Open-or-create — the shape every re-runnable upload script wants.
        Missing → created with these arguments. Existing → opened, then
        VERIFIED: an explicitly expected schema_type errors on mismatch, and
        each field of ``schema=`` must already be declared (ensure never
        mutates an existing schema — that stays explicit, via add_track).
        On a preset subclass, ``schema=``/``schema_type=`` are rejected —
        the preset owns its schema."""
        from dreamlake import _platform

        if cls.SCHEMA_TYPE is not None and (
            schema is not None or schema_type not in (None, cls.SCHEMA_TYPE)
        ):
            raise DatasetError(
                f"{cls.__name__}.ensure does not take schema=/schema_type= — "
                f"the preset owns its schema"
            )
        try:
            row = _platform.get_dataset(name)
        except _platform.DatasetNotFoundError:
            if cls.SCHEMA_TYPE is not None:
                return cls.create(name, visibility=visibility)
            return cls.create(name, schema=schema, schema_type=schema_type,
                              visibility=visibility)
        # Expected type: explicit on the base; implicit on a preset subclass.
        # A bare Dataset.ensure(name) expects nothing — like open, it takes
        # whatever schemaType is there (dispatched).
        want = schema_type if cls is Dataset else cls.SCHEMA_TYPE
        have = row.get("schemaType")
        if want and have and want != have:
            raise DatasetError(
                f"dataset '{row['name']}' holds schemaType '{have}', but ensure "
                f"was told to expect '{want}' — same name, different dataset. "
                f"Pick another name or open it explicitly."
            )
        ds = cls.open(name)
        if schema is not None:
            declared = {f["name"]: f for f in ds._fields}
            for f in schema.to_fields():
                got = declared.get(f["name"])
                if got is None:
                    raise DatasetError(
                        f"dataset '{row['name']}' has no track '{f['name']}' from "
                        f"the passed schema — ensure verifies, it never widens an "
                        f"existing schema. Declare it with ds.add_track(...)"
                    )
                if got.get("type") != f.get("type"):
                    raise DatasetError(
                        f"track '{f['name']}' is declared as {got.get('type')}, "
                        f"but the passed schema says {f.get('type')} — a track's "
                        f"kind cannot change"
                    )
        return ds

    @classmethod
    def list(cls, namespace: Optional[str] = None,
             schema_type: Optional[str] = None) -> List[DatasetInfo]:
        """The catalog of a namespace (the login's own unless ``namespace=``),
        optionally filtered by schemaType."""
        from dreamlake import _platform

        st = schema_type or (cls.SCHEMA_TYPE if cls is not Dataset else None)
        ns = _platform.resolve_namespace(
            namespace.removeprefix("@") if namespace else None
        )
        rows = _platform.list_datasets(st, namespace=ns)
        return [
            DatasetInfo(
                name=r.get("name", ""), namespace=ns,
                schema_type=r.get("schemaType", ""),
                visibility=r.get("visibility", "private"),
            )
            for r in rows
        ]

    @classmethod
    def delete(cls, name: str, *, purge: bool = False) -> None:
        """Remove ``name`` from the catalog — a classmethod on purpose: no
        open, no credential brokering, and a safe distance from the row-level
        ``ds.db.delete(anchors)`` tombstone. ``purge=True`` also deletes the
        backing storage."""
        from dreamlake import _platform

        try:
            _platform.delete_dataset(name, purge=purge)
        except _platform.DatasetNotFoundError as e:
            raise DatasetError(str(e)) from e

    # ---- identity / metadata --------------------------------------------

    @property
    def schema_type(self) -> Optional[str]:
        return self._row.get("schemaType") or self.SCHEMA_TYPE

    @property
    def visibility(self) -> str:
        return self._row.get("visibility", "private")

    def set_visibility(self, visibility: str) -> None:
        """``"private"`` or ``"public"`` — public datasets get anonymous
        presigned reads on the platform."""
        from dreamlake import _platform

        if visibility not in ("private", "public"):
            raise DatasetError(f'visibility must be "private" or "public", got {visibility!r}')
        _platform.patch_dataset(self._qualified(), visibility=visibility)
        self._row["visibility"] = visibility

    @property
    def db(self):
        """The live ``dreamdb.Dataset`` under this handle — the escape hatch
        for everything the SDK does not wrap (branching, tombstones, vector
        queries). Two warnings: fields added here are invisible to
        ``ds.tracks()`` (the fields mirror cannot see them), and the meta
        keys ``dreamdb.schema_type`` / ``dreamdb.dataset.*`` are the SDK's —
        overwriting them breaks the handle."""
        return self._ds

    def _qualified(self) -> str:
        if not self.namespace or not self.name:
            raise DatasetError("this handle is not bound to a platform dataset")
        return f"{self.namespace}/{self.name}"

    def __repr__(self) -> str:
        ident = self._qualified() if (self.namespace and self.name) else self.name
        return f"{type(self).__name__}({ident!r}, schema_type={self.schema_type!r})"

    # ---- tracks (the schema, in its live form) ---------------------------

    def _load_fields_mirror(self) -> List[Dict[str, Any]]:
        try:
            raw = (self._ds.meta() or {}).get(FIELDS_META_KEY)
            fields = json.loads(raw) if raw else []
            return fields if isinstance(fields, list) else []
        except Exception:
            return []

    def _save_fields_mirror(self) -> None:
        self._ds.set_meta(FIELDS_META_KEY, json.dumps(self._fields))

    def _track_from_spec(self, spec: Dict[str, Any]) -> Track:
        return Track(self, spec["name"], spec["type"],
                     mime=spec.get("mime"), dim=spec.get("dim"))

    def tracks(self) -> List[Track]:
        """Every declared track, as handles. This IS the dataset's schema in
        its live form — each Track carries name/kind/mime/dim."""
        return [self._track_from_spec(f) for f in self._fields]

    def track(self, name: str) -> Track:
        """The handle for one declared track; eager — an unknown name errors
        here, not on first write."""
        for f in self._fields:
            if f["name"] == name:
                return self._track_from_spec(f)
        raise DatasetError(
            f"no track '{name}' in this dataset — declare it first with "
            f"ds.add_track({name!r}, kind=...)"
        )

    def add_track(self, name: str, kind: str, *, mime: Optional[str] = None) -> Track:
        """Declare a track (schema evolution — by addition only). ``kind``
        is the dreamdb vocabulary verbatim: ``"video"``/``"image"`` (with
        ``mime=``; JSON documents are ``kind="image", mime="json"``) or a
        scalar. Idempotent for a matching declaration; a kind change errors.
        Embeddings cannot be added post-create (the LSH index is part of the
        schema) — declare them in ``Schema`` at create time."""
        try:
            validate_field_name(name)
        except SchemaError as e:
            raise DatasetError(str(e)) from e
        if kind == "embedding":
            raise DatasetError(
                "embedding tracks can only be declared when a dataset is created "
                "(the LSH index is part of the schema — a DreamDB constraint). "
                "Declare it in Schema() and pass schema= to Dataset.create."
            )
        if kind == "audio":
            raise DatasetError(
                "audio is not supported yet — dreamdb's append_many cannot "
                "ingest it (see Schema.add_audio)"
            )
        if kind not in EVOLVABLE_KINDS:
            raise DatasetError(f"unknown track kind '{kind}' — one of {list(EVOLVABLE_KINDS)}")

        resolved_mime = mime or ("h264" if kind == "video" else "jpeg") \
            if kind in ("image", "video") else None
        for f in self._fields:
            if f["name"] != name:
                continue
            if f["type"] != kind:
                raise DatasetError(
                    f"track '{name}' is already declared as kind '{f['type']}' — "
                    f"a track's kind cannot change"
                )
            if mime is not None and f.get("mime") != mime:
                raise DatasetError(
                    f"track '{name}' is already declared with mime "
                    f"'{f.get('mime')}', not '{mime}' — mime cannot change"
                )
            return self._track_from_spec(f)

        adder = getattr(self._ds, f"add_{kind}")
        try:
            if kind in ("image", "video"):
                adder(name, mime=resolved_mime, required=False)
            else:
                adder(name, required=False)
        except Exception as e:
            if "already exists" not in str(e):
                raise
        spec: Dict[str, Any] = {"name": name, "type": kind}
        if resolved_mime:
            spec["mime"] = resolved_mime
        self._fields.append(spec)
        self._save_fields_mirror()
        return self._track_from_spec(spec)

    # ---- row-wise write / read ------------------------------------------

    def append_rows(self, rows: Iterable[Dict[str, Any]]) -> Dict[str, int]:
        """Append a batch of rows in one commit. Each row is a dict with an
        ``"anchor"`` key (int ns or tz-aware datetime) plus track values;
        sparse rows are the norm — omit a field rather than passing None.
        The same shape :meth:`rows` returns, so
        ``ds2.append_rows(ds1.rows(...))`` holds by contract."""
        tracks_by_name = {f["name"]: self._track_from_spec(f) for f in self._fields}
        samples: List[Dict[str, Any]] = []
        seen: set = set()
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                raise DatasetError(f"rows[{i}] must be a dict, got {type(row).__name__}")
            if "anchor" not in row:
                raise DatasetError(f"rows[{i}] has no 'anchor' key — every row needs one")
            a = to_anchor_ns(row["anchor"], what=f"rows[{i}] anchor")
            sample: Dict[str, Any] = {"_anchor": a}
            for key, value in row.items():
                if key == "anchor":
                    continue
                tr = tracks_by_name.get(key)
                if tr is None:
                    raise DatasetError(
                        f"rows[{i}] names unknown track '{key}' — declare it first "
                        f"with ds.add_track({key!r}, kind=...)"
                    )
                if (a, key) in seen:
                    raise DatasetError(
                        f"rows[{i}]: ({key}, anchor {a}) appears twice in this "
                        f"batch — writes are write-once"
                    )
                seen.add((a, key))
                sample[key] = tr._encode_value(value)
            if len(sample) == 1:
                raise DatasetError(f"rows[{i}] carries no track values — nothing to write")
            samples.append(sample)
        if not samples:
            return {"rows": 0}
        samples.sort(key=lambda s: s["_anchor"])
        self._append_samples(samples)
        return {"rows": len(samples)}

    def rows(self, *, start: Any = None, end: Any = None,
             tracks: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
        """Rows in ``[start, end)`` as ``{"anchor": ns, track: value, ...}``
        dicts, sorted by anchor — sparse (an absent field is an absent key,
        never None). Video tracks are excluded by default (their per-fragment
        bytes have no row semantics); naming one in ``tracks=`` errors and
        points at ``t.read``/playback."""
        if tracks is not None:
            handles = [self.track(n) for n in tracks]
            for t in handles:
                if t.kind == "video":
                    raise DatasetError(
                        f"track '{t.name}' is a video track — it has no row values. "
                        f"Playback goes through the platform; raw bytes via ds.db."
                    )
        else:
            handles = [self._track_from_spec(f) for f in self._fields
                       if f["type"] != "video"]
        if not handles:
            return []
        s = to_anchor_ns(start, what="start") if start is not None else 0
        e = to_anchor_ns(end, what="end") if end is not None else None
        merged: Dict[int, Dict[str, Any]] = {}
        for t in handles:
            for a, v in self._read_field_window(t.name, s, e):
                if v is None:
                    continue
                merged.setdefault(a, {})[t.name] = t._decode_value(v)
        return [{"anchor": a, **merged[a]} for a in sorted(merged)]

    # ---- introspection / recovery ---------------------------------------

    def anchors(self, *, start: Any = None, end: Any = None) -> List[int]:
        """Every item anchor in ``[start, end)``, ascending — the cheap
        "did my upload land / what span is taken" primitive: ``len`` is the
        count, first/last are the span."""
        self._check_lease()
        out = [int(a) for a in self._ds.list_anchors()]
        if start is not None:
            s = to_anchor_ns(start, what="start")
            out = [a for a in out if a >= s]
        if end is not None:
            e = to_anchor_ns(end, what="end")
            out = [a for a in out if a < e]
        return out

    def reload(self) -> "Dataset":
        """Refresh this handle in place: re-read the catalog row and the
        fields mirror, and re-broker the credential lease (HTTP token auth —
        works after the S3 lease expired). Track handles reference this
        dataset, so they all survive. The fix for the two documented
        stalenesses: another process's add_track, and the 12h lease."""
        from dreamlake import _platform

        qualified = self._qualified()
        try:
            row = _platform.get_dataset(qualified)
        except _platform.DatasetNotFoundError as e:
            raise DatasetError(str(e)) from e
        self._ds = _platform.open_dataset(qualified, row=row)
        self._row = row
        self._fields = self._load_fields_mirror()
        return self

    # ---- engine plumbing (shared with Track) ----------------------------

    def _check_lease(self) -> None:
        lease = getattr(self._ds, "dreamlake_lease", None)
        exp = (lease or {}).get("expiration")
        if not exp:
            return
        try:
            when = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
        except ValueError:
            return
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) >= when:
            raise DatasetError(
                "this dataset's credential lease has expired — call ds.reload() "
                "to re-broker and continue"
            )

    def _append_samples(self, samples: List[Dict[str, Any]]) -> None:
        self._check_lease()
        try:
            self._ds.append_many(samples)
        except Exception as e:
            msg = str(e)
            if "not in schema" in msg or "no FieldTrack" in msg:
                raise DatasetError(
                    f"a track in this batch is not in the dataset's schema — "
                    f"declare it first with ds.add_track(...). Engine said: {msg}"
                ) from e
            raise

    def _read_field_window(self, field: str, start_ns: int,
                           end_ns: Optional[int]) -> List[Any]:
        """Raw ``(anchor, value)`` pairs of one field in a window. A declared
        track with no data reads as ``[]`` — the engine's "no FieldTrack" for
        an empty track is normal here, not an error."""
        self._check_lease()
        kwargs: Dict[str, Any] = {"fields": [field], "start_ns": int(start_ns)}
        if end_ns is not None:
            kwargs["end_ns"] = int(end_ns)
        try:
            batches = list(self._ds.iter_all_batches(**kwargs))
        except Exception as e:
            msg = str(e)
            if "no FieldTrack" in msg or "not in schema" in msg:
                return []
            raise
        out = []
        for batch in batches:
            anchors = batch.get("_time_anchors") or []
            values = batch.get(field) or []
            for a, v in zip(anchors, values):
                out.append((int(a), v))
        return out
