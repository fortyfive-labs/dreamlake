"""
Shared connection config for all CLI commands.
Resolved from env vars → ~/.dreamlake/config.json → defaults.
"""

from params_proto import proto, EnvVar

# Dev secrets (local only — never used in production)
_DEBUG_NAMESPACE = "testuser"
_DEBUG_DL_SECRET = "your-secret-key-change-this-in-production"


def _make_debug_token() -> str:
    """Generate a local dev JWT using the known dev secret."""
    import time
    try:
        import jwt as pyjwt
        payload = {"sub": "test-001", "userName": _DEBUG_NAMESPACE, "userId": "test-001", "iat": int(time.time())}
        return pyjwt.encode(payload, _DEBUG_DL_SECRET, algorithm="HS256")
    except ImportError:
        # Fallback: return a static pre-encoded token
        return "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ0ZXN0LTAwMSIsInVzZXJOYW1lIjoidGVzdHVzZXIiLCJ1c2VySWQiOiJ0ZXN0LTAwMSJ9.placeholder"


@proto.prefix
class ServerConfig:
    remote: str = EnvVar @ "DREAMLAKE_REMOTE" | "http://localhost:10334"
    token: str | None = EnvVar @ "DREAMLAKE_API_KEY" | None
    bss_url: str = EnvVar @ "DREAMLAKE_BSS_URL" | "http://localhost:10234"
    debug: bool = False  # skip auth checks, use dev tokens and local URLs

    @classmethod
    def resolve_token(cls) -> str | None:
        """Return token from config, falling back to keyring. In debug mode, returns a dev JWT."""
        if cls.debug:
            return _make_debug_token()
        if cls.token:
            return cls.token
        try:
            from dreamlake.auth.token_storage import get_token_storage
            return get_token_storage().load("dreamlake-token")
        except Exception:
            return None

    _cached_namespace: str | None = None

    @classmethod
    def resolve_namespace(cls) -> str | None:
        """Return current user's namespace slug. Queries server for the authoritative slug."""
        if cls.debug:
            return _DEBUG_NAMESPACE
        if cls._cached_namespace:
            return cls._cached_namespace
        token = cls.resolve_token()
        if not token:
            return None
        # Query server for authoritative namespace slug
        try:
            import httpx
            r = httpx.get(f"{cls.remote}/auth/me", headers={"Authorization": f"Bearer {token}"}, timeout=5)
            if r.status_code == 200:
                ns = r.json().get("namespace")
                if ns and ns.get("slug"):
                    cls._cached_namespace = ns["slug"]
                    return cls._cached_namespace
        except Exception:
            pass
        # Fallback: decode from JWT (stale but better than nothing)
        try:
            import jwt as pyjwt
            payload = pyjwt.decode(token, options={"verify_signature": False})
            return payload.get("username") or payload.get("sub")
        except Exception:
            return None
