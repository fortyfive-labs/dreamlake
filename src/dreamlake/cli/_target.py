"""
Target syntax parser for DreamLake CLI.

Syntax: space[@namespace][:episode]

Examples:
    robotics@alice:run-042       (project=robotics, namespace=alice, episode=run-042)
    robotics:run-042             (namespace → current user)
    robotics@alice               (no episode → project-level)
    robotics                     (minimal — namespace=current user, no episode)

Space syntax (for --project flag): space[@namespace]
    robotics@alice
    robotics                     (namespace → current user)
"""

from dataclasses import dataclass


@dataclass
class ParsedTarget:
    nameproject: str | None   # None → resolved from current user's token
    project: str
    episode: str | None     # None → project-level (episodeId = null)
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
        project_part, namespace = base.split("@", 1)
    else:
        project = base

    if not project:
        raise ValueError("target must include a project name")

    return ParsedTarget(namespace=namespace, project=space, episode=episode, path=path)


@dataclass
class ParsedProject:
    nameproject: str | None   # None → resolved from current user's token
    project: str


def parse_project(target: str) -> ParsedProject:
    """Parse a space target: space[@namespace]"""
    if "@" in target:
        project_part, namespace = target.split("@", 1)
    else:
        project = target
        namespace = None

    if not project:
        raise ValueError("target must include a project name")

    return ParsedProject(namespace=namespace, project=space)


def format_target(t: ParsedTarget) -> str:
    """Reconstruct target string from parsed components (for display)."""
    parts = t.project
    if t.namespace:
        parts += f"@{t.namespace}"
    if t.episode:
        parts += f":{t.episode}"
    if t.path:
        parts += f"//{t.path}"
    return parts


def format_project(s: ParsedProject) -> str:
    """Reconstruct project string for display."""
    if s.namespace:
        return f"{s.project}@{s.namespace}"
    return s.project
