"""
Video and VideoArray objects.

Video is the core data object — sliceable by time (float) or frame (int).
Lazy: metadata fetched on first property access, frames downloaded on pixel access.
"""

from __future__ import annotations

import io
import math
import os
import tempfile
from typing import Iterator

import numpy as np
from PIL import Image

from ._client import DreamLakeClient, get_client
from .resource_id import encode_resource_id, parse_uri
from . import _ffmpeg


class Video:
    """A video or a time/frame slice of a video. Lazy-loaded."""

    def __init__(
        self,
        uri: str,
        client: DreamLakeClient | None = None,
        *,
        _meta: dict | None = None,
        _chunks: list[dict] | None = None,
        _st: float | None = None,
        _et: float | None = None,
    ):
        self._uri = uri
        self._client = client or get_client()
        self.__meta = _meta
        self.__chunks = _chunks
        self._st_override = _st
        self._et_override = _et
        self._video_id: str | None = None
        self._bss_url: str | None = None

        # Parse URI to extract video ID
        parsed = parse_uri(uri)
        if parsed["scheme"] == "bss":
            self._video_id = parsed["id"]
            self._bss_url = parsed["bss_url"]
        elif parsed["scheme"] == "resource":
            self._video_id = parsed["id"]

    # ── Lazy metadata ───────────────────────────────────────────────────

    @property
    def _meta(self) -> dict:
        if self.__meta is None:
            if not self._video_id:
                raise ValueError("Cannot fetch metadata without a video ID")
            self.__meta = self._client.get_video_meta(self._video_id)
        return self.__meta

    @property
    def _chunks_list(self) -> list[dict]:
        if self.__chunks is None:
            streams = self._meta.get("streams", [])
            if not streams:
                self.__chunks = []
            else:
                m3u8 = self._client.get_stream_playlist(self._video_id, streams[0])
                self.__chunks = self._client.parse_chunk_hashes(m3u8)
        return self.__chunks

    # ── Properties ──────────────────────────────────────────────────────

    @property
    def id(self) -> str:
        return encode_resource_id("video", self._video_id) if self._video_id else ""

    @property
    def uri(self) -> str:
        return self._uri

    @property
    def fps(self) -> float:
        return self._meta.get("fps", 30.0)

    @property
    def st(self) -> float:
        if self._st_override is not None:
            return self._st_override
        return self._meta.get("st", 0.0)

    @property
    def et(self) -> float:
        if self._et_override is not None:
            return self._et_override
        return self.st + self._meta.get("durationSec", self._meta.get("duration", 0.0))

    @property
    def duration(self) -> float:
        return self.et - self.st

    @property
    def frames(self) -> int:
        return int(self.duration * self.fps)

    @property
    def width(self) -> int:
        return self._meta.get("width", 0)

    @property
    def height(self) -> int:
        return self._meta.get("height", 0)

    # ── Frame access ────────────────────────────────────────────────────

    def _time_to_chunk_index(self, t: float) -> int:
        """Find which HLS chunk contains time t."""
        elapsed = 0.0
        for i, chunk in enumerate(self._chunks_list):
            if elapsed + chunk["duration"] > t:
                return i
            elapsed += chunk["duration"]
        return max(0, len(self._chunks_list) - 1)

    def _download_and_extract(self, time_sec: float) -> Image.Image:
        """Download the chunk containing time_sec and extract a frame."""
        chunk_idx = self._time_to_chunk_index(time_sec - self._meta.get("st", 0.0))
        chunks = self._chunks_list
        if not chunks:
            raise RuntimeError("No HLS chunks available for this video")
        chunk = chunks[min(chunk_idx, len(chunks) - 1)]

        # Calculate offset within chunk
        elapsed = sum(c["duration"] for c in chunks[:chunk_idx])
        offset = time_sec - self._meta.get("st", 0.0) - elapsed

        # Download chunk to temp file
        data = self._client.download_chunk(chunk["url"])
        with tempfile.NamedTemporaryFile(suffix=".ts", delete=False) as f:
            f.write(data)
            tmp_path = f.name

        try:
            png_bytes = _ffmpeg.extract_frame_at(tmp_path, max(0, offset))
            if not png_bytes:
                raise RuntimeError(f"Failed to extract frame at t={time_sec}")
            return Image.open(io.BytesIO(png_bytes))
        finally:
            os.unlink(tmp_path)

    @property
    def image(self) -> Image.Image:
        """For single-frame Video: return the frame as PIL Image."""
        return self._download_and_extract(self.st)

    @property
    def thumbnail(self) -> Image.Image:
        """Middle frame of the video/slice."""
        mid = self.st + self.duration / 2
        return self._download_and_extract(mid)

    def numpy(self) -> np.ndarray:
        """All frames as numpy array (N, H, W, 3). Downloads all chunks in range."""
        frames = list(self.iterator())
        if not frames:
            return np.empty((0, 0, 0, 3), dtype=np.uint8)
        return np.stack([np.asarray(f) for f in frames])

    def tensor(self):
        """All frames as torch tensor (N, C, H, W). Lazy torch import."""
        try:
            import torch
        except ImportError:
            raise ImportError("torch is required for .tensor(). Install: pip install torch")
        arr = self.numpy()
        # (N, H, W, C) → (N, C, H, W)
        return torch.from_numpy(arr).permute(0, 3, 1, 2)

    def iterator(self) -> Iterator[Image.Image]:
        """Iterate over all frames in the video/slice range."""
        chunks = self._chunks_list
        if not chunks:
            return

        video_st = self._meta.get("st", 0.0)
        elapsed = 0.0

        for chunk in chunks:
            chunk_st = video_st + elapsed
            chunk_et = chunk_st + chunk["duration"]
            elapsed += chunk["duration"]

            # Skip chunks before our range
            if chunk_et <= self.st:
                continue
            # Stop after our range
            if chunk_st >= self.et:
                break

            # Download and extract all frames from this chunk
            data = self._client.download_chunk(chunk["url"])
            with tempfile.NamedTemporaryFile(suffix=".ts", delete=False) as f:
                f.write(data)
                tmp_path = f.name

            try:
                start_in_chunk = max(0, self.st - chunk_st)
                end_in_chunk = min(chunk["duration"], self.et - chunk_st)
                frame_bytes_list = _ffmpeg.extract_frames(
                    tmp_path, start=start_in_chunk, end=end_in_chunk,
                )
                for png_bytes in frame_bytes_list:
                    yield Image.open(io.BytesIO(png_bytes))
            finally:
                os.unlink(tmp_path)

    # ── Indexing ────────────────────────────────────────────────────────

    def __getitem__(self, key) -> Video:
        if isinstance(key, (int, float)):
            return self._index_single(key)
        if isinstance(key, slice):
            return self._index_slice(key)
        raise TypeError(f"Video indices must be int, float, or slice, not {type(key).__name__}")

    def _index_single(self, key) -> Video:
        """Single index: int → frame, float → time. Returns single-frame Video."""
        if isinstance(key, float):
            t = self.st + key
        else:
            t = self.st + key / self.fps
        dt = 1.0 / self.fps
        return Video(
            self._uri, self._client,
            _meta=self.__meta, _chunks=self.__chunks,
            _st=t, _et=t + dt,
        )

    def _index_slice(self, key: slice) -> Video:
        """Slice: float → time range, int → frame range. Returns new Video."""
        start = key.start or 0
        stop = key.stop

        if isinstance(start, float) or isinstance(stop, float):
            # Time-based slicing (relative to current st)
            new_st = self.st + (float(start) if start else 0.0)
            new_et = self.st + float(stop) if stop is not None else self.et
        else:
            # Frame-based slicing (relative to current st)
            new_st = self.st + int(start) / self.fps
            new_et = self.st + int(stop) / self.fps if stop is not None else self.et

        new_st = max(new_st, self.st)
        new_et = min(new_et, self.et)

        return Video(
            self._uri, self._client,
            _meta=self.__meta, _chunks=self.__chunks,
            _st=new_st, _et=new_et,
        )

    # ── Chunking ────────────────────────────────────────────────────────

    def chunk(self, duration: float, by_iframe: bool = False) -> VideoArray:
        """Split into fixed-duration segments. Returns VideoArray."""
        videos = []
        t = self.st
        while t < self.et:
            end = min(t + duration, self.et)
            videos.append(Video(
                self._uri, self._client,
                _meta=self.__meta, _chunks=self.__chunks,
                _st=t, _et=end,
            ))
            t = end
        return VideoArray(videos)

    # ── Repr ────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        uri_short = self._uri
        if len(uri_short) > 30:
            uri_short = "..." + uri_short[-20:]
        if self._st_override is not None:
            return f'Video("{uri_short}", st={self.st}, et={self.et}, duration={self.duration:.1f})'
        try:
            return f'Video("{uri_short}", fps={self.fps}, duration={self.duration:.1f}, {self.width}x{self.height})'
        except Exception:
            return f'Video("{uri_short}")'


