"""``dreamlake.db`` — DreamDB, re-exported. Platform-free by design.

``dreamlake.db`` IS dreamdb: every dreamdb name is forwarded lazily
(``db.Schema``, ``db.Dataset``, ``db.train_and_publish_ivf_centroids``, ...),
so anything you can do with the bare ``dreamdb`` package you can do here. On
top sit exactly two conveniences — ``create``/``open`` against an explicit
backend URI with the fixed ``"main"`` ref and schemaType stamping::

    import dreamlake.db as db

    schema = db.Schema()
    schema.add_image("frame", mime="jpeg", required=False)

    ds = db.create(schema, backend="file:///data/x", schema_type="my-lab.frames/v1")
    ds = db.open(backend="file:///data/x")

This layer never talks to the DreamLake platform: no login, no catalog, no
credential brokering. ``backend`` is required — ``file:///abs/path`` for a
directory, or an ``https://…`` object-store URL with your own credentials in
the environment. The platform experience (managed bucket, catalog row, web
visualization) belongs to the presets built on top — ``dreamlake.dataset``
— which broker credentials internally and then use plain dreamdb handles
just like this module hands out.
"""

from __future__ import annotations

# The dreamdb ref name used for every dataset — a fixed contract shared with
# the TypeScript CLI and the web viewer.
REF_NAME = "main"


# ── dreamdb forwarding ───────────────────────────────────────────────────────

def _dreamdb():
    """Import seam for the dreamdb package.

    Every dreamdb access in this module (and in ``dreamlake._platform``)
    goes through here so tests can monkeypatch a fake
    (``monkeypatch.setattr(db, "_dreamdb", lambda: fake)``) without touching
    the real native package.
    """
    try:
        import dreamdb
    except ImportError as e:
        raise ImportError(
            "dreamlake.db needs the 'dreamdb' package for dataset reads/writes. "
            "Install it with: pip install dreamdb"
        ) from e
    return dreamdb


def __getattr__(name: str):
    # Forward everything we don't define to the dreamdb package, so
    # `dreamlake.db` IS dreamdb. Dunder probes (e.g. `__path__` from import
    # machinery, `__all__` from `from db import *`) must stay AttributeError
    # even when dreamdb is not installed.
    if name.startswith("__") and name.endswith("__"):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(_dreamdb(), name)


# ── Engine-level create/open ─────────────────────────────────────────────────

def create(schema, *, backend: str, schema_type: str = "custom",
           title: str | None = None):
    """Create a dataset on ``backend`` and return a live ``dreamdb.Dataset``.

    ``schema_type`` is stamped into the space meta so readers can dispatch
    on it without opening the tracks (the same key the web viewer uses);
    ``title`` stamps a human-readable name. The ref is always ``"main"``.
    """
    dreamdb = _dreamdb()
    ds = dreamdb.Dataset.create(REF_NAME, schema, backend=backend)
    ds.set_meta("dreamdb.schema_type", schema_type)
    if title:
        ds.set_meta("dreamdb.title", title)
    return ds


def open(backend: str, *, schema=None):
    """Open an existing dataset on ``backend``.

    ``schema=None`` recovers the schema persisted in the dataset itself; an
    explicit schema must match the persisted one.
    """
    dreamdb = _dreamdb()
    return dreamdb.Dataset.open(REF_NAME, schema, backend=backend)
