"""
Target syntax parser for DreamLake CLI.

Syntax: space[@namespace][:episode]

Examples:
    robotics@alice:run-042       (space=robotics, namespace=alice, episode=run-042)
    robotics:run-042             (namespace → current user)
    robotics@alice               (no episode → space-level)
    robotics                     (minimal — namespace=current user, no episode)

Space syntax (for --space flag): space[@namespace]
    robotics@alice
    robotics                     (namespace → current user)
"""

from dataclasses import dataclass


@dataclass
class ParsedTarget:
    namespace: str | None   # None → resolved from current user's token
    space: str
    episode: str | None     # None → space-level (episodeId = null)
    path: str | None        # None → list scope / not required


def parse_target(target: str) -> ParsedTarget:
    """Parse a target string: space[@namespace][:episode][//path]"""
    path = None
    episode = None
    namespace = None

    # Split off path (everything after //)
    if "//" in target:
        target, path = target.split("//", 1)
        path = path.strip("/") or None

    # Split off episode (everything after last :)
    if ":" in target:
        base, episode = target.split(":", 1)
        episode = episode or None
    else:
        base = target

    # Split off namespace (everything after @)
    if "@" in base:
        space, namespace = base.split("@", 1)
    else:
        space = base

    if not space:
        raise ValueError("target must include a space name")

    return ParsedTarget(namespace=namespace, space=space, episode=episode, path=path)


@dataclass
class ParsedSpace:
    namespace: str | None   # None → resolved from current user's token
    space: str


def parse_space(target: str) -> ParsedSpace:
    """Parse a space target: space[@namespace]"""
    if "@" in target:
        space, namespace = target.split("@", 1)
    else:
        space = target
        namespace = None

    if not space:
        raise ValueError("target must include a space name")

    return ParsedSpace(namespace=namespace, space=space)


def format_target(t: ParsedTarget) -> str:
    """Reconstruct target string from parsed components (for display)."""
    parts = t.space
    if t.namespace:
        parts += f"@{t.namespace}"
    if t.episode:
        parts += f":{t.episode}"
    if t.path:
        parts += f"//{t.path}"
    return parts


def format_space(s: ParsedSpace) -> str:
    """Reconstruct space string for display."""
    if s.namespace:
        return f"{s.space}@{s.namespace}"
    return s.space
