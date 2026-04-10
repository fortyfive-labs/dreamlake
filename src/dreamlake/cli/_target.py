"""
Target syntax parser for DreamLake CLI.

Syntax: [namespace@]space[:session][//path]

Examples:
    alice@robotics:experiments/run-042//microphone/front
    robotics:experiments/run-042//microphone/front   (namespace → current user)
    alice@robotics//audio/ambient                    (no session → space-level)
    alice@robotics:experiments/run-042               (no path → list scope)
    robotics                                         (minimal)
"""

from dataclasses import dataclass


@dataclass
class ParsedTarget:
    namespace: str | None   # None → resolved from current user's token
    space: str
    session: str | None     # None → space-level (sessionId = null)
    path: str | None        # None → list scope / not required


def parse_target(target: str) -> ParsedTarget:
    """Parse a target string into its components."""
    path = None
    session = None
    namespace = None

    # Split off path (everything after //)
    if "//" in target:
        target, path = target.split("//", 1)
        path = path.strip("/") or None

    # Split off namespace (everything before @)
    if "@" in target:
        namespace, target = target.split("@", 1)

    # Split off session (everything after :)
    if ":" in target:
        space, session = target.split(":", 1)
        session = session or None
    else:
        space = target

    if not space:
        raise ValueError("target must include a space name")

    return ParsedTarget(namespace=namespace, space=space, session=session, path=path)


def format_target(t: ParsedTarget) -> str:
    """Reconstruct target string from parsed components (for display)."""
    parts = ""
    if t.namespace:
        parts += f"{t.namespace}@"
    parts += t.space
    if t.session:
        parts += f":{t.session}"
    if t.path:
        parts += f"//{t.path}"
    return parts
