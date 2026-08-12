"""DreamLake platform plumbing for annotation presets — INTERNAL.

The public ``dreamlake.db`` layer is deliberately platform-free (it is just
dreamdb against an explicit backend). Presets like ``dreamlake.annotation``
are what bind data to the DreamLake platform, and this module is the
machinery they share: resolve the login, register/look up the annotation in
the namespace catalog, broker short-lived prefix-scoped S3 credentials, and
hand back a plain ``dreamdb.Dataset`` against the brokered backend — the
flow modeled on the proven artifact-push path (``cli/commands/artifact.py``).

Nothing here is public API. User code reaches the platform through a
preset (``Annotation.create("name")``); custom-schema data lives on the
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


class AnnotationExistsError(PlatformError):
    """create on a name that is already registered — open it instead."""


class AnnotationNotFoundError(PlatformError):
    """open/delete on a name the server does not know."""


# ── Qualified names ──────────────────────────────────────────────────────────

def split_qualified(name) -> "tuple[str | None, str]":
    """``(namespace | None, bare_name)`` from an annotation name.

    No ``/`` means the caller's own namespace (resolved from the login) —
    the fully backward-compatible form. Exactly one ``/`` means
    ``namespace/name``, e.g. an org the caller is a member of; the server
    is the authority on whether they may touch it (403/404). The namespace
    segment tolerates a leading ``@`` — the frontend's display form leaks
    into CLI/API calls constantly, and the server strips it too.

    Unambiguous by construction: annotation names cannot contain ``/``.
    """
    if not isinstance(name, str) or not name.strip():
        raise PlatformError(f"annotation name must be a non-empty string, got {name!r}")
    s = name.strip()
    if "/" not in s:
        return None, s
    parts = s.split("/")
    ns = parts[0].removeprefix("@") if len(parts) == 2 else ""
    if len(parts) != 2 or not ns or not parts[1]:
        raise PlatformError(
            f"invalid annotation name '{name}' — expected 'name' or "
            f"'namespace/name' (at most one '/', no empty segments)"
        )
    return ns, parts[1]


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
        f"{remote}/namespaces/{namespace}/annotations/{name}/upload-credentials",
        token,
        json_body={"durationSeconds": duration_seconds},
    )
    if r.status_code >= 400:
        raise PlatformError(
            f"could not get access credentials for annotation '{name}' "
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


def _platform_context(namespace: "str | None" = None) -> tuple[str, str, str]:
    """Resolve (token, namespace, remote) for a platform call. An explicit
    ``namespace`` (from a qualified name) skips the /auth/me resolution —
    the server authorizes it per request."""
    token = _session.get_token()
    remote = _session.remote_url()
    if not namespace:
        namespace = _session.get_namespace(token=token, remote=remote)
    return token, namespace, remote


def resolve_namespace(namespace: "str | None" = None) -> str:
    """The namespace a bare (unqualified) name would land in — the login's
    own — or ``namespace`` verbatim when given."""
    _, ns, _ = _platform_context(namespace)
    return ns


def _forbidden(r, namespace: str, doing: str) -> "PlatformError | None":
    if r.status_code == 403:
        return PlatformError(
            f"not allowed to {doing} in namespace '{namespace}' — you must be "
            f"its owner or a member of the organisation.{_server_said(r)}"
        )
    return None


# ── Catalog + brokered annotation handles ──────────────────────────────────────

def create_annotation(name: str, schema, *, schema_type: str,
                      schema_json: str | None = None,
                      visibility: str | None = None,
                      duration_seconds: int = DEFAULT_DURATION_SECONDS):
    """Register ``name`` (bare, or qualified ``namespace/name``) in the
    catalog, broker a credential lease, and create the dreamdb space on the
    brokered backend. Returns a live ``dreamdb.Dataset`` with
    ``dreamlake_lease`` attached."""
    dreamdb = _db._dreamdb()
    ns_arg, name = split_qualified(name)
    token, namespace, remote = _platform_context(ns_arg)

    body: dict = {"name": name, "schemaType": schema_type}
    if schema_json is not None:
        body["schemaJson"] = schema_json
    if visibility is not None:
        body["visibility"] = visibility

    r = _request("POST", f"{remote}/namespaces/{namespace}/annotations", token,
                 json_body=body)
    if r.status_code == 409:
        raise AnnotationExistsError(
            f"annotation '{name}' already exists in namespace '{namespace}' — "
            f"open it instead of creating, or delete it first."
        )
    if r.status_code >= 400:
        raise _forbidden(r, namespace, "create an annotation") or PlatformError(
            f"could not create annotation '{name}' ({r.status_code}).{_server_said(r)}"
        )

    broker = _broker_credentials(remote, namespace, name, token, duration_seconds)
    ds = dreamdb.Dataset.create(broker.get("refName", _db.REF_NAME), schema,
                                backend=broker["backendUrl"])
    # Stamp the space itself so direct-backend readers can self-describe it.
    ds.set_meta("dreamdb.schema_type", schema_type)
    return _attach_lease(ds, broker, namespace, name)


def get_annotation(name: str) -> dict:
    """The catalog row for ``name`` (bare or ``namespace/name``) — one GET,
    no credential brokering. The row carries ``schemaType`` (the SDK's class
    dispatch key) plus visibility etc.; ``namespace`` and ``name`` are
    stamped in so callers need no second resolution."""
    ns_arg, name = split_qualified(name)
    token, namespace, remote = _platform_context(ns_arg)

    r = _request("GET", f"{remote}/namespaces/{namespace}/annotations/{name}", token)
    if r.status_code == 404:
        raise AnnotationNotFoundError(
            f"annotation '{name}' not found in namespace '{namespace}' — "
            f"create it first."
        )
    if r.status_code >= 400:
        raise PlatformError(
            f"could not look up annotation '{name}' ({r.status_code}).{_server_said(r)}"
        )
    row = r.json()
    if not isinstance(row, dict):
        row = {}
    row.setdefault("name", name)
    row["namespace"] = namespace
    return row


def open_annotation(name: str, *, schema=None, row: dict | None = None,
                    duration_seconds: int = DEFAULT_DURATION_SECONDS):
    """Look ``name`` (bare or ``namespace/name``) up in the catalog, broker
    a credential lease, and open the dreamdb space on the brokered backend.
    Pass ``row=`` (a prior :func:`get_annotation` result) to skip the catalog
    GET — the dispatch path already holds it."""
    dreamdb = _db._dreamdb()
    if row is None:
        row = get_annotation(name)
    namespace, name = row["namespace"], row["name"]
    token, namespace, remote = _platform_context(namespace)

    broker = _broker_credentials(remote, namespace, name, token, duration_seconds)
    ds = dreamdb.Dataset.open(broker.get("refName", _db.REF_NAME), schema,
                              backend=broker["backendUrl"])
    return _attach_lease(ds, broker, namespace, name)


def patch_annotation(name: str, *, visibility: str) -> None:
    """Update the catalog row. The server's PATCH accepts visibility only."""
    ns_arg, name = split_qualified(name)
    token, namespace, remote = _platform_context(ns_arg)
    r = _request("PATCH", f"{remote}/namespaces/{namespace}/annotations/{name}",
                 token, json_body={"visibility": visibility})
    if r.status_code == 404:
        raise AnnotationNotFoundError(
            f"annotation '{name}' not found in namespace '{namespace}'."
        )
    if r.status_code >= 400:
        raise _forbidden(r, namespace, "update an annotation") or PlatformError(
            f"could not update annotation '{name}' ({r.status_code}).{_server_said(r)}"
        )


