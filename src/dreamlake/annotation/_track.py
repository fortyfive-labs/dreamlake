"""``Track`` — the handle for one declared track of an annotation.

Column-wise reads and writes live here; row-wise (cross-track) reads and
writes live on the Annotation. The write vocabulary is one verb, ``append``
(storage is append-only), with the shape in the suffix: ``append(anchor,
value)`` puts one data point at a specific anchor, ``append_range(items)``
appends a stretch of timeline. Reads pair up: ``get(anchor)`` ↔ ``append``,
``read(start, end)`` ↔ ``append_range``.

Write semantics are write-once: the engine resolves same-anchor duplicates
by CONTENT order, not write order, so re-writing an (anchor, track) pair is
undefined — the SDK rejects duplicates it can see (within one call) and the
contract covers the rest.

Values: dreamdb's native representations (bytes / int ns / native scalars /
float32 vectors) pass through untouched. On top, a few common Python shapes
are accepted as one-way input conveniences (paths, tz-aware datetimes,
dicts on ``mime="json"`` tracks, ``.npy`` files); the SDK's real write-side
job is validation — moving errors that would otherwise surface deep in the
Rust engine (after bytes already landed on S3) up to the call site.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ._errors import AnnotationError
from ._fields import dumps_compact, to_anchor_ns


class Track:
    """One declared track. Cheap, eagerly validated — obtained from
    ``ds.track(name)`` / ``ds.add_track(...)`` / ``ds.tracks()``, never
    constructed directly."""

    def __init__(self, annotation, name: str, kind: str, *,
                 mime: Optional[str] = None, dim: Optional[int] = None,
                 role: Optional[str] = None, camera: Optional[str] = None):
        self._d = annotation
        self.name = name
        self.kind = kind
        self.mime = mime
        self.dim = dim
        # Preset-only extras (None on custom annotations): which structural role
        # a video-annotation track plays, and which camera owns it.
        self.role = role
        self.camera = camera

    @property
    def preset(self) -> bool:
        """True when a preset owns this track (it has a structural role);
        False for user tracks and for every track of a custom annotation."""
        return self.role not in (None, "user", "unknown")

    def __repr__(self) -> str:
        extra = f", mime={self.mime!r}" if self.mime else (f", dim={self.dim}" if self.dim else "")
        return f"Track({self.name!r}, kind={self.kind!r}{extra})"

    # ---- write side ------------------------------------------------------

    def append(self, anchor: Any, value: Any) -> Dict[str, int]:
        """One data point at a specific anchor. Each call is one commit —
        for a stretch of timeline use :meth:`append_range` (one commit for
        the whole batch)."""
        return self.append_range([(anchor, value)])

    def append_range(self, items: Iterable[Tuple[Any, Any]]) -> Dict[str, int]:
        """Append a stretch of timeline: ``(anchor, value)`` pairs, sorted
        by anchor before writing. A duplicate anchor within the batch is an
        error (write-once — the engine cannot overwrite). Evenly-sampled
        signals pair with ``sequence_anchors``:
        ``t.append_range(zip(sequence_anchors(len(vs), start=t0), vs))``."""
        encoded: List[Tuple[int, Any]] = []
        for item in items:
            try:
                anchor, value = item
            except (TypeError, ValueError):
                raise AnnotationError(
                    f"track '{self.name}': append_range items are (anchor, value) "
                    f"pairs, got {item!r}"
                ) from None
            encoded.append((to_anchor_ns(anchor, what=f"track '{self.name}' anchor"),
                            self._encode_value(value)))
        if not encoded:
            return {"items": 0}
        encoded.sort(key=lambda av: av[0])
        for (a, _), (b, _) in zip(encoded, encoded[1:]):
            if a == b:
                raise AnnotationError(
                    f"track '{self.name}': anchor {a} appears twice in this batch — "
                    f"writes are write-once (the engine resolves same-anchor "
                    f"duplicates by content, not write order)"
                )
        self._d._append_samples([{"_anchor": a, self.name: v} for a, v in encoded])
        return {"items": len(encoded)}

    # ---- read side -------------------------------------------------------

    def get(self, anchor: Any) -> Any:
        """The value at exactly ``anchor``, or None (no value at that anchor,
        or the track was never written — both are normal, not errors)."""
        a = to_anchor_ns(anchor, what=f"track '{self.name}' anchor")
        rows = self._d._read_field_window(self.name, a, a + 1)
        for _, v in rows:
            if v is not None:
                return self._decode_value(v)
        return None

    def read(self, *, start: Any = None, end: Any = None) -> List[Tuple[int, Any]]:
        """All ``(anchor, value)`` in ``[start, end)``, sorted by anchor.
        Defaults to the whole track; a declared-but-never-written track reads
        as ``[]``. Pass a range on large annotations — the return is one list."""
        if self.kind == "video":
            raise AnnotationError(
                f"track '{self.name}' is a video track — there is no ranged video "
                f"read in v1. Playback goes through the platform; raw fragment "
                f"bytes are reachable via ds.db."
            )
        s = to_anchor_ns(start, what="start") if start is not None else 0
        e = to_anchor_ns(end, what="end") if end is not None else None
        out = [(a, self._decode_value(v))
               for a, v in self._d._read_field_window(self.name, s, e)
               if v is not None]
        out.sort(key=lambda av: av[0])
        return out

    # ---- video ingest ----------------------------------------------------

    def ingest(self, src: Any, *, anchor: Any, frag_seconds: float = 2.0,
               height: Optional[int] = None) -> Dict[str, Any]:
        """Ingest a video file at ``anchor`` — the only write path for video
        tracks. ``height=None`` (default) is a lossless remux: fast, but a
        CMAF track accepts exactly one codec configuration, so every clip on
        this track must be identically encoded. ``height=N`` re-encodes to a
        uniform profile (h264, 30 fps, N px tall) so mixed sources can share
        the track. Returns the ingest summary."""
        if self.kind != "video":
            raise AnnotationError(
                f"track '{self.name}' is kind '{self.kind}' — ingest is for video "
                f"tracks. Write this track with append/append_range."
            )
        if not (1.0 <= float(frag_seconds) <= 30.0):
            raise AnnotationError("frag_seconds must be between 1 and 30 seconds")
        src = str(src)
        a = to_anchor_ns(anchor, what=f"track '{self.name}' anchor")

        from ._ffmpeg import fragment_video, probe

        info = probe(src)
        dur_ns = round(info.duration_sec * 1e9)

        # Overlap pre-check: each clip occupies [anchor, anchor+duration).
        # The engine's behavior on overlapping spans is undefined, so refuse
        # up front. The window starts 30s early to catch a fragment that
        # BEGINS before `anchor` but runs into it (30s is the max fragment
        # length dreamdb permits).
        window_start = max(0, a - 30 * 1_000_000_000)
        existing = self._d._read_field_window(self.name, window_start, a + dur_ns)
        if any(v is not None for _, v in existing):
            raise AnnotationError(
                f"track '{self.name}': [{a}, {a + dur_ns}) overlaps footage already "
                f"on this track — each clip needs its own anchor span "
                f"(ds.anchors() shows what is taken)"
            )

        self._d._check_lease()
        if height is None:
            try:
                result = self._d._ds.ingest_video(
                    self.name, src, frag_duration=float(frag_seconds), anchor=a
                )
            except Exception as e:
                if "init segment" in str(e):
                    raise AnnotationError(
                        f"track '{self.name}': this clip's codec configuration "
                        f"differs from the one already on the track — a lossless "
                        f"track holds identically encoded clips only. Re-encode "
                        f"to a shared profile with ingest(..., height=N)."
                    ) from e
                raise
            summary = result.get("raw", result) if isinstance(result, dict) else result
            return {"mode": "remux", "anchor": a, "duration_s": info.duration_sec,
                    "ingest": summary}

        frag = fragment_video(
            src, anchor_ns=a, frag_seconds=float(frag_seconds),
            scale_height=int(height),
        )
        try:
            result = self._d._ds.ingest_cmaf(self.name, frag.init_path, frag.fragments)
            n = len(frag.fragments)
        finally:
            frag.cleanup()
        return {"mode": "reencode", "anchor": a, "duration_s": info.duration_sec,
                "fragments": n, "ingest": result}

    # ---- value codec (shared with the row-wise API on Annotation) -----------

    def _encode_value(self, value: Any) -> Any:
        """``value`` as the engine representation, or an actionable error.
        Native representations pass through untouched."""
        name, kind = self.name, self.kind
        if value is None:
            raise AnnotationError(
                f"track '{name}': None is not a value — omit the field instead "
                f"(sparse rows are how absence is expressed; append_many rejects None)"
            )
        if kind == "video":
            raise AnnotationError(
                f"track '{name}' is a video track — use ds.track({name!r}).ingest(...) "
                f"instead of append"
            )
        if kind == "image":
            if isinstance(value, (bytes, bytearray, memoryview)):
                return bytes(value)
            if self.mime == "json" and isinstance(value, (dict, list)):
                return dumps_compact(value).encode()
            if isinstance(value, (str, Path)):
                p = Path(value)
                if not p.is_file():
                    raise AnnotationError(
                        f"track '{name}': file '{value}' does not exist — image "
                        f"values are bytes, or a path to a readable file"
                    )
                return p.read_bytes()
            raise AnnotationError(
                f"track '{name}' (image/{self.mime}): value must be bytes or a file "
                f"path{' — or a dict/list (stored as JSON)' if self.mime == 'json' else ''}, "
                f"got {type(value).__name__}"
            )
        if kind == "embedding":
            return self._encode_vector(value)
        if kind == "scalar_timestamp":
            return to_anchor_ns(value, what=f"track '{name}' timestamp")
        if kind == "scalar_bool":
            if isinstance(value, bool):
                return value
        elif kind == "scalar_int":
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        elif kind == "scalar_float":
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
        elif kind in ("scalar_string", "scalar_categorical"):
            if isinstance(value, str):
                return value
        else:
            raise AnnotationError(f"track '{name}' has unknown kind '{kind}'")
        raise AnnotationError(
            f"track '{name}' is {kind} — got {type(value).__name__} {value!r}. "
            f"Strict on purpose: a lossy coercion here would corrupt silently."
        )

    def _encode_vector(self, value: Any) -> List[float]:
        name = self.name
        if isinstance(value, (str, Path)):
            if not str(value).endswith(".npy"):
                raise AnnotationError(
                    f"track '{name}': embedding paths must be .npy files, got '{value}'"
                )
            try:
                import numpy as np
            except ImportError as e:
                raise AnnotationError(
                    f"track '{name}': reading .npy embeddings needs numpy — "
                    f"pip install numpy, or pass the vector as a list of floats"
                ) from e
            try:
                value = np.load(str(value))
            except Exception as e:
                raise AnnotationError(f"track '{name}': could not read '{value}': {e}") from e
        if hasattr(value, "astype") and hasattr(value, "tolist"):  # np.ndarray, no hard dep
            value = value.astype("float32").reshape(-1).tolist()
        if not isinstance(value, (list, tuple)):
            raise AnnotationError(
                f"track '{name}': embedding values are a list of floats, an "
                f"ndarray, or a .npy path — got {type(value).__name__}"
            )
        try:
            vec = [float(x) for x in value]
        except (TypeError, ValueError) as e:
            raise AnnotationError(f"track '{name}': embedding has non-numeric entries") from e
        if self.dim is not None and len(vec) != self.dim:
            raise AnnotationError(
                f"track '{name}' is dim={self.dim}, got a {len(vec)}-vector"
            )
        return vec

    def _decode_value(self, value: Any) -> Any:
        """The stored representation, returned as-is — with ONE exception:
        ``mime="json"`` tracks decode back to the dict/list that went in
        (otherwise write-dict/read-bytes would break the round-trip
        contract)."""
        if isinstance(value, (bytes, bytearray, memoryview)):
            raw = bytes(value)
            if self.kind == "image" and self.mime == "json":
                try:
                    return json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    return raw
            return raw
        return value
