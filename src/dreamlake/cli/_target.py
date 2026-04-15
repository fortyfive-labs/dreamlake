"""
Target syntax parser for DreamLake CLI.

Syntax: [namespace@]space[:episode][//path]

Examples:
    alice@robotics:experiments/run-042//microphone/front
    robotics:experiments/run-042//microphone/front   (namespace → current user)
    alice@robotics//audio/ambient                    (no episode → space-level)
    alice@robotics:experiments/run-042               (no path → list scope)
    robotics                                         (minimal)
"""

from dataclasses import dataclass


@dataclass
class ParsedTarget:
    namespace: str | None   # None → resolved from current user's token
    space: str
    episode: str | None     # None → space-level (episodeId = null)
    path: str | None        # None → list scope / not required


def parse_target(target: str) -> ParsedTarget:
    """Parse a target string into its components."""
    path = None
    episode = None
    namespace = None

    # Split off path (everything after //)
    if "//" in target:
        target, path = target.split("//", 1)
        path = path.strip("/") or None

    # Split off namespace (everything before @)
    if "@" in target:
        namespace, target = target.split("@", 1)

    # Split off episode (everything after :)
    if ":" in target:
        space, episode = target.split(":", 1)
        episode = episode or None
    else:
        space = target

    if not space:
        raise ValueError("target must include a space name")

    return ParsedTarget(namespace=namespace, space=space, episode=episode, path=path)


def format_target(t: ParsedTarget) -> str:
    """Reconstruct target string from parsed components (for display)."""
    parts = ""
    if t.namespace:
        parts += f"{t.namespace}@"
    parts += t.space
    if t.episode:
        parts += f":{t.episode}"
    if t.path:
        parts += f"//{t.path}"
    return parts
