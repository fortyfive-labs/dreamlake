"""
DreamLake Python API.

Objects:
    Video       — video data, sliceable by time/frame
    VideoArray  — batch of videos with fancy indexing
    TextTrack   — time-aligned text entries
    VectorIndex — named vector index (Qdrant)

Functions:
    load_video  — load a Video by resource ID or URI
    load        — generic loader (parses type prefix)
    upload      — chunked upload with auto type detection
    text_track  — create a TextTrack
    vec_index   — create/connect to a VectorIndex

Context:
    Prefix      — context manager for project/path scoping
"""

from .video import Video, VideoArray
from .text_track import TextTrack
from .vector_index import VectorIndex
from .prefix import Prefix

__all__ = [
    "Video",
    "VideoArray",
    "TextTrack",
    "VectorIndex",
    "Prefix",
]
