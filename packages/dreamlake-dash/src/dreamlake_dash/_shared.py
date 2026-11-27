"""Shared recording primitives, also re-exported by the umbrella SDK."""

from enum import Enum

import httpx

DEFAULT_REMOTE_URL = "https://api.dreamlake.ai"

class OperationMode(Enum):
    """Operation mode for the episode."""
    LOCAL = "local"
    REMOTE = "remote"
    HYBRID = "hybrid"  # Future: sync local to remote


class LogLevel(Enum):
    """
    Standard log levels for Dreamlake.

    Supported levels:
    - INFO: Informational messages
    - WARN: Warning messages
    - ERROR: Error messages
    - DEBUG: Debug messages
    - FATAL: Fatal error messages
    """
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    DEBUG = "debug"
    FATAL = "fatal"

    @classmethod
    def validate(cls, level: str) -> str:
        """
        Validate and normalize log level.

        Args:
            level: Log level string (case-insensitive)

        Returns:
            Normalized log level string (lowercase)

        Raises:
            ValueError: If level is not one of the 5 standard levels

        Example:
            >>> LogLevel.validate("INFO")
            "info"
            >>> LogLevel.validate("invalid")
            ValueError: Invalid log level: 'invalid'. Must be one of: info, warn, error, debug, fatal
        """
        level_lower = level.lower()
        try:
            return cls[level_lower.upper()].value
        except KeyError:
            valid_levels = ", ".join([l.value for l in cls])
            raise ValueError(
                f"Invalid log level: '{level}'. "
                f"Must be one of: {valid_levels}"
            )


class ExperimentRequestError(Exception):
    """A native Experiment request failed; writes are never retried implicitly.

    ``status`` is the HTTP status (None for transport/shape failures), ``code``
    the server's ``error.code`` string when the response carried one (e.g.
    ``OPERATION_CONFLICT``, ``NATIVE_RUN_ACTIVE``), and ``body`` the parsed JSON
    response (or raw text). ``str(error)`` never echoes the body.
    """

    def __init__(self, status: int | None, message: str, *, code: str | None = None, body=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.body = body


def _error_code(body) -> str | None:
    err = body.get("error") if isinstance(body, dict) else None
    code = err.get("code") if isinstance(err, dict) else None
    return code if isinstance(code, str) else None


class _BorrowedTransport(httpx.BaseTransport):
    """Let short-lived HTTP clients share a transport owned by their caller."""

    def __init__(self, transport: httpx.BaseTransport):
        self._transport = transport

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self._transport.handle_request(request)

    def close(self) -> None:
        # Only the caller that supplied the transport controls its lifetime.
        pass
