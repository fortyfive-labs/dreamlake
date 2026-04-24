"""
Upload command.

Usage:
    dreamlake upload <file> --episode [nameproject@]space[:episode] --to <path>

File type is auto-detected from extension. Use --type to override.

Categories: audio, video, track, text-track, label-track
"""

import sys
import json
import hashlib
from pathlib import Path

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


@proto.prefix
class UploadConfig:
    episode: str | None = None   # [nameproject@]space[:episode]
    to: str | None = None     # destination path (within episode)
    type: str | None = None   # category override
    yes: bool = False         # skip confirmation prompt (for folder upload)
    collection: str | None = None  # comma-separated collection names (auto-created)


def print_help():
    print(f"""
{BOLD}dreamlake upload{RESET} - Upload a file to DreamLake

{BOLD}Usage:{RESET}
    dreamlake upload <file> --episode [nameproject@]space[:episode] --to <path>

{BOLD}Options:{RESET}
    --episode    Episode scope: [nameproject@]space[:episode]
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
                # Try to add members to existing collection
                r = client.post(
                    f"{remote}/namespaces/{t.namespace}/projects/{t.project}/collections/{name}/members",
                    json={"add": node_ids},
                )
                if r.status_code == 404:
                    # Collection doesn't exist — create it with members
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

def _upload_single_file(file_path: Path, t, path: str, token: str, category: str) -> int:
    """Upload a single file. Returns 0 on success, 1 on failure. Sets _last_node_id."""
    global _last_node_id
    _last_node_id = None
    try:
        if category == "video":
            return _upload_video(file_path, t, path, token)
        else:
            return _upload_asset(file_path, t, path, category, token)
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

    # ── Step 1: Scan directory ────────────────────────────────────────────
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

    # ── Load / create manifest ────────────────────────────────────────────
    manifest_path = _folder_manifest_path(dir_path, UploadConfig.episode, UploadConfig.to)
    manifest = _load_folder_manifest(manifest_path) or {
        "sourceDir": str(dir_path.resolve()),
        "target": UploadConfig.episode,
        "to": UploadConfig.to,
        "files": {},
        "skipped": [],
    }

    # Merge: add new files, keep existing status
    for fname, cat in uploadable.items():
        if fname not in manifest["files"]:
            manifest["files"][fname] = {"status": "pending", "category": cat}
    manifest["skipped"] = skipped

    done_count = sum(1 for f in manifest["files"].values() if f["status"] == "done")
    failed_count = sum(1 for f in manifest["files"].values() if f["status"] == "failed")
    pending_count = sum(1 for f in manifest["files"].values() if f["status"] in ("pending", "failed"))

    # ── Step 2: Display summary ───────────────────────────────────────────
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

    # ── Step 3: Upload with progress ──────────────────────────────────────
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

    # ── Step 4: Final summary ─────────────────────────────────────────────
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

    # Resolve namespace from current user if omitted
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


def _state_path(raw_hash: str) -> Path:
    state_dir = Path.home() / ".dreamlake" / "uploads"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / f"{raw_hash}.json"


def _save_state(raw_hash: str, upload_id: str, key: str, total_parts: int,
                completed_parts: list, lock) -> None:
    import json
    with lock:
        _state_path(raw_hash).write_text(json.dumps({
            "uploadId": upload_id,
            "key": key,
            "totalParts": total_parts,
            "completedParts": completed_parts,
        }))


def _load_state(raw_hash: str) -> dict | None:
    import json
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


def _upload_video(file_path: Path, t, path: str, token: str) -> int:
    """Upload video to BSS via S3 multipart upload, then register in dreamlake-server."""
    import hashlib
    import json
    import math
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import httpx

    bss_url = ServerConfig.bss_url
    remote = ServerConfig.remote
    headers = {"Authorization": f"Bearer {token}"}

    # Phase 1: hash + compute parts
    content = file_path.read_bytes()
    raw_hash = hashlib.sha256(content).hexdigest()[:16]
    file_size = len(content)
    total_parts = max(1, math.ceil(file_size / CHUNK_SIZE))

    print(f"  {DIM}size:{RESET}   {file_size / 1024 / 1024:.1f} MB")
    print(f"  {DIM}parts:{RESET}  {total_parts} × {CHUNK_SIZE // 1024 // 1024} MB")

    state_lock = threading.Lock()

    # Check for existing upload state (pause/resume)
    upload_id: str | None = None
    key: str | None = None
    completed_parts: list[dict] = []
    completed_map: dict[int, dict] = {}  # partNumber → {partNumber, etag}

    state = _load_state(raw_hash)
    if state:
        # Verify the upload is still alive in S3
        with httpx.Client(timeout=30, headers=headers) as client:
            r = client.get(f"{bss_url}/videos/upload/multipart/parts-done", params={
                "uploadId": state["uploadId"],
                "key": state["key"],
            })
        if r.status_code == 200:
            data = r.json()
            if not data.get("expired"):
                upload_id = state["uploadId"]
                key = state["key"]
                for part in data["parts"]:
                    completed_map[part["partNumber"]] = part
                completed_parts = list(completed_map.values())
                remaining = total_parts - len(completed_parts)
                print(f"  {DIM}resuming:{RESET} {len(completed_parts)}/{total_parts} parts already uploaded, uploading {remaining} remaining")

    # Phase 2: init multipart upload (only if not resuming)
    if not upload_id:
        with httpx.Client(timeout=30, headers=headers) as client:
            r = client.post(f"{bss_url}/videos/upload/multipart/init", json={
                "owner": t.namespace,
                "project": t.project,
                "hash": raw_hash,
                "contentType": "video/mp4",
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
            r = client.post(f"{bss_url}/videos/upload/multipart/parts", json={
                "uploadId": upload_id,
                "key": key,
                "partNumbers": remaining_parts,
            })
            r.raise_for_status()
            parts_data = r.json()["parts"]  # {"1": url, "2": url, ...}

    # Phase 4: parallel chunk upload
    failed = False

    def upload_part(part_number: int) -> dict:
        start = (part_number - 1) * CHUNK_SIZE
        chunk = content[start: start + CHUNK_SIZE]
        url = parts_data[str(part_number)]
        with httpx.Client(timeout=300) as s3_client:
            resp = s3_client.put(url, content=chunk, headers={"Content-Type": "video/mp4"})
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

    with httpx.Client(timeout=60, headers=headers) as client:
        r = client.post(f"{bss_url}/videos/upload/multipart/complete", json={
            "uploadId": upload_id,
            "key": key,
            "parts": completed_parts,
        })
        r.raise_for_status()

    _clear_state(raw_hash)

    # Register in BSS (creates DB record + S3 meta)
    with httpx.Client(timeout=30, headers=headers) as client:
        r = client.post(f"{bss_url}/videos", json={
            "name": f"/{path}/{file_path.name}",
            "owner": t.namespace,
            "project": t.project,
            "episodeId": t.episode,
            "stagingHash": raw_hash,
        })
        r.raise_for_status()
        bss_video = r.json()

    # Register in dreamlake-server (links asset to namespace/space/episode)
    with httpx.Client(timeout=30, headers=headers) as client:
        r = client.post(f"{remote}/assets/video", json={
            "namespace": t.namespace,
            "project": t.project,
            "episodeName": t.episode,
            "name": f"/{path}/{file_path.name}",
            "bssVideoId": bss_video.get("id"),
            "fps": 30,
            "lens": "pinhole",
        })
        r.raise_for_status()
        dl_asset = r.json()
        global _last_node_id; _last_node_id = dl_asset.get("nodeId")

    # Trigger HLS splitting via presigned URL from dreamlake-server
    lambda_url = dl_asset.get("lambdaUrl")
    if lambda_url:
        with httpx.Client(timeout=30) as client:
            r = client.post(lambda_url)
            if r.status_code == 202:
                print(f"  {DIM}splitting:{RESET}    queued")
            else:
                print(f"  {DIM}splitting:{RESET}    skipped ({r.status_code})", file=sys.stderr)

    print(f"{GREEN}✓ Uploaded:{RESET} /{path}")
    print(f"  {DIM}bss id:{RESET}       {bss_video.get('id')}")
    print(f"  {DIM}dreamlake id:{RESET} {dl_asset.get('id')}")
    return 0


def _upload_asset(file_path: Path, t, path: str, category: str, token: str) -> int:
    """Upload non-video assets."""
    if category == "audio":
        return _upload_audio(file_path, t, path, token)
    if category == "label-track":
        return _upload_label_track(file_path, t, path, token)
    if category == "text-track":
        return _upload_text_track(file_path, t, path, token)
    if category == "image":
        return _upload_image(file_path, t, path, token)

    print(f"(upload for '{category}' not yet implemented)", file=sys.stderr)
    return 1


def _upload_audio(file_path: Path, t, path: str, token: str) -> int:
    """Upload audio to BSS via S3 multipart upload, then register in dreamlake-server."""
    import hashlib
    import math
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import httpx

    bss_url = ServerConfig.bss_url
    remote = ServerConfig.remote
    headers = {"Authorization": f"Bearer {token}"}

    # Detect content type from extension
    ext_to_mime = {
        ".wav": "audio/wav", ".mp3": "audio/mpeg", ".aac": "audio/aac", ".opus": "audio/ogg",
    }
    content_type = ext_to_mime.get(file_path.suffix.lower(), "audio/wav")

    # Phase 1: hash + split
    content = file_path.read_bytes()
    raw_hash = hashlib.sha256(content).hexdigest()[:16]
    file_size = len(content)
    total_parts = max(1, math.ceil(file_size / CHUNK_SIZE))

    print(f"  {DIM}size:{RESET}   {file_size / 1024 / 1024:.1f} MB")
    print(f"  {DIM}parts:{RESET}  {total_parts} × {CHUNK_SIZE // 1024 // 1024} MB")

    state_lock = threading.Lock()
    upload_id: str | None = None
    key: str | None = None
    completed_parts: list[dict] = []
    completed_map: dict[int, dict] = {}

    state = _load_state(raw_hash)
    if state:
        with httpx.Client(timeout=30, headers=headers) as client:
            r = client.get(f"{bss_url}/audio/upload/multipart/parts-done", params={
                "uploadId": state["uploadId"],
                "key": state["key"],
            })
        if r.status_code == 200:
            data = r.json()
            if not data.get("expired"):
                upload_id = state["uploadId"]
                key = state["key"]
                for part in data["parts"]:
                    completed_map[part["partNumber"]] = part
                completed_parts = list(completed_map.values())
                remaining = total_parts - len(completed_parts)
                print(f"  {DIM}resuming:{RESET} {len(completed_parts)}/{total_parts} parts done, {remaining} remaining")

    # Phase 2: init
    if not upload_id:
        with httpx.Client(timeout=30, headers=headers) as client:
            r = client.post(f"{bss_url}/audio/upload/multipart/init", json={
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

    # Phase 3: get presigned URLs
    remaining_parts = [n for n in range(1, total_parts + 1) if n not in completed_map]
    parts_data: dict[str, str] = {}
    if remaining_parts:
        with httpx.Client(timeout=30, headers=headers) as client:
            r = client.post(f"{bss_url}/audio/upload/multipart/parts", json={
                "uploadId": upload_id,
                "key": key,
                "partNumbers": remaining_parts,
            })
            r.raise_for_status()
            parts_data = r.json()["parts"]

    # Phase 4: parallel upload
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

    with httpx.Client(timeout=60, headers=headers) as client:
        r = client.post(f"{bss_url}/audio/upload/multipart/complete", json={
            "uploadId": upload_id,
            "key": key,
            "parts": completed_parts,
        })
        r.raise_for_status()

    _clear_state(raw_hash)

    # Register in BSS
    with httpx.Client(timeout=30, headers=headers) as client:
        r = client.post(f"{bss_url}/audio", json={
            "name": f"/{path}/{file_path.name}",
            "owner": t.namespace,
            "project": t.project,
            "episodeId": t.episode,
            "stagingHash": raw_hash,
        })
        r.raise_for_status()
        bss_audio = r.json()

    bss_audio_id = bss_audio.get("id")

    # Register in dreamlake-server (gets presigned Lambda URL back)
    with httpx.Client(timeout=30, headers=headers) as client:
        r = client.post(f"{remote}/assets/audio", json={
            "namespace": t.namespace,
            "project": t.project,
            "episodeName": t.episode,
            "name": f"/{path}/{file_path.name}",
            "bssAudioId": bss_audio_id,
        })
        r.raise_for_status()
        dl_asset = r.json()
        global _last_node_id; _last_node_id = dl_asset.get("nodeId")

    # Trigger Lambda processing via presigned URL
    lambda_url = dl_asset.get("lambdaUrl")
    if lambda_url:
        with httpx.Client(timeout=30) as client:
            r = client.post(lambda_url)
            if r.status_code == 202:
                print(f"  {DIM}processing:{RESET}   queued")
            else:
                print(f"  {DIM}processing:{RESET}   skipped ({r.status_code})", file=sys.stderr)

    print(f"{GREEN}✓ Uploaded:{RESET} /{path}")
    print(f"  {DIM}bss id:{RESET}       {bss_audio_id}")
    print(f"  {DIM}dreamlake id:{RESET} {dl_asset.get('id')}")
    return 0


def _upload_label_track(file_path: Path, t, path: str, token: str) -> int:
    """Upload a JSONL label track to BSS via S3 multipart, then register in dreamlake-server."""
    import hashlib
    import math
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import httpx

    bss_url = ServerConfig.bss_url
    remote = ServerConfig.remote
    headers = {"Authorization": f"Bearer {token}"}

    # Phase 1: hash + split
    content = file_path.read_bytes()
    raw_hash = hashlib.sha256(content).hexdigest()[:16]
    file_size = len(content)
    total_parts = max(1, math.ceil(file_size / CHUNK_SIZE))

    print(f"  {DIM}size:{RESET}   {file_size / 1024:.1f} KB")
    print(f"  {DIM}parts:{RESET}  {total_parts} × {CHUNK_SIZE // 1024 // 1024} MB")

    state_lock = threading.Lock()
    upload_id: str | None = None
    key: str | None = None
    completed_parts: list[dict] = []
    completed_map: dict[int, dict] = {}

    state = _load_state(raw_hash)
    if state:
        with httpx.Client(timeout=30, headers=headers) as client:
            r = client.get(f"{bss_url}/labels/upload/multipart/parts-done", params={
                "uploadId": state["uploadId"],
                "key": state["key"],
            })
        if r.status_code == 200:
            data = r.json()
            if not data.get("expired"):
                upload_id = state["uploadId"]
                key = state["key"]
                for part in data["parts"]:
                    completed_map[part["partNumber"]] = part
                completed_parts = list(completed_map.values())
                remaining = total_parts - len(completed_parts)
                print(f"  {DIM}resuming:{RESET} {len(completed_parts)}/{total_parts} parts done, {remaining} remaining")

    # Phase 2: init
    if not upload_id:
        with httpx.Client(timeout=30, headers=headers) as client:
            r = client.post(f"{bss_url}/labels/upload/multipart/init", json={
                "owner": t.namespace,
                "project": t.project,
                "hash": raw_hash,
            })
            r.raise_for_status()
            init_data = r.json()
            upload_id = init_data["uploadId"]
            key = init_data["key"]
        _save_state(raw_hash, upload_id, key, total_parts, [], state_lock)

    # Phase 3: get presigned URLs
    remaining_parts = [n for n in range(1, total_parts + 1) if n not in completed_map]
    parts_data: dict[str, str] = {}
    if remaining_parts:
        with httpx.Client(timeout=30, headers=headers) as client:
            r = client.post(f"{bss_url}/labels/upload/multipart/parts", json={
                "uploadId": upload_id,
                "key": key,
                "partNumbers": remaining_parts,
            })
            r.raise_for_status()
            parts_data = r.json()["parts"]

    # Phase 4: parallel upload
    failed = False

    def upload_part(part_number: int) -> dict:
        start = (part_number - 1) * CHUNK_SIZE
        chunk = content[start: start + CHUNK_SIZE]
        url = parts_data[str(part_number)]
        with httpx.Client(timeout=300) as s3_client:
            resp = s3_client.put(url, content=chunk, headers={"Content-Type": "application/x-jsonlines"})
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

    with httpx.Client(timeout=60, headers=headers) as client:
        r = client.post(f"{bss_url}/labels/upload/multipart/complete", json={
            "uploadId": upload_id,
            "key": key,
            "parts": completed_parts,
        })
        r.raise_for_status()

    _clear_state(raw_hash)

    # Register in BSS (parses JSONL inline to extract stats)
    with httpx.Client(timeout=30, headers=headers) as client:
        r = client.post(f"{bss_url}/labels", json={
            "name": f"/{path}/{file_path.name}",
            "owner": t.namespace,
            "project": t.project,
            "episodeId": t.episode,
            "stagingHash": raw_hash,
        })
        r.raise_for_status()
        bss_label = r.json()

    bss_label_id = bss_label.get("id")
    entry_count = bss_label.get("entryCount", 0)
    fields = bss_label.get("fields", [])

    # Register in dreamlake-server
    with httpx.Client(timeout=30, headers=headers) as client:
        r = client.post(f"{remote}/assets/label-track", json={
            "namespace": t.namespace,
            "project": t.project,
            "episodeName": t.episode,
            "name": f"/{path}/{file_path.name}",
            "bssLabelId": bss_label_id,
        })
        r.raise_for_status()
        dl_asset = r.json()
        global _last_node_id; _last_node_id = dl_asset.get("nodeId")

    # Trigger Lambda processing via presigned URL
    lambda_url = dl_asset.get("lambdaUrl")
    if lambda_url:
        with httpx.Client(timeout=30) as client:
            r = client.post(lambda_url)
            if r.status_code == 202:
                print(f"  {DIM}processing:{RESET}   queued")
            else:
                print(f"  {DIM}processing:{RESET}   skipped ({r.status_code})", file=sys.stderr)

    print(f"{GREEN}✓ Uploaded:{RESET} /{path}")
    print(f"  {DIM}entries:{RESET}      {entry_count}")
    print(f"  {DIM}fields:{RESET}       {', '.join(fields) if fields else '(none detected)'}")
    print(f"  {DIM}bss id:{RESET}       {bss_label_id}")
    print(f"  {DIM}dreamlake id:{RESET} {dl_asset.get('id')}")
    return 0


def _upload_text_track(file_path: Path, t, path: str, token: str) -> int:
    """Upload a text track (VTT/SRT/JSONL) to BSS via S3 multipart, then register in dreamlake-server."""
    import hashlib
    import math
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import httpx

    bss_url = ServerConfig.bss_url
    remote = ServerConfig.remote
    headers = {"Authorization": f"Bearer {token}"}

    # Detect format from extension
    ext_to_format = {".vtt": "vtt", ".srt": "srt", ".jsonl": "jsonl"}
    fmt = ext_to_format.get(file_path.suffix.lower())
    if not fmt:
        print(f"{RED}error:{RESET} unsupported text track extension '{file_path.suffix}' — use .vtt, .srt, or .jsonl", file=sys.stderr)
        return 1

    ext_to_mime = {".vtt": "text/vtt", ".srt": "text/plain", ".jsonl": "application/x-jsonlines"}
    content_type = ext_to_mime[file_path.suffix.lower()]

    # Phase 1: hash + split
    content = file_path.read_bytes()
    raw_hash = hashlib.sha256(content).hexdigest()[:16]
    file_size = len(content)
    total_parts = max(1, math.ceil(file_size / CHUNK_SIZE))

    print(f"  {DIM}format:{RESET} {fmt}")
    print(f"  {DIM}size:{RESET}   {file_size / 1024:.1f} KB")
    print(f"  {DIM}parts:{RESET}  {total_parts} × {CHUNK_SIZE // 1024 // 1024} MB")

    state_lock = threading.Lock()
    upload_id: str | None = None
    key: str | None = None
    completed_parts: list[dict] = []
    completed_map: dict[int, dict] = {}

    state = _load_state(raw_hash)
    if state:
        with httpx.Client(timeout=30, headers=headers) as client:
            r = client.get(f"{bss_url}/text-tracks/upload/multipart/parts-done", params={
                "uploadId": state["uploadId"],
                "key": state["key"],
            })
        if r.status_code == 200:
            data = r.json()
            if not data.get("expired"):
                upload_id = state["uploadId"]
                key = state["key"]
                for part in data["parts"]:
                    completed_map[part["partNumber"]] = part
                completed_parts = list(completed_map.values())
                remaining = total_parts - len(completed_parts)
                print(f"  {DIM}resuming:{RESET} {len(completed_parts)}/{total_parts} parts done, {remaining} remaining")

    # Phase 2: init
    if not upload_id:
        with httpx.Client(timeout=30, headers=headers) as client:
            r = client.post(f"{bss_url}/text-tracks/upload/multipart/init", json={
                "owner": t.namespace,
                "project": t.project,
                "hash": raw_hash,
            })
            r.raise_for_status()
            init_data = r.json()
            upload_id = init_data["uploadId"]
            key = init_data["key"]
        _save_state(raw_hash, upload_id, key, total_parts, [], state_lock)

    # Phase 3: get presigned URLs
    remaining_parts = [n for n in range(1, total_parts + 1) if n not in completed_map]
    parts_data: dict[str, str] = {}
    if remaining_parts:
        with httpx.Client(timeout=30, headers=headers) as client:
            r = client.post(f"{bss_url}/text-tracks/upload/multipart/parts", json={
                "uploadId": upload_id,
                "key": key,
                "partNumbers": remaining_parts,
            })
            r.raise_for_status()
            parts_data = r.json()["parts"]

    # Phase 4: parallel upload
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

    with httpx.Client(timeout=60, headers=headers) as client:
        r = client.post(f"{bss_url}/text-tracks/upload/multipart/complete", json={
            "uploadId": upload_id,
            "key": key,
            "parts": completed_parts,
        })
        r.raise_for_status()

    _clear_state(raw_hash)

    # Register in BSS (parses content inline to extract stats)
    with httpx.Client(timeout=30, headers=headers) as client:
        r = client.post(f"{bss_url}/text-tracks", json={
            "name": f"/{path}/{file_path.name}",
            "owner": t.namespace,
            "project": t.project,
            "episodeId": t.episode,
            "stagingHash": raw_hash,
            "format": fmt,
        })
        r.raise_for_status()
        bss_track = r.json()

    bss_track_id = bss_track.get("id")
    entry_count = bss_track.get("entryCount", 0)

    # Register in dreamlake-server
    with httpx.Client(timeout=30, headers=headers) as client:
        r = client.post(f"{remote}/assets/text-track", json={
            "namespace": t.namespace,
            "project": t.project,
            "episodeName": t.episode,
            "name": f"/{path}/{file_path.name}",
            "bssTextTrackId": bss_track_id,
            "format": fmt,
        })
        r.raise_for_status()
        dl_asset = r.json()
        global _last_node_id; _last_node_id = dl_asset.get("nodeId")

    # Trigger Lambda processing via presigned URL
    lambda_url = dl_asset.get("lambdaUrl")
    if lambda_url:
        with httpx.Client(timeout=30) as client:
            r = client.post(lambda_url)
            if r.status_code == 202:
                print(f"  {DIM}processing:{RESET}   queued")
            else:
                print(f"  {DIM}processing:{RESET}   skipped ({r.status_code})", file=sys.stderr)

    print(f"{GREEN}✓ Uploaded:{RESET} /{path}")
    print(f"  {DIM}entries:{RESET}      {entry_count}")
    print(f"  {DIM}bss id:{RESET}       {bss_track_id}")
    print(f"  {DIM}dreamlake id:{RESET} {dl_asset.get('id')}")
    return 0


def _upload_image(file_path: Path, t, path: str, token: str) -> int:
    """Upload image to BSS via S3 multipart upload, then register in dreamlake-server. No Lambda."""
    import hashlib
    import math
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import httpx

    bss_url = ServerConfig.bss_url
    remote = ServerConfig.remote
    headers = {"Authorization": f"Bearer {token}"}

    ext_to_mime = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
        ".tiff": "image/tiff", ".tif": "image/tiff",
    }
    content_type = ext_to_mime.get(file_path.suffix.lower(), "image/jpeg")

    content = file_path.read_bytes()
    raw_hash = hashlib.sha256(content).hexdigest()[:16]
    file_size = len(content)
    total_parts = max(1, math.ceil(file_size / CHUNK_SIZE))

    print(f"  {DIM}size:{RESET}   {file_size / 1024 / 1024:.1f} MB")
    print(f"  {DIM}parts:{RESET}  {total_parts} x {CHUNK_SIZE // 1024 // 1024} MB")

    state_lock = threading.Lock()
    upload_id: str | None = None
    key: str | None = None
    completed_parts: list[dict] = []
    completed_map: dict[int, dict] = {}

    state = _load_state(raw_hash)
    if state:
        with httpx.Client(timeout=30, headers=headers) as client:
            r = client.get(f"{bss_url}/image/upload/multipart/parts-done", params={
                "uploadId": state["uploadId"],
                "key": state["key"],
            })
        if r.status_code == 200:
            data = r.json()
            if not data.get("expired"):
                upload_id = state["uploadId"]
                key = state["key"]
                for p in data.get("parts", []):
                    completed_map[p["partNumber"]] = p

    if not upload_id:
        with httpx.Client(timeout=30, headers=headers) as client:
            r = client.post(f"{bss_url}/image/upload/multipart/init", json={
                "owner": t.namespace, "project": t.project, "hash": raw_hash,
                "contentType": content_type,
            })
            r.raise_for_status()
            data = r.json()
            upload_id = data["uploadId"]
            key = data["key"]
        _save_state(raw_hash, upload_id, key)

    remaining_parts = [i + 1 for i in range(total_parts) if (i + 1) not in completed_map]

    if remaining_parts:
        with httpx.Client(timeout=30, headers=headers) as client:
            r = client.post(f"{bss_url}/image/upload/multipart/parts", json={
                "uploadId": upload_id, "key": key, "partNumbers": remaining_parts,
            })
            r.raise_for_status()
            part_urls = r.json()["parts"]

        def upload_part(part_number: int) -> dict:
            url = part_urls[str(part_number)]
            start = (part_number - 1) * CHUNK_SIZE
            end = min(start + CHUNK_SIZE, file_size)
            chunk = content[start:end]
            resp = httpx.Client(timeout=120).put(url, content=chunk, headers={"Content-Type": content_type})
            resp.raise_for_status()
            return {"partNumber": part_number, "etag": resp.headers["etag"]}

        failed = False
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(upload_part, n): n for n in remaining_parts}

            for f in as_completed(futures):
                pn = futures[f]
                try:
                    part = f.result()
                    with state_lock:
                        completed_map[pn] = part
                except Exception as e:
                    print(f"  {RED}part {pn} failed:{RESET} {e}", file=sys.stderr)
                    failed = True
                    for pending in futures:
                        if not pending.done():
                            pending.cancel()
                    break

        if failed:
            print(f"{RED}error:{RESET} upload paused — re-run to resume", file=sys.stderr)
            return 1

    completed_parts = sorted(completed_map.values(), key=lambda p: p["partNumber"])

    with httpx.Client(timeout=60, headers=headers) as client:
        r = client.post(f"{bss_url}/image/upload/multipart/complete", json={
            "uploadId": upload_id, "key": key, "parts": completed_parts,
        })
        r.raise_for_status()

    _clear_state(raw_hash)

    # Register in BSS
    with httpx.Client(timeout=30, headers=headers) as client:
        r = client.post(f"{bss_url}/image", json={
            "name": f"/{path}/{file_path.name}",
            "owner": t.namespace,
            "project": t.project,
            "episodeId": t.episode,
            "stagingHash": raw_hash,
            "fileSize": file_size,
        })
        r.raise_for_status()
        bss_image = r.json()

    bss_image_id = bss_image.get("id")

    # Register in dreamlake-server (no Lambda needed)
    with httpx.Client(timeout=30, headers=headers) as client:
        r = client.post(f"{remote}/assets/image", json={
            "namespace": t.namespace,
            "project": t.project,
            "episodeName": t.episode,
            "name": f"/{path}/{file_path.name}",
            "bssImageId": bss_image_id,
        })
        r.raise_for_status()
        dl_asset = r.json()
        global _last_node_id; _last_node_id = dl_asset.get("nodeId")

    print(f"{GREEN}✓ Uploaded:{RESET} /{path}/{file_path.name}")
    print(f"  {DIM}bss id:{RESET}       {bss_image_id}")
    print(f"  {DIM}dreamlake id:{RESET} {dl_asset.get('id')}")
    return 0


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
