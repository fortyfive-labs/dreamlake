"""Helpers shared by the asset-library importers."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path

#: filenames never shipped into a library
_SKIP_NAMES = {".DS_Store", "__pycache__", ".git", ".gitignore",
               ".gitattributes"}

_README_TITLE_SUFFIX = re.compile(
    r"\s*(description\s*)?\(mjcf\)\s*$|\s+description\s*$",
    re.IGNORECASE,
)

_LICENSE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Apache-2.0", ("apache license", "version 2.0")),
    ("BSD-3-Clause", ("redistribution and use in source and binary forms",
                      "neither the name")),
    ("MIT", ("permission is hereby granted, free of charge",)),
)


def iter_asset_files(dirpath: Path) -> Iterator[Path]:
    """Every regular file under ``dirpath``, relative, sorted, hygienic.

    Skips VCS/OS litter (:data:`_SKIP_NAMES`) and hidden files. Yields
    relative :class:`~pathlib.Path` objects; contents are never read,
    so 36k-file repos stream through in constant memory.
    """
    for root, dirs, files in os.walk(dirpath):
        dirs[:] = sorted(
            d for d in dirs
            if d not in _SKIP_NAMES and not d.startswith(".")
        )
        for name in sorted(files):
            if name in _SKIP_NAMES or name.startswith("."):
                continue
            yield (Path(root) / name).relative_to(dirpath)


def place_file(src: Path, dst: Path, link: bool = False) -> None:
    """Copy (default) or hardlink ``src`` to ``dst``, creating parents."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    if link:
        try:
            os.link(src, dst)
            return
        except OSError:  # cross-device etc. -- fall back to copying
            pass
    shutil.copy2(src, dst)


def readme_title(dirpath: Path) -> str | None:
    """First markdown H1 of ``<dirpath>/README.md``, cleaned up.

    Strips the Menagerie-style `` Description (MJCF)`` suffix -- the
    display convention generate_gallery.py uses.
    """
    readme = dirpath / "README.md"
    if not readme.exists():
        return None
    try:
        text = readme.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            title = _README_TITLE_SUFFIX.sub("", line.lstrip("#").strip())
            return title.rstrip() or None
        if line:  # H1 must lead the file
            return None
    return None


def prettify(name: str) -> str:
    """``shadow_hand`` -> ``Shadow Hand`` (fallback display title)."""
    words = re.split(r"[_\-]+", name)
    return " ".join(w[:1].upper() + w[1:] for w in words if w)


def sniff_license(dirpath: Path) -> str | None:
    """SPDX id sniffed from a LICENSE file's text; ``None`` if unsure."""
    for candidate in ("LICENSE", "LICENSE.txt", "LICENSE.md", "LICENCE"):
        path = dirpath / candidate
        if path.exists():
            try:
                text = path.read_text(
                    encoding="utf-8", errors="replace").lower()
            except OSError:
                return None
            for spdx, needles in _LICENSE_PATTERNS:
                if all(needle in text for needle in needles):
                    return spdx
            return None
    return None


def looks_compilable(xml_path: Path) -> bool:
    """Whether an XML looks like a standalone-compilable MJCF.

    Root element ``<mujoco>`` with a ``<worldbody>`` or ``<include>``
    somewhere -- keyframe/actuator-only include fragments fail this.
    """
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError:
        return False
    root = tree.getroot()
    if root.tag != "mujoco":
        return False
    return any(el.tag in ("worldbody", "include") for el in root.iter())


def sanitize_id(name: str) -> str | None:
    """Deterministic asset id from a directory name, or ``None``.

    Asset ids must match ``^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$``; real
    repos stray (GSO ships two ``Pokémon_*`` dirs). Accents are
    stripped via NFKD, every other illegal char becomes ``-``, and the
    result is trimmed to a legal first char and length. Callers should
    record the original name (e.g. in ``upstream.id``) when it
    differs.
    """
    ascii_name = unicodedata.normalize("NFKD", name)
    ascii_name = ascii_name.encode("ascii", "ignore").decode("ascii")
    ascii_name = re.sub(r"[^a-zA-Z0-9._-]", "-", ascii_name)
    ascii_name = ascii_name.lstrip("._-")[:128]
    return ascii_name or None


def git_head(path: Path) -> str | None:
    """``git rev-parse HEAD`` of a checkout, or ``None``."""
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    commit = out.stdout.strip()
    return commit or None
