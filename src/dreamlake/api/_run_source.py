"""Explicit, bounded source manifests for tracked runs; no Git or shell required."""
from __future__ import annotations

import base64
import hashlib
import os
import re
import stat
from collections.abc import Sequence
from pathlib import Path


class RunConfigurationError(ValueError):
    """Invalid tracked-run input; messages never include file contents."""


def collect_source(includes: Sequence[str], cwd: str | Path | None = None,
                   limit: int = 8 * 1024 * 1024) -> list[dict]:
    """Read explicit regular files beneath cwd, without following path symlinks."""
    if isinstance(includes, (str, bytes)) or not isinstance(includes, Sequence):
        raise RunConfigurationError("include must be a sequence of relative file paths")
    if len(includes) > 100:
        raise RunConfigurationError("At most 100 explicitly included files are supported")
    paths = []
    seen = set()
    for path in includes:
        if (not isinstance(path, str) or not path or "\\" in path or "\0" in path
                or path.startswith("/") or re.match(r"^[A-Za-z]:", path)
                or any(part in {"", ".", ".."} for part in path.split("/"))):
            raise RunConfigurationError("Included files must be relative paths without traversal")
        if path in seen:
            raise RunConfigurationError("Duplicate included file")
        seen.add(path)
        paths.append(path)
    result = []
    total = 0
    try:
        root = os.open(cwd or ".", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for path in paths:
                # Keep open directory descriptors so concurrent path replacement cannot
                # turn a checked directory into a symlink to another subtree.
                parent = os.dup(root)
                try:
                    parts = path.split("/")
                    for part in parts[:-1]:
                        child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                        dir_fd=parent)
                        os.close(parent)
                        parent = child
                    fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=parent)
                    with os.fdopen(fd, "rb") as source:
                        info = os.fstat(source.fileno())
                        if not stat.S_ISREG(info.st_mode):
                            raise RunConfigurationError("Included paths must be regular files, not links or directories")
                        if info.st_size > limit - total:
                            raise RunConfigurationError("Explicit source exceeds the decoded upload size limit")
                        data = source.read(limit - total + 1)
                        if len(data) > limit - total:
                            raise RunConfigurationError("Explicit source exceeds the decoded upload size limit")
                    total += len(data)
                    result.append({"path": path, "contentBase64": base64.b64encode(data).decode(),
                                   "sha256": hashlib.sha256(data).hexdigest(),
                                   "mode": 493 if info.st_mode & 0o111 else 420})
                finally:
                    os.close(parent)
        finally:
            os.close(root)
    except OSError:
        raise RunConfigurationError("Cannot read included regular files without following symlinks") from None
    return result
