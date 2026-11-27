"""Local CI/CT definitions and runtime graph recording; never captures output or uploads.

This harness is independent of ``client.cict``, the remote control-plane API.
"""
from .harness import Run, Suite

__all__ = ["Run", "Suite"]
