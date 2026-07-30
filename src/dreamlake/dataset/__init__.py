"""Robot-training episode datasets on DreamDB — see :class:`Dataset`.

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
from ._episode import Episode
from ._ffmpeg import FfmpegError, ProbeResult, probe
from ._schema import (
    DATASET_REF,
    DEFAULT_CAMERA,
    DEFAULT_PREVIEW_FPS,
    EPISODE_STRIDE_NS,
    MAX_EPISODE_SECONDS,
    CameraMeta,
    EpisodeMeta,
    TrackInfo,
    base_anchor,
    classify_track,
    frame_anchor,
    gid_of,
    joints_track,
    preview_track,
    raw_track,
)

__all__ = [
    "Dataset",
    "Episode",
    "DatasetError",
    "FfmpegError",
    "ProbeResult",
    "probe",
    "CameraMeta",
    "EpisodeMeta",
    "TrackInfo",
    "classify_track",
    "DATASET_REF",
    "DEFAULT_CAMERA",
    "DEFAULT_PREVIEW_FPS",
    "EPISODE_STRIDE_NS",
    "MAX_EPISODE_SECONDS",
    "base_anchor",
    "frame_anchor",
    "gid_of",
    "joints_track",
    "preview_track",
    "raw_track",
]
