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
    project: str | None = None,
    prefix: str | None = None,
    path: str | None = None,
    type: str | None = None,
) -> dict:
    """Upload a file to DreamLake. Type auto-detected from extension.

    Examples:
        dl.upload("./video.mp4", project="robotics@alice", prefix="/2026/04/run-042/camera/front")
    """
    from .api.prefix import resolve_path, resolve_project
    from pathlib import Path
    import hashlib
    import math

    resolved_project = resolve_project(project)
    resolved_path = resolve_path(prefix or path or "")

    if not resolved_project:
        raise ValueError("project is required. Set via dl.Prefix or project= arg.")

    # Parse project
    parts = resolved_project.split("@")
    if len(parts) == 2:
        project_slug, namespace = parts[0], parts[1]
    else:
        project_slug = parts[0]
        client = _get_client()
        me = client.get_auth_me()
        namespace = me.get("namespace", {}).get("slug", "")

    client = _get_client()

    # Auto-detect kind from extension. Free-form — anything that isn't in this
    # table falls through to kind="file" (still uploads, just no special tag).
    ext_to_kind = {
        ".mp4": "video", ".mkv": "video", ".mov": "video", ".webm": "video",
        ".wav": "audio", ".mp3": "audio", ".aac": "audio", ".opus": "audio", ".flac": "audio",
        ".jpg": "image", ".jpeg": "image", ".png": "image", ".gif": "image",
        ".webp": "image", ".bmp": "image", ".tiff": "image",
        ".jsonl": "label-track", ".vtt": "text-track", ".srt": "text-track",
        ".parquet": "parquet", ".csv": "csv", ".npy": "npy", ".npz": "npy",
        ".pkl": "pickle", ".pickle": "pickle",
        ".txt": "text", ".md": "text", ".log": "text",
        ".json": "json",
    }

    # MIME content-types (browsers + S3 use this for Content-Type headers)
    ext_to_mime = {
        ".mp4": "video/mp4", ".mkv": "video/x-matroska", ".mov": "video/quicktime", ".webm": "video/webm",
        ".wav": "audio/wav", ".mp3": "audio/mpeg", ".aac": "audio/aac",
        ".opus": "audio/ogg", ".flac": "audio/flac",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp", ".tiff": "image/tiff",
        ".vtt": "text/vtt", ".srt": "text/plain", ".jsonl": "application/x-jsonlines",
        ".parquet": "application/vnd.apache.parquet", ".csv": "text/csv",
        ".npy": "application/octet-stream", ".npz": "application/octet-stream",
        ".pkl": "application/octet-stream", ".pickle": "application/octet-stream",
        ".txt": "text/plain", ".md": "text/markdown", ".log": "text/plain",
        ".json": "application/json",
    }

    fp = Path(file_path)
    ext = fp.suffix.lower()
    asset_type = type or ext_to_kind.get(ext) or "file"  # default kind="file"
    content_type = ext_to_mime.get(ext, "application/octet-stream")

    from rich.progress import Progress, BarColumn, TextColumn, TransferSpeedColumn

    content = fp.read_bytes()
    raw_hash = hashlib.sha256(content).hexdigest()[:16]
    file_size = len(content)

    chunk_size = 10 * 1024 * 1024
    total_parts = max(1, math.ceil(file_size / chunk_size))

    # Build full asset name
    full_name = f"{resolved_path}/{fp.name}" if resolved_path else fp.name
    if not full_name.startswith("/"):
        full_name = f"/{full_name}"

    size_str = f"{file_size / 1024 / 1024:.1f} MB" if file_size > 1024 * 1024 else f"{file_size / 1024:.1f} KB"
    print(f"  uploading {fp.name} (kind={asset_type}, {size_str})")

    # Multipart upload — unified /files route
    init = client.upload_init("files", namespace, project_slug, raw_hash, content_type)
    upload_id, key = init["uploadId"], init["key"]
    part_urls = client.upload_parts("files", upload_id, key, list(range(1, total_parts + 1)))

    import httpx as _httpx
    completed = []

    with Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total} parts"),
        TransferSpeedColumn(),
        transient=True,
    ) as progress:
        task = progress.add_task("uploading", total=total_parts)
        for pn in range(1, total_parts + 1):
            start = (pn - 1) * chunk_size
            end = min(start + chunk_size, file_size)
            chunk = content[start:end]
            r = _httpx.put(part_urls[str(pn)], content=chunk, headers={"Content-Type": content_type}, timeout=120)
            r.raise_for_status()
            completed.append({"partNumber": pn, "etag": r.headers["etag"]})
            progress.advance(task)

    client.upload_complete("files", upload_id, key, completed)

    # Episode name = last segment of the Prefix context's prefix
    # The path under the episode = resolved_path stripped of the episode segment
    from .api.prefix import _ctx_prefix
    ctx_prefix_str = _ctx_prefix.get().strip("/")
    if ctx_prefix_str:
        episode_name = ctx_prefix_str.split("/")[-1]
    else:
        episode_name = None

    # Build the name to send to the server — relative to the episode (no episode prefix).
    # The server creates folders under the episode's basePath, so we strip the prefix.
    if ctx_prefix_str:
        ctx_prefix_normalized = "/" + ctx_prefix_str
        if full_name.startswith(ctx_prefix_normalized + "/"):
            server_name = full_name[len(ctx_prefix_normalized):]
        elif full_name == ctx_prefix_normalized + "/" + fp.name:
            server_name = "/" + fp.name
        else:
            server_name = full_name
    else:
        server_name = full_name

    # Register in BSS — unified /files route, kind passes through
    bss_body = {
        "name": full_name,
        "owner": namespace,
        "project": project_slug,
        "stagingHash": raw_hash,
        "kind": asset_type,
        "contentType": content_type,
        "size": file_size,
        "originalName": fp.name,
    }
    bss_result = client.register_bss_asset("file", bss_body)
    bss_id = bss_result.get("id")

    # Register in dreamlake-server (unified POST /nodes)
    node_body: dict = {
        "namespace": namespace,
        "kind": asset_type,
        "name": server_name,
        "project": project_slug,
        "metadata": {
            "bssId": bss_id,
            "hash": raw_hash,
            "size": file_size,
            "contentType": content_type,
        },
    }
    if episode_name:
        node_body["episode"] = episode_name

    try:
        dl_result = client.register_node(node_body)
    except Exception as e:
        import httpx as _hx
        if isinstance(e, _hx.HTTPStatusError) and e.response.status_code == 404:
            server_err = e.response.json().get("error", "")
            raise ValueError(f"Registration failed: {server_err}") from e
        raise
    print(f"  done: {full_name}")
    return dl_result


