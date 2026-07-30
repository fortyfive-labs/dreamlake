"""DreamLake platform plumbing for dataset presets — INTERNAL.

The public ``dreamlake.db`` layer is deliberately platform-free (it is just
dreamdb against an explicit backend). Presets like ``dreamlake.dataset``
are what bind data to the DreamLake platform, and this module is the
machinery they share: resolve the login, register/look up the dataset in
the namespace catalog, broker short-lived prefix-scoped S3 credentials, and
hand back a plain ``dreamdb.Dataset`` against the brokered backend — the
flow modeled on the proven artifact-push path (``cli/commands/artifact.py``).

Nothing here is public API. User code reaches the platform through a
preset (``Dataset.create("name")``); custom-schema data lives on the
user's own backend via ``dreamlake.db``.
"""

from __future__ import annotations

import os

from dreamlake import _session
from dreamlake import db as _db

DEFAULT_DURATION_SECONDS = 43200  # 12h credential lease
_DEFAULT_REGION = "us-east-1"


class PlatformError(RuntimeError):
    """A DreamLake platform call failed for a reason the caller can act on."""


class DatasetExistsError(PlatformError):
    """create on a name that is already registered — open it instead."""


class DatasetNotFoundError(PlatformError):
    """open/delete on a name the server does not know."""


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


def _platform_context() -> tuple[str, str, str]:
    """Resolve (token, namespace, remote) for a platform call."""
    token = _session.get_token()
    remote = _session.remote_url()
    namespace = _session.get_namespace(token=token, remote=remote)
    return token, namespace, remote


# ── Catalog + brokered dataset handles ──────────────────────────────────────

def create_dataset(name: str, schema, *, schema_type: str,
                   schema_json: str | None = None,
                   visibility: str | None = None,
                   duration_seconds: int = DEFAULT_DURATION_SECONDS):
    """Register ``name`` in the caller's namespace catalog, broker a
    credential lease, and create the dreamdb space on the brokered backend.
    Returns a live ``dreamdb.Dataset`` with ``dreamlake_lease`` attached."""
    dreamdb = _db._dreamdb()
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
            f"dataset '{name}' already exists in namespace '{namespace}' — "
            f"open it instead of creating, or delete it first."
        )
    if r.status_code >= 400:
        raise PlatformError(
            f"could not create dataset '{name}' ({r.status_code}).{_server_said(r)}"
        )

    broker = _broker_credentials(remote, namespace, name, token, duration_seconds)
    ds = dreamdb.Dataset.create(broker.get("refName", _db.REF_NAME), schema,
                                backend=broker["backendUrl"])
    # Stamp the space itself so direct-backend readers can self-describe it.
    ds.set_meta("dreamdb.schema_type", schema_type)
    return _attach_lease(ds, broker, namespace, name)


def open_dataset(name: str, *, schema=None,
                 duration_seconds: int = DEFAULT_DURATION_SECONDS):
    """Look ``name`` up in the caller's namespace catalog, broker a
    credential lease, and open the dreamdb space on the brokered backend."""
    dreamdb = _db._dreamdb()
    token, namespace, remote = _platform_context()

    r = _request("GET", f"{remote}/namespaces/{namespace}/datasets/{name}", token)
    if r.status_code == 404:
        raise DatasetNotFoundError(
            f"dataset '{name}' not found in namespace '{namespace}' — "
            f"create it first."
        )
    if r.status_code >= 400:
        raise PlatformError(
            f"could not look up dataset '{name}' ({r.status_code}).{_server_said(r)}"
        )

    broker = _broker_credentials(remote, namespace, name, token, duration_seconds)
    ds = dreamdb.Dataset.open(broker.get("refName", _db.REF_NAME), schema,
                              backend=broker["backendUrl"])
    return _attach_lease(ds, broker, namespace, name)


def list_datasets(schema_type: str | None = None) -> list[dict]:
    """The namespace's dataset catalog, optionally filtered by schemaType."""
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


def delete_dataset(name: str, purge: bool = False) -> None:
    """Delete a dataset from the catalog. ``purge=True`` also deletes the
    backing storage; otherwise only the catalog entry is removed."""
    token, namespace, remote = _platform_context()
    url = f"{remote}/namespaces/{namespace}/datasets/{name}"
    if purge:
        url += "/purge"
    r = _request("DELETE", url, token)
    if r.status_code == 404:
        raise DatasetNotFoundError(
            f"dataset '{name}' not found in namespace '{namespace}'."
        )
    if r.status_code >= 400:
        raise PlatformError(
            f"could not delete dataset '{name}' ({r.status_code}).{_server_said(r)}"
        )
