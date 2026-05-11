"""
Upload command.

Usage:
    dreamlake upload <file> --episode [namespace@]project[:episode] --to <path>

File type is auto-detected from extension. Use --type to override.

Categories: audio, video, track, text-track, label-track, image
"""

import sys
import json
import hashlib
import math
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from params_proto import proto

from dreamlake.cli._args import args_to_dict
from dreamlake.cli._config import ServerConfig
from dreamlake.cli._target import parse_target, format_target

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
CYAN = "\033[36m"

CHUNK_SIZE = 10 * 1024 * 1024  # 10 MB per part (S3 minimum is 5 MB except last)
MAX_WORKERS = 4                  # parallel part uploads

EXTENSION_TO_CATEGORY = {
    ".wav": "audio", ".mp3": "audio", ".aac": "audio", ".opus": "audio",
    ".mp4": "video", ".mkv": "video",
    ".npy": "track", ".msgpack": "track", ".csv": "track",
    ".srt": "text-track", ".vtt": "text-track",
    ".jsonl": "label-track",
    ".jpg": "image", ".jpeg": "image", ".png": "image", ".gif": "image",
    ".webp": "image", ".bmp": "image", ".tiff": "image", ".tif": "image",
}

CATEGORIES = {"audio", "video", "track", "text-track", "label-track", "image"}

# BSS route prefix per category
BSS_ROUTE_MAP = {
    "video": "videos", "audio": "audio", "image": "image",
    "text-track": "text-tracks", "label-track": "labels",
}

# MIME type per category
MIME_MAP = {
    ".wav": "audio/wav", ".mp3": "audio/mpeg", ".aac": "audio/aac", ".opus": "audio/ogg",
    ".mp4": "video/mp4", ".mkv": "video/x-matroska",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".tiff": "image/tiff", ".tif": "image/tiff",
    ".vtt": "text/vtt", ".srt": "text/plain",
    ".jsonl": "application/x-jsonlines",
}


@proto.prefix
class UploadConfig:
    episode: str | None = None   # [namespace@]project[:episode]
    to: str | None = None     # destination path (within episode)
    type: str | None = None   # category override
    yes: bool = False         # skip confirmation prompt (for folder upload)
    collection: str | None = None  # comma-separated collection names (auto-created)


def print_help():
    print(f"""
{BOLD}dreamlake upload{RESET} - Upload a file to DreamLake

{BOLD}Usage:{RESET}
    dreamlake upload <file> --episode [namespace@]project[:episode] --to <path>

{BOLD}Options:{RESET}
    --episode    Episode scope: [namespace@]project[:episode]
    --to      Destination path within the episode
    --type    Override auto-detected file type

{BOLD}Auto-detected types:{RESET}
    .wav .mp3 .aac .opus        → audio
    .mp4 .mkv                   → video
    .jpg .jpeg .png .gif .webp  → image
    .npy .msgpack .csv          → track
    .srt .vtt                   → text-track
    .jsonl                      → label-track (default; use --type text-track to override)

{BOLD}Examples:{RESET}
    dreamlake upload ./mic.wav --episode alice@robotics:2026/q1/run-042 --to /microphone/front
    dreamlake upload ./video.mp4 --episode robotics:experiments/run-042 --to /camera/front
    dreamlake upload ./labels.jsonl --episode alice@robotics:run-042 --to /detections/yolo
    dreamlake upload ./transcript.jsonl --episode alice@robotics:run-042 --to /subtitles/en --type text-track
""".strip())


def detect_category(file_path: Path, type_override: str | None) -> str | None:
    if type_override:
        return type_override
    return EXTENSION_TO_CATEGORY.get(file_path.suffix.lower())


def _add_to_collections(collection_names: list[str], node_ids: list[str], t, token: str) -> None:
    """Add node IDs to collections (auto-created if they don't exist)."""
    import httpx

    if not collection_names or not node_ids:
        return

    remote = ServerConfig.remote
    headers = {"Authorization": f"Bearer {token}"}

    for name in collection_names:
        name = name.strip()
        if not name:
            continue
        try:
            with httpx.Client(timeout=30, headers=headers) as client:
                r = client.post(
                    f"{remote}/namespaces/{t.namespace}/projects/{t.project}/collections/{name}/members",
                    json={"add": node_ids},
                )
                if r.status_code == 404:
                    r = client.post(
                        f"{remote}/namespaces/{t.namespace}/projects/{t.project}/collections",
                        json={"name": name, "members": node_ids},
                    )
                    r.raise_for_status()
                    print(f"  {DIM}collection:{RESET}  created '{name}' ({len(node_ids)} files)")
                elif r.status_code == 200:
                    data = r.json()
                    print(f"  {DIM}collection:{RESET}  added to '{name}' (total: {data.get('total', '?')} files)")
                else:
                    print(f"  {DIM}collection:{RESET}  '{name}' failed ({r.status_code})", file=sys.stderr)
        except Exception as e:
            print(f"  {DIM}collection:{RESET}  '{name}' error: {e}", file=sys.stderr)