def _download_file(client, node_id: str, dest_path: "Path") -> None:
    """Stream a single file by node ID to dest_path."""
    import httpx as _httpx
    from rich.progress import Progress, BarColumn, TextColumn, TransferSpeedColumn, DownloadColumn

    presigned = client.get_node_download_url(node_id)
    url = presigned["url"]

    dest_path.parent.mkdir(parents=True, exist_ok=True)

    with _httpx.stream("GET", url, follow_redirects=True, timeout=300.0) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        with Progress(
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            transient=True,
        ) as progress:
            task = progress.add_task(dest_path.name, total=total or None)
            with open(dest_path, "wb") as f:
                for chunk in r.iter_bytes(chunk_size=64 * 1024):
                    f.write(chunk)
                    progress.update(task, advance=len(chunk))


def _resolve_to_node(node_id_or_path: str, project: str | None) -> dict:
    """Resolve a node ID or path to a node record."""
    from .api.prefix import resolve_path, resolve_project, _ctx_prefix
    import re

    client = _get_client()

    # Detect: 24-char hex = node ID
    if re.fullmatch(r"[a-fA-F0-9]{24}", node_id_or_path):
        # We only have the ID; need to fetch the node — use descendants endpoint
        # with leavesOnly=false to also list folders, but easier: lookup by path is not
        # possible from ID alone. Just call get_node_download_url to validate
        # and return a minimal record.
        # However for folder downloads we need kind. Hit GET /nodes?... or skip.
        # Simpler: use the descendants endpoint — it returns the root too.
        result = client.get_node_descendants(node_id_or_path)
        return result["root"]

    # Path resolution
    resolved_project = resolve_project(project)
    if not resolved_project:
        raise ValueError("project is required when using a path. "
                         "Set via dl.Prefix or project= arg.")
    parts = resolved_project.split("@")
    if len(parts) == 2:
        project_slug, namespace = parts[0], parts[1]
    else:
        project_slug = parts[0]
        me = client.get_auth_me()
        namespace = me.get("namespace", {}).get("slug", "")

    ctx_prefix_str = _ctx_prefix.get().strip("/")
    episode_name = ctx_prefix_str.split("/")[-1] if ctx_prefix_str else None

    if node_id_or_path.startswith("/"):
        asset_path = node_id_or_path
    else:
        resolved = resolve_path(node_id_or_path)
        if ctx_prefix_str:
            ctx_norm = "/" + ctx_prefix_str
            if resolved.startswith(ctx_norm + "/"):
                asset_path = resolved[len(ctx_norm):]
            elif resolved == ctx_norm:
                asset_path = "/"
            else:
                asset_path = resolved
        else:
            asset_path = resolved if resolved.startswith("/") else "/" + resolved

    return client.lookup_node(namespace, project_slug, asset_path, episode=episode_name)