def list_annotations(schema_type: str | None = None, *,
                     namespace: str | None = None) -> list[dict]:
    """A namespace's annotation catalog (the login's own unless ``namespace=``),
    optionally filtered by schemaType."""
    token, ns, remote = _platform_context(
        namespace.removeprefix("@") if namespace else None
    )
    url = f"{remote}/namespaces/{ns}/annotations"
    if schema_type:
        url += f"?schemaType={schema_type}"
    r = _request("GET", url, token)
    if r.status_code >= 400:
        raise PlatformError(
            f"could not list annotations ({r.status_code}).{_server_said(r)}"
        )
    data = r.json()
    # "annotations" is the renamed catalog key; a server still sending the
    return data.get("annotations", [])


def delete_annotation(name: str, purge: bool = False) -> None:
    """Delete an annotation (bare or ``namespace/name``) from the catalog.
    ``purge=True`` also deletes the backing storage; otherwise only the
    catalog entry is removed."""
    ns_arg, name = split_qualified(name)
    token, namespace, remote = _platform_context(ns_arg)
    url = f"{remote}/namespaces/{namespace}/annotations/{name}"
    if purge:
        url += "/purge"
    r = _request("DELETE", url, token)
    if r.status_code == 404:
        raise AnnotationNotFoundError(
            f"annotation '{name}' not found in namespace '{namespace}'."
        )
    if r.status_code >= 400:
        raise _forbidden(r, namespace, "delete an annotation") or PlatformError(
            f"could not delete annotation '{name}' ({r.status_code}).{_server_said(r)}"
        )