# Shared state to capture nodeId from the last upload
_last_node_id: str | None = None


# ── Upload state (pause/resume) ─────────────────────────────────────────────

def _state_path(raw_hash: str) -> Path:
    state_dir = Path.home() / ".dreamlake" / "uploads"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / f"{raw_hash}.json"


def _save_state(raw_hash: str, upload_id: str, key: str, total_parts: int,
                completed_parts: list, lock) -> None:
    with lock:
        _state_path(raw_hash).write_text(json.dumps({
            "uploadId": upload_id,
            "key": key,
            "totalParts": total_parts,
            "completedParts": completed_parts,
        }))


def _load_state(raw_hash: str) -> dict | None:
    p = _state_path(raw_hash)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return None
    return None


def _clear_state(raw_hash: str) -> None:
    p = _state_path(raw_hash)
    if p.exists():
        p.unlink()


# ── Unified upload function ──────────────────────────────────────────────────

def _upload_file(file_path: Path, t, path: str, category: str, token: str) -> int:
    """Upload any file type to BSS via S3 multipart, then register via POST /nodes."""
    import httpx

    global _last_node_id
    _last_node_id = None

    bss_url = ServerConfig.bss_url
    remote = ServerConfig.remote
    headers = {"Authorization": f"Bearer {token}"}

    bss_route = BSS_ROUTE_MAP.get(category)
    if not bss_route:
        print(f"(upload for '{category}' not yet implemented)", file=sys.stderr)
        return 1

    content_type = MIME_MAP.get(file_path.suffix.lower(), "application/octet-stream")

    # Phase 1: hash + compute parts
    content = file_path.read_bytes()
    raw_hash = hashlib.sha256(content).hexdigest()[:16]
    file_size = len(content)
    total_parts = max(1, math.ceil(file_size / CHUNK_SIZE))

    size_str = f"{file_size / 1024 / 1024:.1f} MB" if file_size > 1024 * 1024 else f"{file_size / 1024:.1f} KB"
    print(f"  {DIM}size:{RESET}   {size_str}")
    print(f"  {DIM}parts:{RESET}  {total_parts} x {CHUNK_SIZE // 1024 // 1024} MB")

    state_lock = threading.Lock()

    # Check for existing upload state (pause/resume)
    upload_id: str | None = None
    key: str | None = None
    completed_parts: list[dict] = []
    completed_map: dict[int, dict] = {}

    state = _load_state(raw_hash)
    if state:
        with httpx.Client(timeout=30, headers=headers) as client:
            r = client.get(f"{bss_url}/{bss_route}/upload/multipart/parts-done", params={
                "uploadId": state["uploadId"],
                "key": state["key"],
            })
        if r.status_code == 200:
            data = r.json()
            if not data.get("expired"):
                upload_id = state["uploadId"]
                key = state["key"]
                for part in data.get("parts", []):
                    completed_map[part["partNumber"]] = part
                completed_parts = list(completed_map.values())
                remaining = total_parts - len(completed_parts)
                print(f"  {DIM}resuming:{RESET} {len(completed_parts)}/{total_parts} parts done, {remaining} remaining")

    # Phase 2: init multipart upload (only if not resuming)
    if not upload_id:
        with httpx.Client(timeout=30, headers=headers) as client:
            r = client.post(f"{bss_url}/{bss_route}/upload/multipart/init", json={
                "owner": t.namespace,
                "project": t.project,
                "hash": raw_hash,
                "contentType": content_type,
            })
            r.raise_for_status()
            init_data = r.json()
            upload_id = init_data["uploadId"]
            key = init_data["key"]
        _save_state(raw_hash, upload_id, key, total_parts, [], state_lock)

    # Phase 3: get presigned URLs for remaining parts
    remaining_parts = [n for n in range(1, total_parts + 1) if n not in completed_map]
    parts_data: dict[str, str] = {}
    if remaining_parts:
        with httpx.Client(timeout=30, headers=headers) as client:
            r = client.post(f"{bss_url}/{bss_route}/upload/multipart/parts", json={
                "uploadId": upload_id,
                "key": key,
                "partNumbers": remaining_parts,
            })
            r.raise_for_status()
            parts_data = r.json()["parts"]

    # Phase 4: parallel chunk upload
    failed = False

    def upload_part(part_number: int) -> dict:
        start = (part_number - 1) * CHUNK_SIZE
        chunk = content[start: start + CHUNK_SIZE]
        url = parts_data[str(part_number)]
        with httpx.Client(timeout=300) as s3_client:
            resp = s3_client.put(url, content=chunk, headers={"Content-Type": content_type})
            resp.raise_for_status()
            etag = resp.headers.get("ETag", "").strip('"')
            return {"partNumber": part_number, "etag": etag}

    if remaining_parts:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(upload_part, n): n for n in remaining_parts}
            for future in as_completed(futures):
                part_n = futures[future]
                try:
                    result = future.result()
                    completed_parts.append(result)
                    completed_map[result["partNumber"]] = result
                    _save_state(raw_hash, upload_id, key, total_parts, completed_parts, state_lock)
                    print(f"  {DIM}part {part_n}/{total_parts}{RESET} uploaded")
                except Exception as e:
                    print(f"{RED}error:{RESET} part {part_n} failed: {e}", file=sys.stderr)
                    failed = True
                    for f in futures:
                        f.cancel()
                    break

    # Phase 5: complete or abort
    if failed:
        print(f"{RED}error:{RESET} upload paused — re-run to resume", file=sys.stderr)
        return 1

    completed_parts = sorted(completed_map.values(), key=lambda p: p["partNumber"])

    with httpx.Client(timeout=60, headers=headers) as client:
        r = client.post(f"{bss_url}/{bss_route}/upload/multipart/complete", json={
            "uploadId": upload_id,
            "key": key,
            "parts": completed_parts,
        })
        r.raise_for_status()

    _clear_state(raw_hash)

    # Phase 6: Register in BSS
    bss_body: dict = {
        "name": f"/{path}/{file_path.name}",
        "owner": t.namespace,
        "project": t.project,
        "stagingHash": raw_hash,
    }
    if category == "text-track":
        ext_to_format = {".vtt": "vtt", ".srt": "srt", ".jsonl": "jsonl"}
        bss_body["format"] = ext_to_format.get(file_path.suffix.lower(), "jsonl")

    with httpx.Client(timeout=30, headers=headers) as client:
        r = client.post(f"{bss_url}/{bss_route}", json=bss_body)
        r.raise_for_status()
        bss_result = r.json()

    bss_id = bss_result.get("id")

    # Phase 7: Register in dreamlake-server (POST /nodes)
    node_body: dict = {
        "namespace": t.namespace,
        "kind": category,
        "name": f"/{path}/{file_path.name}",
        "project": t.project,
        "metadata": {
            "bssId": bss_id,
            "hash": raw_hash,
            "size": file_size,
            "contentType": content_type,
        },
    }
    if t.episode:
        node_body["episode"] = t.episode

    with httpx.Client(timeout=30, headers=headers) as client:
        r = client.post(f"{remote}/nodes", json=node_body)
        r.raise_for_status()
        dl_result = r.json()
        _last_node_id = dl_result.get("id")

    print(f"{GREEN}✓ Uploaded:{RESET} /{path}/{file_path.name}")
    print(f"  {DIM}bss id:{RESET}       {bss_id}")
    print(f"  {DIM}node id:{RESET}      {_last_node_id}")
    return 0