def download(
    node_id_or_path: str,
    to: str | None = None,
    project: str | None = None,
) -> "Path":
    """Download a file or folder by node ID or path.

    - File leaf nodes (video/audio/image/...) download as a single file.
    - Container nodes (folder/episode/project) recursively download all
      file descendants, preserving relative folder structure.

    Args:
        node_id_or_path: 24-char hex node ID, or a path like
            "/sensors/camera/front/video.mp4" (use Prefix context or project=).
        to: Destination. For files: target path or directory. For folders:
            target directory (created if missing).
        project: "slug@namespace" — overrides Prefix context.

    Examples:
        # Single file by path
        with dl.Prefix(project="robotics@tom-tao", prefix="/session-042"):
            dl.download("sensors/camera/front/video.mp4", to="./out/")

        # Folder (recursive)
        with dl.Prefix(project="robotics@tom-tao", prefix="/session-042"):
            dl.download("sensors", to="./out/")

        # Episode (everything in session-042)
        dl.download("/session-042", project="robotics@tom-tao", to="./out/")
    """
    from pathlib import Path

    client = _get_client()
    node = _resolve_to_node(node_id_or_path, project)
    kind = node["kind"]

    # ── Single-file download ────────────────────────────────────────────
    if kind not in ("folder", "episode", "project"):
        filename = node["name"]
        if to is None:
            dest = Path(filename)
        else:
            dest = Path(to)
            if dest.is_dir() or str(to).endswith("/") or str(to).endswith(Path.sep):
                dest = dest / filename
        print(f"  downloading {filename}")
        _download_file(client, node["id"], dest)
        print(f"  done: {dest}")
        return dest

    # ── Folder/Episode/Project download (recursive) ─────────────────────
    result = client.get_node_descendants(node["id"], leaves_only=True)
    files = result["descendants"]
    root_prefix = result["rootPrefix"]  # e.g. ",robotics,session-042,sensors,"

    if not files:
        print(f"  no files under {kind} '{node['name']}'")
        return Path(to or ".")

    out_dir = Path(to or node["name"])
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"  downloading {len(files)} file(s) from {kind} '{node['name']}' → {out_dir}")

    for f in files:
        # Build relative path: strip root_prefix from full path
        full_prefix = f["path"]
        if full_prefix.startswith(root_prefix):
            rel_segments = full_prefix[len(root_prefix):].strip(",").split(",")
            rel_segments = [s for s in rel_segments if s]
        else:
            rel_segments = []

        rel_path = Path(*rel_segments) / f["name"] if rel_segments else Path(f["name"])
        dest = out_dir / rel_path
        _download_file(client, f["id"], dest)

    print(f"  done: {out_dir} ({len(files)} files)")
    return out_dir


