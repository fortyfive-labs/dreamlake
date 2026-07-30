"""
Shared connection config for all CLI commands.
Resolved from env vars → ~/.dreamlake/config.json → defaults.
"""

from params_proto import proto, EnvVar

from dreamlake.config import DEFAULT_REMOTE_URL

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
    remote: str = EnvVar @ "DREAMLAKE_REMOTE" | DEFAULT_REMOTE_URL
    token: str | None = EnvVar @ "DREAMLAKE_API_KEY" | None
    bss_url: str = EnvVar @ "DREAMLAKE_BSS_URL" | "http://localhost:10234"
    debug: bool = False  # skip auth checks, use dev tokens and local URLs

    @classmethod
    def resolve_token(cls) -> str | None:
        """Return token from config, falling back to storage (via dreamlake._session).
        In debug mode, returns a dev JWT."""
        if cls.debug:
            return _make_debug_token()
        if cls.token:
            return cls.token
        from dreamlake import _session
        return _session.get_token_or_none()

    @classmethod
    def resolve_namespace(cls) -> str | None:
        """Return current user's namespace slug (server-authoritative, JWT fallback).

        Delegates to dreamlake._session, which caches per (remote, token)."""
        if cls.debug:
            return _DEBUG_NAMESPACE
        token = cls.resolve_token()
        if not token:
            return None
        from dreamlake import _session
        try:
            return _session.get_namespace(token=token, remote=cls.remote)
        except Exception:
            return None
