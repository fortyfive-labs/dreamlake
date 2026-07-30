"""Unified auth / remote / namespace resolution for the dreamlake SDK.

Single source of truth for "who am I, against which server?" — shared by the
SDK (``dreamlake.db``), the API client, and the CLI, so ``dreamlake login``
works the same everywhere.

Precedence:

* token:      ``DREAMLAKE_API_KEY`` env var → token storage
              (keyring → encrypted file → plaintext, see ``auth.token_storage``)
* remote:     ``DREAMLAKE_REMOTE`` env var → ``remote_url`` in
              ``~/.dreamlake/config.json`` → ``DEFAULT_REMOTE_URL``
* namespace:  ``GET {remote}/auth/me`` (authoritative slug, cached per
              (remote, token)) → JWT payload fallback (``username``/``sub``,
              stale but better than nothing) when the server is unreachable.

This module must stay dependency-light: no params_proto, nothing from
``dreamlake.cli``. httpx is imported lazily inside the one function that
needs it.
"""

from __future__ import annotations

import os

import dreamlake.config as _config
from dreamlake.auth.exceptions import NotAuthenticatedError

# The key under which `dreamlake login` stores the API token.
TOKEN_STORAGE_KEY = "dreamlake-token"

# Authoritative namespace slugs, keyed by (remote, token). Only server
# responses are cached — the JWT fallback is recomputed every time so a
# recovered server wins on the next call.
_namespace_cache: dict[tuple[str, str], str] = {}


def get_token_or_none() -> str | None:
    """Resolve the API token, or None if the user is not logged in.

    ``DREAMLAKE_API_KEY`` env var wins; otherwise the token stored by
    ``dreamlake login`` (keyring / encrypted file / plaintext fallback).
    """
    env = os.environ.get("DREAMLAKE_API_KEY")
    if env:
        return env
    try:
        from dreamlake.auth.token_storage import get_token_storage

        return get_token_storage().load(TOKEN_STORAGE_KEY)
    except Exception:
        return None


def get_token() -> str:
    """Resolve the API token, raising :class:`NotAuthenticatedError` if absent."""
    token = get_token_or_none()
    if not token:
        raise NotAuthenticatedError(
            "Not authenticated. Run 'dreamlake login' or set the "
            "DREAMLAKE_API_KEY environment variable."
        )
    return token


def remote_url() -> str:
    """Resolve the DreamLake server URL (no trailing slash)."""
    env = os.environ.get("DREAMLAKE_REMOTE")
    if env:
        return env.rstrip("/")
    stored = _config.config.remote_url
    if stored:
        return stored.rstrip("/")
    return _config.DEFAULT_REMOTE_URL


def get_namespace(token: str | None = None, remote: str | None = None) -> str:
    """Resolve the current user's namespace slug.

    Asks ``GET {remote}/auth/me`` for the authoritative slug (cached per
    (remote, token)); if the server is unreachable or answers non-200, falls
    back to decoding the JWT payload without verification (``username`` or
    ``sub`` claim — stale but better than nothing).

    Raises RuntimeError when neither source yields a slug, and
    :class:`NotAuthenticatedError` when there is no token at all.
    """
    tok = token or get_token()
    rem = (remote or remote_url()).rstrip("/")

    cached = _namespace_cache.get((rem, tok))
    if cached:
        return cached

    # 1) Authoritative: ask the server.
    try:
        import httpx

        r = httpx.get(
            f"{rem}/auth/me",
            headers={"Authorization": f"Bearer {tok}"},
            timeout=5,
        )
        if r.status_code == 200:
            ns = r.json().get("namespace") or {}
            slug = ns.get("slug")
            if slug:
                _namespace_cache[(rem, tok)] = slug
                return slug
    except Exception:
        pass

    # 2) Fallback: decode the JWT payload without verification.
    from dreamlake.auth.token_storage import decode_jwt_payload

    payload = decode_jwt_payload(tok)
    slug = payload.get("username") or payload.get("sub")
    if slug:
        return slug

    raise RuntimeError(
        f"Could not resolve your namespace: {rem}/auth/me did not answer and "
        "the token carries no 'username'/'sub' claim. Check that the server "
        "is reachable, or run 'dreamlake login' to refresh your token."
    )
