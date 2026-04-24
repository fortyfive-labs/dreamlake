"""
Dreamlake Python SDK

A simple and flexible SDK for ML experiment tracking and data storage.

Usage:

    # Remote mode (API server)
    from dreamlake import Episode

    with Episode(
        name="my-experiment",
        workspace="my-workspace",
        remote="http://localhost:3000",
        api_key="your-jwt-token"
    ) as episode:
        episode.log("Training started")
        episode.track("loss", {"step": 0, "value": 0.5})

    # Local mode (filesystem)
    with Episode(
        name="my-experiment",
        workspace="my-workspace",
        local_path=".dreamlake"
    ) as episode:
        episode.log("Training started")

    # Decorator style
    from dreamlake import dreamlake_episode

    @dreamlake_episode(
        name="my-experiment",
        workspace="my-workspace",
        remote="http://localhost:3000",
        api_key="your-jwt-token"
    )
    def train_model(episode):
        episode.log("Training started")
"""

from .episode import Episode, dreamlake_episode, OperationMode
from .client import RemoteClient
from .storage import LocalStorage
from .log import LogLevel, LogBuilder
from .params import ParametersBuilder

__version__ = "0.4.2"

# ── Python API ──────────────────────────────────────────────────────────────

from .api import Video, VideoArray, TextTrack, VectorIndex, Prefix
from .api._client import DreamLakeClient, get_client as _get_client
from .api.resource_id import parse_uri as _parse_uri


def load_video(uri: str) -> Video:
    """Load a Video by resource ID or URI.

    Examples:
        dl.load_video("v-BV1bW411n7fY9x01")
        dl.load_video("bss://localhost:10234/videos/69e7264a")
    """
    return Video(uri)


def load(resource_id: str):
    """Generic loader — parses type prefix and returns the appropriate object.

    Examples:
        dl.load("v-BV1bW411n7fY9x01")   → Video
        dl.load("tt-3fHj7kLm0pQs5uXw")  → TextTrack (future)
    """
    parsed = _parse_uri(resource_id)
    if parsed.get("type") == "video" or parsed.get("scheme") == "bss":
        return Video(resource_id)
    raise ValueError(f"Unsupported resource type: {resource_id}")


def upload(
    file_path: str,
    space: str | None = None,
    prefix: str | None = None,
    path: str | None = None,
    type: str | None = None,
) -> dict:
    """Upload a file to DreamLake. Type auto-detected from extension.

    Examples:
        dl.upload("./video.mp4", space="robotics@alice", prefix="/2026/04/run-042/camera/front")
    """
    from .api.prefix import resolve_path, resolve_space
    from pathlib import Path
    import hashlib
    import math

    resolved_space = resolve_space(space)
    resolved_path = resolve_path(prefix or path or "")

    if not resolved_space:
        raise ValueError("space is required. Set via dl.Prefix or space= arg.")

    # Parse space
    parts = resolved_space.split("@")
    if len(parts) == 2:
        space_slug, namespace = parts[0], parts[1]
    else:
        space_slug = parts[0]
        client = _get_client()
        me = client.get_auth_me()
        namespace = me.get("namespace", {}).get("slug", "")

    # Auto-detect type
    ext_map = {
        ".wav": "audio", ".mp3": "audio", ".aac": "audio", ".opus": "audio",
        ".mp4": "video", ".mkv": "video",
        ".jpg": "image", ".jpeg": "image", ".png": "image", ".gif": "image",
        ".webp": "image", ".bmp": "image", ".tiff": "image",
        ".jsonl": "label-track", ".vtt": "text-track", ".srt": "text-track",
    }
    fp = Path(file_path)
    asset_type = type or ext_map.get(fp.suffix.lower())
    if not asset_type:
        raise ValueError(f"Cannot detect type for {fp.suffix}. Use type= to specify.")

    client = _get_client()
    content = fp.read_bytes()
    raw_hash = hashlib.sha256(content).hexdigest()[:16]
    file_size = len(content)

    chunk_size = 10 * 1024 * 1024
    total_parts = max(1, math.ceil(file_size / chunk_size))

    # BSS route for upload
    bss_type_map = {"video": "videos", "audio": "audio", "image": "image",
                    "text-track": "text-tracks", "label-track": "labels"}
    bss_route = bss_type_map.get(asset_type, asset_type)

    mime_map = {"video": "video/mp4", "audio": "audio/wav", "image": "image/jpeg",
                "text-track": "text/vtt", "label-track": "application/x-jsonlines"}
    content_type = mime_map.get(asset_type, "application/octet-stream")

    # Multipart upload
    init = client.upload_init(bss_route, namespace, space_slug, raw_hash, content_type)
    upload_id, key = init["uploadId"], init["key"]

    part_urls = client.upload_parts(bss_route, upload_id, key, list(range(1, total_parts + 1)))

    import httpx as _httpx
    completed = []
    for pn in range(1, total_parts + 1):
        start = (pn - 1) * chunk_size
        end = min(start + chunk_size, file_size)
        chunk = content[start:end]
        r = _httpx.put(part_urls[str(pn)], content=chunk, headers={"Content-Type": content_type}, timeout=120)
        r.raise_for_status()
        completed.append({"partNumber": pn, "etag": r.headers["etag"]})

    client.upload_complete(bss_route, upload_id, key, completed)

    # Register in BSS
    bss_body = {
        "name": f"/{resolved_path}/{fp.name}" if resolved_path else f"/{fp.name}",
        "owner": namespace,
        "project": space_slug,
        "stagingHash": raw_hash,
    }
    bss_result = client.register_bss_asset(asset_type, bss_body)
    bss_id = bss_result.get("id")

    # Extract episode name from path
    path_parts = resolved_path.strip("/").split("/")
    episode_name = path_parts[0] if path_parts and path_parts[0] else None

    # Register in dreamlake-server
    dl_body = {
        "namespace": namespace,
        "space": space_slug,
        "name": f"/{resolved_path}/{fp.name}" if resolved_path else f"/{fp.name}",
    }
    if episode_name:
        dl_body["episodeName"] = episode_name
    # Map BSS ID field by type
    bss_id_field = {"video": "bssVideoId", "audio": "bssAudioId", "image": "bssImageId",
                    "text-track": "bssTextTrackId", "label-track": "bssLabelId"}.get(asset_type)
    if bss_id_field:
        dl_body[bss_id_field] = bss_id

    dl_result = client.register_dl_asset(asset_type, dl_body)
    return dl_result


def text_track(
    prefix: str | None = None,
    space: str | None = None,
    path: str | None = None,
) -> TextTrack:
    """Create a TextTrack for buffering and uploading text entries.

    Examples:
        track = dl.text_track(prefix="/2026/04/run-042/captions/llava", space="robotics@alice")
    """
    return TextTrack(prefix=prefix, space=space, path=path)


def vec_index(name: str, dim: int = 768) -> VectorIndex:
    """Create or connect to a named VectorIndex (Qdrant collection).

    Examples:
        index = dl.vec_index("my-experiment")
    """
    return VectorIndex(name, dim=dim)


__all__ = [
    # Legacy
    "Episode",
    "dreamlake_episode",
    "OperationMode",
    "RemoteClient",
    "LocalStorage",
    "LogLevel",
    "LogBuilder",
    "ParametersBuilder",
    # API
    "Video",
    "VideoArray",
    "TextTrack",
    "VectorIndex",
    "Prefix",
    "load_video",
    "load",
    "upload",
    "text_track",
    "vec_index",
]