def _parse_project_arg(project: str | None) -> tuple[str, str]:
    """Parse 'slug@namespace' (or just 'slug') into (project_slug, namespace)."""
    from .api.prefix import resolve_project
    resolved = resolve_project(project)
    if not resolved:
        raise ValueError("project is required. Set via dl.Prefix or project= arg.")
    parts = resolved.split("@")
    if len(parts) == 2:
        return parts[0], parts[1]
    project_slug = parts[0]
    me = _get_client().get_auth_me()
    namespace = me.get("namespace", {}).get("slug", "")
    return project_slug, namespace


def _resolve_path_to_id(path: str, namespace: str, project_slug: str,
                        episode_name: str | None) -> str:
    """Resolve a path to a node ID via /nodes/lookup."""
    from .api.prefix import resolve_path, _ctx_prefix

    ctx_prefix_str = _ctx_prefix.get().strip("/")
    if path.startswith("/"):
        asset_path = path
    else:
        resolved = resolve_path(path)
        if ctx_prefix_str:
            ctx_norm = "/" + ctx_prefix_str
            if resolved.startswith(ctx_norm + "/"):
                asset_path = resolved[len(ctx_norm):]
            elif resolved == ctx_norm:
                asset_path = "/"
            else:
                asset_path = resolved
        else:
            asset_path = resolved if resolved.startswith("/") else "/" + resolved

    node = _get_client().lookup_node(namespace, project_slug, asset_path,
                                     episode=episode_name)
    return node["id"]


def bindr(
    name: str,
    project: str | None = None,
    members: list[str] | None = None,
    bindrs: list[str] | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    create: bool = True,
) -> dict:
    """Get or create a bindr. Adds members and/or nested bindrs if provided.

    Args:
        name: Bindr name (unique within project)
        project: "slug@namespace" — overrides Prefix context
        members: List of paths (each resolves to a node id). Paths are
            absolute (`/episode/sensors`) or relative to Prefix context.
        bindrs: List of bindr names to include as nested bindr members
            (resolved within the same project). Server rejects cycles.
        description: Optional description (set on create)
        tags: Optional tag list (set on create)
        create: If True, create the bindr if missing. Default True.

    Examples:
        # File/folder members by path
        with dl.Prefix(project="robotics@alice", prefix="/run-100"):
            dl.bindr("training-set", members=["camera/front", "labels/yolo"])

        # Nest other bindrs
        dl.bindr("master-train",
                 project="robotics@alice",
                 bindrs=["train-batch-1", "train-batch-2"])
    """
    from .api.prefix import _ctx_prefix
    client = _get_client()
    project_slug, namespace = _parse_project_arg(project)

    # Episode name from Prefix context (used for path resolution)
    ctx_prefix_str = _ctx_prefix.get().strip("/")
    episode_name = ctx_prefix_str.split("/")[-1] if ctx_prefix_str else None

    # Build the unified list of refs to add: tagged {type, id} objects.
    refs: list[dict] = []
    if members:
        for m in members:
            node_id = _resolve_path_to_id(m, namespace, project_slug, episode_name)
            refs.append({"type": "node", "id": node_id})
    if bindrs:
        for bname in bindrs:
            b = client.get_bindr(namespace, project_slug, bname)
            refs.append({"type": "bindr", "id": b["id"]})

    # Try to fetch existing bindr
    import httpx as _httpx
    try:
        existing = client.get_bindr(namespace, project_slug, name)
        if refs:
            updated = client.add_bindr_members(namespace, project_slug, name, refs)
            existing["members"] = updated.get("members", existing.get("members"))
        return existing
    except _httpx.HTTPStatusError as e:
        if e.response.status_code != 404 or not create:
            raise

    # Create — pass refs as members (the server normalizes either form)
    return client.create_bindr(
        namespace, project_slug, name,
        members=refs or None, description=description, tags=tags,
    )


