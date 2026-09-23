"""Env-layer composition (RFC 0007, ``dreamlake.env-layers/v2``).

An env is a layer stack: an ordered list of layers, each carrying its own
compose rule (``merge`` | ``attach`` | ``override``), materialized over one
MjSpec into a self-contained env directory (entry XML + flat ``meshes/`` +
pinned ``dreamlake.layers.json``).

The engine composes only RESOLVED stacks -- every layer source a local
path; registry refs (``{"env": ...}``) must be resolved by the caller
(the DreamLake CLI) first.

mujoco is an optional dependency: ``pip install 'dreamlake[compose]'``.
``stack`` (parsing/validation) imports without it; ``compose_stack``
raises a :class:`ComposeError` pointing at the extra when it is missing.
"""

from .engine import Report, compose_stack
from .stack import ComposeError, Stack, load_stack, require_resolved

__all__ = [
    "ComposeError",
    "Report",
    "Stack",
    "compose_stack",
    "load_stack",
    "require_resolved",
]