class VideoArray:
    """Array of Videos. Supports fancy indexing and batch tensor conversion."""

    def __init__(self, videos: list[Video]):
        self._videos = videos

    def __len__(self) -> int:
        return len(self._videos)

    def __iter__(self):
        return iter(self._videos)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._videos[key]
        if isinstance(key, slice):
            return VideoArray(self._videos[key])
        if isinstance(key, tuple):
            return self._fancy_index(key)
        raise TypeError(f"VideoArray indices must be int, slice, or tuple, not {type(key).__name__}")

    def _fancy_index(self, key: tuple):
        """Fancy indexing: clips[:, 0] = first frame of each clip."""
        if len(key) != 2:
            raise IndexError("VideoArray supports at most 2D indexing (batch, frame)")

        batch_key, frame_key = key

        # Resolve batch dimension
        if isinstance(batch_key, slice):
            selected = self._videos[batch_key]
        elif isinstance(batch_key, int):
            selected = [self._videos[batch_key]]
        else:
            raise TypeError(f"Unsupported batch index type: {type(batch_key)}")

        # Apply frame index to each video
        result = [v[frame_key] for v in selected]
        return VideoArray(result)

    def numpy(self) -> np.ndarray:
        """All videos as numpy array. Shape depends on content."""
        arrays = [v.numpy() for v in self._videos]
        if not arrays:
            return np.empty((0,), dtype=np.uint8)
        # If all same shape, stack into (N, ...) batch
        shapes = [a.shape for a in arrays]
        if len(set(shapes)) == 1:
            return np.stack(arrays)
        # Different shapes: return as object array
        return np.array(arrays, dtype=object)

    def tensor(self):
        """All videos as torch tensor. Requires all frames same size."""
        try:
            import torch
        except ImportError:
            raise ImportError("torch is required for .tensor(). Install: pip install torch")
        arr = self.numpy()
        if arr.dtype == object:
            raise ValueError("Cannot create tensor: videos have different frame sizes")
        return torch.from_numpy(arr)

    def __repr__(self) -> str:
        return f"VideoArray(len={len(self._videos)})"
