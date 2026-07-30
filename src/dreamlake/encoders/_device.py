"""Device auto-selection shared by the encoders."""

from __future__ import annotations


def auto_device() -> str:
    """Best available torch device: cuda > mps > cpu."""
    import torch

    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"
