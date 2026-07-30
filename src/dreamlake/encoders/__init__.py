"""Pure encoding: media and text in, L2-normalized vectors out.

This package knows nothing about datasets, storage, or DreamDB — it only
produces embeddings. The model choices are a compatibility contract:
``openai/clip-vit-base-patch32`` (512-dim) and ``BAAI/bge-small-en-v1.5``
(384-dim) are both shipped by @xenova/transformers, so vectors produced
here match what a browser can produce later.

Requires ``torch``, ``transformers``, and ``pillow``. None is a hard
dependency of ``dreamlake``; the import fails here, at first use, with an
actionable message.
"""

try:
    import torch  # noqa: F401
    import transformers  # noqa: F401
    from PIL import Image  # noqa: F401
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "dreamlake.encoders needs torch, transformers, and pillow. "
        'Install them with: pip install "dreamlake[search]"'
    ) from e

from ._clip import DEFAULT_CLIP_MODEL, ClipEncoder
from ._text import DEFAULT_TEXT_MODEL, TextEncoder
from ._video import iter_video_frames

__all__ = [
    "ClipEncoder",
    "TextEncoder",
    "iter_video_frames",
    "DEFAULT_CLIP_MODEL",
    "DEFAULT_TEXT_MODEL",
]
