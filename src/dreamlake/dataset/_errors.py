"""The dataset layer's exception family.

One module so the generic layer (``_base``/``_track``/``_fields``) and the
video-annotation preset (``_core``/``_episode``) can share the hierarchy
without importing each other.
"""

from __future__ import annotations


class DatasetError(RuntimeError):
    """A dataset operation that failed for a reason the caller can act on."""


class SchemaError(DatasetError):
    """A schema declaration the engine would reject (or silently mangle) —
    raised at declaration time, before anything touches the platform."""
