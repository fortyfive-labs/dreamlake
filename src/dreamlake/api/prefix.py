"""
Prefix context manager for scoping space and path.

Usage:
    with dl.Prefix(project="robotics@alice", prefix="/2026/04/run-042"):
        dl.upload("./video.mp4", path="camera/front")
        # resolves to: /2026/04/run-042/camera/front
"""

import os
import contextvars

_ctx_project = contextvars.ContextVar("dl_project", default=None)
_ctx_prefix = contextvars.ContextVar("dl_prefix", default="")


class Prefix:
    """Context manager that sets space and path prefix for all DreamLake calls."""

    def __init__(self, project: str | None = None, prefix: str = ""):
        self._project = project
        self._prefix = prefix
        self._tokens: list = []

    def __enter__(self):
        if self._project is not None:
            self._tokens.append(_ctx_project.set(self._project))

        current = _ctx_prefix.get()
        if self._prefix.startswith("/"):
            self._tokens.append(_ctx_prefix.set(self._prefix))
        elif self._prefix:
            self._tokens.append(_ctx_prefix.set(os.path.join(current, self._prefix)))

        return self

    def __exit__(self, *args):
        for token in reversed(self._tokens):
            token.var.reset(token)
        self._tokens.clear()


def resolve_path(path: str | None = None) -> str:
    """Resolve a path against the current prefix context.

    Absolute paths (starting with /) ignore the prefix.
    Relative paths are appended to the current prefix.
    """
    prefix = _ctx_prefix.get()
    if path is None:
        return prefix
    if path.startswith("/"):
        return path
    if prefix:
        return os.path.join(prefix, path)
    return path


def resolve_project(project: str | None = None) -> str | None:
    """Return explicit project or the one from the current prefix context."""
    return project or _ctx_project.get()
