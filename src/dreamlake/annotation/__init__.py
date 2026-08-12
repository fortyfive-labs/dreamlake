"""DreamLake annotations — one family, dispatched by schemaType.

:class:`Annotation` is the generic member: platform annotations with a
USER-DEFINED schema (declare tracks, append rows/ranges, read back — see
``_base``). Known schemaTypes get a preset subclass with rich methods:
:class:`VideoAnnotation` (``video.annotation/v2``) is the
robot-training episode preset. ``Annotation.open(name)`` returns whichever
class the catalog's schemaType names; unknown types degrade to the generic
handle, never refuse.

Requires the ``dreamdb`` package (the DreamDB Python SDK); the preset's
video paths additionally need ``ffmpeg`` on PATH. Neither is a hard
dependency of ``dreamlake``; the import fails here, at first use, with an
actionable message.
"""

try:
    import dreamdb  # noqa: F401
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "dreamlake.annotation needs the 'dreamdb' package (the DreamDB Python "
        "SDK). Install it with: pip install dreamdb"
    ) from e

from ._base import Annotation, AnnotationInfo
from ._core import VideoAnnotation
from ._episode import Episode
from ._errors import AnnotationError, SchemaError
from ._ffmpeg import FfmpegError, ProbeResult, probe
from ._fields import Schema, sequence_anchors
from ._track import Track
from ._schema import (
    ANNOTATION_REF,
    DEFAULT_CAMERA,
    DEFAULT_PREVIEW_FPS,
    EPISODE_STRIDE_NS,
    MAX_EPISODE_SECONDS,
    CameraMeta,
    EpisodeMeta,
    base_anchor,
    classify_track,
    frame_anchor,
    gid_of,
    joints_track,
    preview_track,
    raw_track,
)

__all__ = [
    # the family
    "Annotation",
    "AnnotationInfo",
    "Schema",
    "Track",
    "sequence_anchors",
    "AnnotationError",
    "SchemaError",
    # the video-annotation preset
    "VideoAnnotation",
    "Episode",
    "FfmpegError",
    "ProbeResult",
    "probe",
    "CameraMeta",
    "EpisodeMeta",
    "classify_track",
    "ANNOTATION_REF",
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