def _upload_single_file(file_path: Path, t, path: str, token: str, category: str) -> int:
    """Upload a single file. Returns 0 on success, 1 on failure."""
    global _last_node_id
    _last_node_id = None
    try:
        return _upload_file(file_path, t, path, category, token)
    except Exception as e:
        print(f"{RED}error:{RESET} {e}", file=sys.stderr)
        return 1


# ── Folder manifest helpers ───────────────────────────────────────────────────

def _folder_manifest_hash(dir_path: Path, episode: str, to: str) -> str:
    key = f"{dir_path.resolve()}:{episode}:{to}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _folder_manifest_path(dir_path: Path, episode: str, to: str) -> Path:
    state_dir = Path.home() / ".dreamlake" / "uploads"
    state_dir.mkdir(parents=True, exist_ok=True)
    h = _folder_manifest_hash(dir_path, episode, to)
    return state_dir / f"folder-{h}.json"


def _load_folder_manifest(path: Path) -> dict | None:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return None
    return None


def _save_folder_manifest(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, indent=2))


# ── Folder upload ─────────────────────────────────────────────────────────────

def _upload_folder(dir_path: Path) -> int:
    """Upload all files in a directory (flat, no recursion)."""
    from rich.console import Console
    from rich.table import Table
    from rich.progress import Progress, BarColumn, TextColumn, MofNCompleteColumn
    from rich.prompt import Confirm

    console = Console()

    if not UploadConfig.episode:
        console.print("[red]error:[/red] --episode is required", style="bold")
        return 1
    if not UploadConfig.to:
        console.print("[red]error:[/red] --to is required", style="bold")
        return 1

    try:
        t = parse_target(UploadConfig.episode)
    except ValueError as e:
        console.print(f"[red]error:[/red] {e}")
        return 1

    to_path = UploadConfig.to.lstrip("/")

    if not t.namespace:
        t.namespace = ServerConfig.resolve_namespace()
        if not t.namespace:
            console.print("[red]error:[/red] namespace not specified. run 'dreamlake login'")
            return 1

    token = ServerConfig.resolve_token()
    if not token:
        console.print("[red]error:[/red] not authenticated. run 'dreamlake login' first")
        return 1

    # Step 1: Scan directory
    console.print(f"\nScanning [cyan]{dir_path}[/cyan] ...")

    all_files = sorted([f for f in dir_path.iterdir() if f.is_file()])
    classified: dict[str, list[Path]] = {}
    skipped: list[str] = []

    for f in all_files:
        cat = detect_category(f, None)
        if cat and cat in CATEGORIES:
            classified.setdefault(cat, []).append(f)
        else:
            skipped.append(f.name)

    uploadable = {name: cat for cat, files in classified.items() for f in files for name, cat in [(f.name, cat)]}
    total_uploadable = sum(len(files) for files in classified.values())

    # Load / create manifest
    manifest_path = _folder_manifest_path(dir_path, UploadConfig.episode, UploadConfig.to)
    manifest = _load_folder_manifest(manifest_path) or {
        "sourceDir": str(dir_path.resolve()),
        "target": UploadConfig.episode,
        "to": UploadConfig.to,
        "files": {},
        "skipped": [],
    }

    for fname, cat in uploadable.items():
        if fname not in manifest["files"]:
            manifest["files"][fname] = {"status": "pending", "category": cat}
    manifest["skipped"] = skipped

    done_count = sum(1 for f in manifest["files"].values() if f["status"] == "done")
    failed_count = sum(1 for f in manifest["files"].values() if f["status"] == "failed")
    pending_count = sum(1 for f in manifest["files"].values() if f["status"] in ("pending", "failed"))

    # Step 2: Display summary
    console.print()
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="dim", width=14)
    table.add_column()
    table.add_row("Total", f"{len(all_files)} files")
    for cat in sorted(classified.keys()):
        table.add_row(cat.replace("-", " ").title(), str(len(classified[cat])))
    if skipped:
        skip_display = ", ".join(skipped[:10])
        if len(skipped) > 10:
            skip_display += f" ...and {len(skipped) - 10} more"
        table.add_row("Skipped", f"{len(skipped)} — unknown extension:")
        console.print(table)
        console.print(f"    [dim]{skip_display}[/dim]")
    else:
        console.print(table)

    if done_count > 0:
        console.print(f"\n  [green]Resuming[/green] ({done_count}/{total_uploadable} done, {failed_count} failed, {pending_count} pending)")

    if total_uploadable == 0:
        console.print("\n  No files to upload.")
        return 0

    console.print()
    if not UploadConfig.yes:
        if not Confirm.ask("  Continue?", default=True):
            return 0

    _save_folder_manifest(manifest_path, manifest)

    # Step 3: Upload with progress
    to_upload = [
        (fname, info["category"])
        for fname, info in manifest["files"].items()
        if info["status"] in ("pending", "failed")
    ]

    uploaded = 0
    failed = 0

    with Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("[dim]{task.fields[current]}[/dim]"),
        console=console,
    ) as progress:
        task = progress.add_task("Uploading", total=len(to_upload), current="")

        for fname, cat in to_upload:
            file_path = dir_path / fname
            if not file_path.exists():
                manifest["files"][fname]["status"] = "failed"
                manifest["files"][fname]["error"] = "file not found"
                failed += 1
                progress.advance(task)
                continue

            size_mb = file_path.stat().st_size / (1024 * 1024)
            progress.update(task, current=f"{fname} ({cat}, {size_mb:.1f} MB)")

            result = _upload_single_file(file_path, t, to_path, token, cat)

            if result == 0:
                manifest["files"][fname]["status"] = "done"
                if _last_node_id:
                    manifest["files"][fname]["nodeId"] = _last_node_id
                uploaded += 1
            else:
                manifest["files"][fname]["status"] = "failed"
                manifest["files"][fname]["error"] = "upload failed"
                failed += 1

            _save_folder_manifest(manifest_path, manifest)
            progress.advance(task)

    # Step 4: Final summary
    console.print()
    total_done = sum(1 for f in manifest["files"].values() if f["status"] == "done")
    total_failed = sum(1 for f in manifest["files"].values() if f["status"] == "failed")

    if total_failed == 0:
        console.print(f"[green]✓ {total_done}/{total_uploadable} uploaded[/green], {len(skipped)} skipped")
    else:
        console.print(f"[yellow]✓ {total_done}/{total_uploadable} uploaded[/yellow], {len(skipped)} skipped")
        console.print(f"  [red]Failed: {total_failed}[/red]")
        for fname, info in manifest["files"].items():
            if info["status"] == "failed":
                console.print(f"    {fname}: {info.get('error', 'unknown')}")
        console.print("  Re-run to retry failed files.")

    # Add to collections if specified
    if UploadConfig.collection:
        collection_names = [n.strip() for n in UploadConfig.collection.split(',') if n.strip()]
        node_ids = [info.get("nodeId") for info in manifest["files"].values() if info.get("nodeId")]
        if node_ids and collection_names:
            _add_to_collections(collection_names, node_ids, t, token)

    # Clean up manifest if all done
    if total_failed == 0 and manifest_path.exists():
        manifest_path.unlink()

    return 0 if total_failed == 0 else 1


