"""``dreamlake.db`` — DreamDB, re-exported, plus DreamLake platform plumbing.

``dreamlake.db`` IS dreamdb: every dreamdb name is forwarded lazily
(``db.Schema``, ``db.Dataset``, ``db.train_and_publish_ivf_centroids``, ...),
so anything you can do with the bare ``dreamdb`` package you can do here. On
top sit five platform functions that broker short-lived, prefix-scoped S3
credentials from the DreamLake server — modeled exactly on the proven
artifact-push flow (``cli/commands/artifact.py``)::

    import dreamlake.db as db

    schema = db.Schema().jpg("image").label("caption")

    ds = db.create("my-set", schema)        # register + broker creds + create
    ds = db.open("my-set")                  # lookup + broker creds + open
    ds = db.open_backend("file:///tmp/x")   # direct backend, no platform calls
    db.list()                               # the namespace's dataset catalog
    db.delete("my-set", purge=True)

Platform flow (``backend=None``): resolve token/namespace/remote via
``dreamlake._session``, register or look up the dataset in the server
catalog, ``POST .../upload-credentials`` for a lease, export it as ``AWS_*``
env vars, then hand off to ``dreamdb.Dataset`` against the brokered
``backendUrl``. Pass ``backend="file:///..."`` (or ``s3://``, ``https://``)
to skip the platform entirely and hit the backend directly — the dreamdb ref
name is always ``"main"``, matching the TypeScript CLI.

The returned handle is a plain ``dreamdb.Dataset``; platform opens/creates
carry the lease details in ``ds.dreamlake_lease``.
"""

from __future__ import annotations

import os

from dreamlake import _session

# The dreamdb ref name used for every dataset — a fixed contract shared with
# the TypeScript CLI and the web viewer.
REF_NAME = "main"

DEFAULT_DURATION_SECONDS = 43200  # 12h credential lease
_DEFAULT_REGION = "us-east-1"


class PlatformError(RuntimeError):
    """A DreamLake platform call failed for a reason the caller can act on."""


class DatasetExistsError(PlatformError):
    """``db.create`` on a name that is already registered — use ``db.open``."""


class DatasetNotFoundError(PlatformError):
    """``db.open``/``db.delete`` on a name the server does not know."""


# ── dreamdb forwarding ───────────────────────────────────────────────────────

def _dreamdb():
    """Import seam for the dreamdb package.

    Every dreamdb access in this module goes through here so tests can
    monkeypatch a fake (``monkeypatch.setattr(db, "_dreamdb", lambda: fake)``)
    without touching the real native package.
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


# ── HTTP plumbing ────────────────────────────────────────────────────────────

def _request(method: str, url: str, token: str, *, json_body: dict | None = None):
    """One platform HTTP call. Network failure → PlatformError; HTTP status
    handling is the caller's (it knows which codes are actionable)."""
    import httpx

    try:
        return httpx.request(
            method,
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=json_body,
            timeout=30,
        )
    except Exception as e:
        raise PlatformError(
            f"could not reach the DreamLake server ({method} {url}): {e}. "
            "Check DREAMLAKE_REMOTE / your network."
        ) from e


def _server_said(r) -> str:
    text = (getattr(r, "text", "") or "").strip()
    return f" Server said: {text[:200]}" if text else ""


def _broker_credentials(remote: str, namespace: str, name: str, token: str,
                        duration_seconds: int) -> dict:
    """POST .../upload-credentials, export the lease as AWS_* env vars, and
    return the broker response (credentials, region, bucket, prefix,
    backendUrl, refName)."""
    r = _request(
        "POST",
        f"{remote}/namespaces/{namespace}/datasets/{name}/upload-credentials",
        token,
        json_body={"durationSeconds": duration_seconds},
    )
    if r.status_code >= 400:
        raise PlatformError(
            f"could not get access credentials for dataset '{name}' "
            f"({r.status_code}).{_server_said(r)}"
        )
    broker = r.json()

    # Parse defensively — a shape change shouldn't crash with a raw KeyError.
    try:
        creds = broker["credentials"]
        os.environ["AWS_ACCESS_KEY_ID"] = creds["accessKeyId"]
        os.environ["AWS_SECRET_ACCESS_KEY"] = creds["secretAccessKey"]
        # Static-endpoint (MinIO) broker path returns an empty session token —
        # setting an empty AWS_SESSION_TOKEN breaks SigV4, so only set non-empty.
        session = creds.get("sessionToken") or ""
        if session:
            os.environ["AWS_SESSION_TOKEN"] = session
        else:
            os.environ.pop("AWS_SESSION_TOKEN", None)
        os.environ["AWS_REGION"] = broker.get("region", _DEFAULT_REGION)
        broker["backendUrl"]  # presence check — required below
    except (KeyError, TypeError) as e:
        raise PlatformError(
            f"unexpected upload-credentials response (missing {e})."
        ) from e
    return broker


def _attach_lease(ds, broker: dict, namespace: str, name: str):
    """Stamp the lease onto the returned handle. dreamdb.Dataset is a plain
    Python wrapper class, so the attribute sticks."""
    ds.dreamlake_lease = {
        "backend_url": broker["backendUrl"],
        "expiration": (broker.get("credentials") or {}).get("expiration"),
        "namespace": namespace,
        "name": name,
        "prefix": broker.get("prefix"),
    }
    return ds


