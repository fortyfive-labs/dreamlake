"""Robot-training datasets on DreamDB — see :class:`Dataset`.

Requires the ``dreamdb`` package (the DreamDB Python SDK) and ``ffmpeg`` on
PATH. Neither is a hard dependency of ``dreamlake``; the import fails here,
at first use, with an actionable message.
"""

try:
    import dreamdb  # noqa: F401
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "dreamlake.dataset needs the 'dreamdb' package (the DreamDB Python "
        "SDK). Install it with: pip install dreamdb"
    ) from e

from ._core import Dataset, DatasetError
from ._ffmpeg import FfmpegError, ProbeResult, probe
from ._schema import (
    DATASET_REF,
    DEFAULT_PREVIEW_FPS,
    MAX_VIDEO_SECONDS,
    VIDEO_STRIDE_NS,
    VideoMeta,
    base_anchor,
    frame_anchor,
    gid_of,
)

__all__ = [
    "Dataset",
    "DatasetError",
    "FfmpegError",
    "ProbeResult",
    "probe",
    "VideoMeta",
    "DATASET_REF",
    "DEFAULT_PREVIEW_FPS",
    "MAX_VIDEO_SECONDS",
    "VIDEO_STRIDE_NS",
    "base_anchor",
    "frame_anchor",
    "gid_of",
]