def _normalize_member_ref(m) -> dict:
    """Coerce a member entry into {type, id}. Legacy strings become node refs."""
    if isinstance(m, str):
        return {"type": "node", "id": m}
    if isinstance(m, dict) and "id" in m:
        return {"type": "bindr" if m.get("type") == "bindr" else "node", "id": m["id"]}
    return {"type": "node", "id": str(m)}


def _download_node_member(client, member_id: str, out_dir: "Path") -> int:
    """Download a single node member (file or folder). Returns file count."""
    from pathlib import Path

    result = client.get_node_descendants(member_id, leaves_only=True)
    root = result["root"]
    member_name = root["name"]

    if root["kind"] not in ("folder", "episode", "project"):
        # Single file member
        dest = out_dir / member_name
        _download_file(client, member_id, dest)
        print(f"  ✓ {member_name}")
        return 1

    # Container — recurse into out_dir / member_name /
    member_root = out_dir / member_name
    member_root.mkdir(parents=True, exist_ok=True)
    root_prefix = result["rootPrefix"]
    count = 0
    for f in result["descendants"]:
        full_prefix = f["path"]
        if full_prefix.startswith(root_prefix):
            rel = full_prefix[len(root_prefix):].strip(",").split(",")
            rel = [s for s in rel if s]
        else:
            rel = []
        dest = member_root.joinpath(*rel, f["name"]) if rel else member_root / f["name"]
        _download_file(client, f["id"], dest)
        count += 1
    print(f"  ✓ {member_name} ({count} file(s))")
    return count


def _download_bindr_recursive(client, bindr: dict, out_dir: "Path",
                              visited: set, depth: int = 0) -> int:
    """Walk a bindr's members; recurse into nested bindrs. Returns file count."""
    from pathlib import Path

    bindr_id = bindr["id"]
    if bindr_id in visited:
        print(f"  ↺ skipping '{bindr['name']}' (cycle)")
        return 0
    visited.add(bindr_id)

    members = [_normalize_member_ref(m) for m in (bindr.get("members") or [])]
    if not members:
        print(f"  bindr '{bindr['name']}' has no members")
        return 0

    indent = "  " * depth
    if depth > 0:
        print(f"{indent}── nested bindr '{bindr['name']}' ({len(members)} member(s))")

    count = 0
    for m in members:
        if m["type"] == "bindr":
            child = client.get_bindr_by_id(m["id"])
            count += _download_bindr_recursive(client, child, out_dir, visited, depth + 1)
        else:
            count += _download_node_member(client, m["id"], out_dir)
    return count


def download_bindr(
    name: str,
    project: str | None = None,
    to: str = ".",
) -> "Path":
    """Download every node referenced by a bindr (recursing into nested
    bindrs). Each leaf member becomes a top-level entry under `to`,
    preserving the relative tree for folder members.

    Cycles in the bindr graph are detected and broken — already-visited
    bindrs are skipped with a warning.

    Examples:
        dl.download_bindr("training-set", project="robotics@alice", to="./train/")
    """
    from pathlib import Path

    client = _get_client()
    project_slug, namespace = _parse_project_arg(project)

    b = client.get_bindr(namespace, project_slug, name)
    out_dir = Path(to)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"  downloading bindr '{name}' → {out_dir}")
    total = _download_bindr_recursive(client, b, out_dir, visited=set())
    print(f"  done: {out_dir} ({total} file(s))")
    return out_dir


def text_track(
    prefix: str | None = None,
    project: str | None = None,
    path: str | None = None,
) -> TextTrack:
    """Create a TextTrack for buffering and uploading text entries.

    Examples:
        track = dl.text_track(prefix="/2026/04/run-042/captions/llava", project="robotics@alice")
    """
    return TextTrack(prefix=prefix, project=project, path=path)


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
    "download",
    "bindr",
    "download_bindr",
    "text_track",
    "vec_index",
]