def cmd_upload(file: str) -> int:
    if not UploadConfig.episode:
        print(f"{RED}error:{RESET} --episode is required", file=sys.stderr)
        return 1

    if not UploadConfig.to:
        print(f"{RED}error:{RESET} --to is required", file=sys.stderr)
        return 1

    file_path = Path(file)
    if not file_path.exists():
        print(f"{RED}error:{RESET} file not found: {file}", file=sys.stderr)
        return 1

    # Directory → folder upload mode
    if file_path.is_dir():
        return _upload_folder(file_path)

    # Single file upload
    category = detect_category(file_path, UploadConfig.type)
    if not category:
        print(
            f"{RED}error:{RESET} cannot detect file type for '{file_path.suffix}'. use --type to specify.",
            file=sys.stderr,
        )
        return 1

    if category not in CATEGORIES:
        print(f"{RED}error:{RESET} unknown type '{category}'. valid: {', '.join(sorted(CATEGORIES))}", file=sys.stderr)
        return 1

    try:
        t = parse_target(UploadConfig.episode)
    except ValueError as e:
        print(f"{RED}error:{RESET} {e}", file=sys.stderr)
        return 1

    path = UploadConfig.to.lstrip("/")

    if not t.namespace:
        t.namespace = ServerConfig.resolve_namespace()
        if not t.namespace:
            print(
                f"{RED}error:{RESET} namespace not specified and no authenticated user found. run 'dreamlake login'",
                file=sys.stderr,
            )
            return 1

    token = ServerConfig.resolve_token()
    if not token:
        print(f"{RED}error:{RESET} not authenticated. run 'dreamlake login' first", file=sys.stderr)
        return 1

    print(f"Uploading {CYAN}{file_path.name}{RESET} ({category})")
    print(f"  {DIM}episode:{RESET} {format_target(t)}")
    print(f"  {DIM}path:{RESET}    /{path}")

    result = _upload_single_file(file_path, t, path, token, category)

    # Add to collections if specified
    if result == 0 and UploadConfig.collection and _last_node_id:
        collection_names = [n.strip() for n in UploadConfig.collection.split(',') if n.strip()]
        _add_to_collections(collection_names, [_last_node_id], t, token)

    return result


def main(args: list) -> int:
    if not args or args[0] in ("-h", "--help", "help"):
        print_help()
        return 0 if args else 1

    # Extract positional file arg (first non-flag arg)
    file = None
    remaining = []
    i = 0
    while i < len(args):
        if args[i].startswith("-"):
            remaining.append(args[i])
            if i + 1 < len(args) and not args[i + 1].startswith("-"):
                remaining.append(args[i + 1])
                i += 2
            else:
                i += 1
        else:
            if file is None:
                file = args[i]
            i += 1

    if not file:
        print(f"{RED}error:{RESET} file path is required", file=sys.stderr)
        print_help()
        return 1

    UploadConfig._update(args_to_dict(remaining))
    return cmd_upload(file)
