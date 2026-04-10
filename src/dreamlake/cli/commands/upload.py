"""
Upload command.

Usage:
    dreamlake upload <file> --sess [namespace@]space[:session] --to <path>

File type is auto-detected from extension. Use --type to override.

Categories: audio, video, track, text-track, label-track
"""

import sys
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
}

CATEGORIES = {"audio", "video", "track", "text-track", "label-track"}


@proto.prefix
class UploadConfig:
    sess: str | None = None   # [namespace@]space[:session]
    to: str | None = None     # destination path (within session)
    type: str | None = None   # category override


def print_help():
    print(f"""
{BOLD}dreamlake upload{RESET} - Upload a file to DreamLake

{BOLD}Usage:{RESET}
    dreamlake upload <file> --sess [namespace@]space[:session] --to <path>

{BOLD}Options:{RESET}
    --sess    Session scope: [namespace@]space[:session]
    --to      Destination path within the session
    --type    Override auto-detected file type

{BOLD}Auto-detected types:{RESET}
    .wav .mp3 .aac .opus  → audio
    .mp4 .mkv             → video
    .npy .msgpack .csv    → track
    .srt .vtt             → text-track
    .jsonl                → label-track (default; use --type text-track to override)

{BOLD}Examples:{RESET}
    dreamlake upload ./mic.wav --sess alice@robotics:2026/q1/run-042 --to /microphone/front
    dreamlake upload ./video.mp4 --sess robotics:experiments/run-042 --to /camera/front
    dreamlake upload ./labels.jsonl --sess alice@robotics:run-042 --to /detections/yolo
    dreamlake upload ./transcript.jsonl --sess alice@robotics:run-042 --to /subtitles/en --type text-track
""".strip())


def detect_category(file_path: Path, type_override: str | None) -> str | None:
    if type_override:
        return type_override
    return EXTENSION_TO_CATEGORY.get(file_path.suffix.lower())


def cmd_upload(file: str) -> int:
    if not UploadConfig.sess:
        print(f"{RED}error:{RESET} --sess is required", file=sys.stderr)
        return 1

    if not UploadConfig.to:
        print(f"{RED}error:{RESET} --to is required", file=sys.stderr)
        return 1

    file_path = Path(file)
    if not file_path.exists():
        print(f"{RED}error:{RESET} file not found: {file}", file=sys.stderr)
        return 1

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
        t = parse_target(UploadConfig.sess)
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
    print(f"  {DIM}session:{RESET} {format_target(t)}")
    print(f"  {DIM}path:{RESET}    /{path}")

    try:
        if category == "video":
            return _upload_video(file_path, t, path, token)
        else:
            return _upload_asset(file_path, t, path, category, token)
    except Exception as e:
        print(f"{RED}error:{RESET} {e}", file=sys.stderr)
        return 1


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
                "project": t.space,
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
            "name": path.split("/")[-1],
            "owner": t.namespace,
            "project": t.space,
            "sessionId": t.session,
            "rawHash": raw_hash,
        })
        r.raise_for_status()
        bss_video = r.json()

    # Register in dreamlake-server (links asset to namespace/space/session)
    with httpx.Client(timeout=30, headers=headers) as client:
        r = client.post(f"{remote}/assets/video", json={
            "namespace": t.namespace,
            "space": t.space,
            "sessionName": t.session,
            "name": f"/{path}",
            "bssVideoId": bss_video.get("id"),
            "fps": 30,
            "lens": "pinhole",
        })
        r.raise_for_status()
        dl_asset = r.json()

    # Trigger HLS splitting (async — Lambda processes in background)
    bss_video_id = bss_video.get("id")
    with httpx.Client(timeout=30, headers=headers) as client:
        r = client.post(f"{bss_url}/videos/{bss_video_id}/split")
        if r.status_code == 202:
            print(f"  {DIM}splitting:{RESET}    queued")
        else:
            print(f"  {DIM}splitting:{RESET}    skipped ({r.status_code})", file=sys.stderr)

    print(f"{GREEN}✓ Uploaded:{RESET} /{path}")
    print(f"  {DIM}bss id:{RESET}       {bss_video_id}")
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
                "project": t.space,
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
            "name": path.split("/")[-1],
            "owner": t.namespace,
            "project": t.space,
            "sessionId": t.session,
            "rawHash": raw_hash,
        })
        r.raise_for_status()
        bss_audio = r.json()

    bss_audio_id = bss_audio.get("id")

    # Trigger Lambda processing (async)
    with httpx.Client(timeout=30, headers=headers) as client:
        r = client.post(f"{bss_url}/audio/{bss_audio_id}/process")
        if r.status_code == 202:
            print(f"  {DIM}processing:{RESET}   queued")
        else:
            print(f"  {DIM}processing:{RESET}   skipped ({r.status_code})", file=sys.stderr)

    # Register in dreamlake-server
    with httpx.Client(timeout=30, headers=headers) as client:
        r = client.post(f"{remote}/assets/audio", json={
            "namespace": t.namespace,
            "space": t.space,
            "sessionName": t.session,
            "name": f"/{path}",
            "bssAudioId": bss_audio_id,
        })
        r.raise_for_status()
        dl_asset = r.json()

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
                "project": t.space,
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
            "name": path.split("/")[-1],
            "owner": t.namespace,
            "project": t.space,
            "sessionId": t.session,
            "rawHash": raw_hash,
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
            "space": t.space,
            "sessionName": t.session,
            "name": f"/{path}",
            "bssLabelId": bss_label_id,
        })
        r.raise_for_status()
        dl_asset = r.json()

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
                "project": t.space,
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
            "name": path.split("/")[-1],
            "owner": t.namespace,
            "project": t.space,
            "sessionId": t.session,
            "rawHash": raw_hash,
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
            "space": t.space,
            "sessionName": t.session,
            "name": f"/{path}",
            "bssTextTrackId": bss_track_id,
            "format": fmt,
        })
        r.raise_for_status()
        dl_asset = r.json()

    print(f"{GREEN}✓ Uploaded:{RESET} /{path}")
    print(f"  {DIM}entries:{RESET}      {entry_count}")
    print(f"  {DIM}bss id:{RESET}       {bss_track_id}")
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