def _require_name(name: str | None, verb: str) -> None:
    if not name:
        raise ValueError(
            f"db.{verb}: name is required when no backend= is given. "
            f"Pass a dataset name for the platform flow, or backend='file:///...' "
            f"to hit a backend directly."
        )


def _platform_context() -> tuple[str, str, str]:
    """Resolve (token, namespace, remote) for a platform call."""
    token = _session.get_token()
    remote = _session.remote_url()
    namespace = _session.get_namespace(token=token, remote=remote)
    return token, namespace, remote


# ── Platform functions ───────────────────────────────────────────────────────

def create(name: str | None = None, schema=None, *, backend: str | None = None,
           schema_type: str = "custom", schema_json: str | None = None,
           visibility: str | None = None,
           duration_seconds: int = DEFAULT_DURATION_SECONDS):
    """Create a dataset and return a live ``dreamdb.Dataset`` handle.

    Platform flow (default): registers ``name`` in your namespace's catalog,
    brokers a credential lease, and creates the dreamdb space on the brokered
    backend. With ``backend=`` given, skips the platform entirely and creates
    directly on that backend (ref name is always "main").
    """
    dreamdb = _dreamdb()

    if backend is not None:
        ds = dreamdb.Dataset.create(REF_NAME, schema, backend=backend)
        ds.set_meta("dreamdb.schema_type", schema_type)
        if name:
            ds.set_meta("dreamdb.title", name)
        return ds

    _require_name(name, "create")
    token, namespace, remote = _platform_context()

    body: dict = {"name": name, "schemaType": schema_type}
    if schema_json is not None:
        body["schemaJson"] = schema_json
    if visibility is not None:
        body["visibility"] = visibility

    r = _request("POST", f"{remote}/namespaces/{namespace}/datasets", token,
                 json_body=body)
    if r.status_code == 409:
        raise DatasetExistsError(
            f"dataset '{name}' already exists in namespace '{namespace}'. "
            f"Use db.open({name!r}) to write to it, or db.delete({name!r}) first."
        )
    if r.status_code >= 400:
        raise PlatformError(
            f"could not create dataset '{name}' ({r.status_code}).{_server_said(r)}"
        )

    broker = _broker_credentials(remote, namespace, name, token, duration_seconds)
    ds = dreamdb.Dataset.create(broker.get("refName", REF_NAME), schema,
                                backend=broker["backendUrl"])
    # Stamp the space itself so direct-backend readers can self-describe it.
    ds.set_meta("dreamdb.schema_type", schema_type)
    return _attach_lease(ds, broker, namespace, name)


def open(name: str | None = None, *, backend: str | None = None, schema=None,
         duration_seconds: int = DEFAULT_DURATION_SECONDS):
    """Open an existing dataset and return a live ``dreamdb.Dataset`` handle.

    Platform flow (default): looks ``name`` up in your namespace's catalog,
    brokers a credential lease, and opens the dreamdb space on the brokered
    backend. With ``backend=`` given, opens the backend directly.
    """
    dreamdb = _dreamdb()

    if backend is not None:
        return dreamdb.Dataset.open(REF_NAME, schema, backend=backend)

    _require_name(name, "open")
    token, namespace, remote = _platform_context()

    r = _request("GET", f"{remote}/namespaces/{namespace}/datasets/{name}", token)
    if r.status_code == 404:
        raise DatasetNotFoundError(
            f"dataset '{name}' not found in namespace '{namespace}'. "
            f"Use db.create({name!r}, schema) to create it, or db.list() to "
            f"see what exists."
        )
    if r.status_code >= 400:
        raise PlatformError(
            f"could not look up dataset '{name}' ({r.status_code}).{_server_said(r)}"
        )

    broker = _broker_credentials(remote, namespace, name, token, duration_seconds)
    ds = dreamdb.Dataset.open(broker.get("refName", REF_NAME), schema,
                              backend=broker["backendUrl"])
    return _attach_lease(ds, broker, namespace, name)


def open_backend(backend: str, schema=None):
    """Open a dataset directly on a backend URI (no platform calls).

    Thin alias of ``db.open(backend=...)`` — ``file:///path``,
    ``s3://bucket/prefix``, or ``https://...`` all work.
    """
    return open(backend=backend, schema=schema)


def list(schema_type: str | None = None) -> list[dict]:
    """List the datasets registered in your namespace's catalog."""
    token, namespace, remote = _platform_context()
    url = f"{remote}/namespaces/{namespace}/datasets"
    if schema_type:
        url += f"?schemaType={schema_type}"
    r = _request("GET", url, token)
    if r.status_code >= 400:
        raise PlatformError(
            f"could not list datasets ({r.status_code}).{_server_said(r)}"
        )
    return r.json().get("datasets", [])


def delete(name: str, purge: bool = False) -> None:
    """Delete a dataset from the catalog. ``purge=True`` also deletes the
    backing storage; otherwise only the catalog entry is removed."""
    _require_name(name, "delete")
    token, namespace, remote = _platform_context()
    url = f"{remote}/namespaces/{namespace}/datasets/{name}"
    if purge:
        url += "/purge"
    r = _request("DELETE", url, token)
    if r.status_code == 404:
        raise DatasetNotFoundError(
            f"dataset '{name}' not found in namespace '{namespace}'. "
            f"Use db.list() to see what exists."
        )
    if r.status_code >= 400:
        raise PlatformError(
            f"could not delete dataset '{name}' ({r.status_code}).{_server_said(r)}"
        )
