"""``dreamlake source`` — managed DreamDB video sources.

Three steps, matching the three concepts (source → collection/schema → data):

    dreamlake source create <name> [--ns <slug>]
    dreamlake source collection create <name> --source <src> [--preset video | --schema s.json]
    dreamlake source push <dir> --source <src> --collection <col>
    dreamlake source push --manifest m.json --source <src> --collection <col>

The write path (slicing video into CMAF fragments) lives only in the Python
``dreamdb`` SDK, so it belongs here even though the general CLI is deprecated.
The server only creates the source row and mints short-lived, prefix-scoped
credentials; video bytes go CLI → S3 directly on the user's bandwidth.

Every clip in one collection must share ONE CMAF init segment. We normalise each
clip to a fixed config AND zero the init's ``btrt`` box (a bitrate hint that is
the only per-clip variance) — that makes the init byte-identical across every
clip and every push, so appending later just works.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from textwrap import dedent

from dreamlake.cli._config import ServerConfig

RESET = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
RED = "\033[31m"; GREEN = "\033[32m"; CYAN = "\033[36m"

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
FIELD_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
SCALAR_TYPES = {"scalar_string", "scalar_int", "scalar_float", "scalar_bool", "scalar_categorical", "scalar_timestamp"}
BLOB_TYPES = {"image"}            # non-video blobs ingestable via append_many (raw bytes)
# audio is intentionally omitted: the dreamdb SDK's append_many can't ingest it
# yet (planned "Phase 1.6"), so a declarable-but-unfillable field would only mislead.
SCHEMA_TYPES = {"video", "embedding"} | BLOB_TYPES | SCALAR_TYPES
PRESET_VIDEO_FIELDS = [
    {"name": "video", "type": "video", "mime": "h264"},
    {"name": "path", "type": "scalar_string"},
]
STRIDE_NS = 3600 * 10**9  # one 1h anchor slot per clip


def _err(msg: str) -> None:
    print(f"{RED}error:{RESET} {msg}", file=sys.stderr)


def _errs(title: str, problems: list) -> None:
    _err(f"{title} ({len(problems)} problem(s)):")
    for p in problems:
        print(f"  - {p}", file=sys.stderr)


def _ok(msg: str) -> None:
    print(f"{GREEN}✓{RESET} {msg}")


# ── auth + http ───────────────────────────────────────────────────────────────

def _auth(ns_arg):
    """Return (token, namespace, remote) or None after printing a friendly error."""
    token = ServerConfig.resolve_token()
    if not token:
        _err("not authenticated. run 'dreamlake login' first.")
        return None
    namespace = ns_arg or ServerConfig.resolve_namespace()
    if not namespace:
        _err("could not resolve a namespace. run 'dreamlake login', or pass --ns <slug>.")
        return None
    return token, namespace, ServerConfig.remote


def _http(method, url, token, body=None):
    import httpx
    # Bypass a system/env HTTP proxy for localhost only: macOS httpx reads the OS
    # proxy config and tunnels even localhost through it, which hangs local dev.
    # For a remote server (staging/prod) respect the proxy — a corporate network
    # may require it to reach the API at all.
    is_local = "://localhost" in url or "://127.0.0.1" in url
    try:
        with httpx.Client(timeout=60, trust_env=not is_local, headers={"Authorization": f"Bearer {token}"}) as c:
            r = c.request(method, url, json=body)
    except Exception as e:
        return 0, {"error": f"cannot reach server: {e}"}
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"error": r.text}


def _find_source(remote, ns, name, token):
    """Return the source dict, the string 'missing', or None (on error, already printed)."""
    st, data = _http("GET", f"{remote}/namespaces/{ns}/sources", token)
    if st == 404:
        _err(f"namespace '{ns}' not found.")
        return None
    if st in (401, 403):
        _err(f"you don't have access to namespace '{ns}' (or your login expired).")
        return None
    if st // 100 != 2:
        _err(f"could not list sources ({st}): {data.get('error') or data}")
        return None
    rows = data.get("sources") if isinstance(data, dict) else data
    for s in rows or []:
        if s.get("name") == name:
            return s
    return "missing"


def _mint_credentials(remote, ns, source_id, token):
    st, data = _http("POST", f"{remote}/namespaces/{ns}/sources/{source_id}/dreamdb/credentials", token)
    if st // 100 != 2:
        return None, (data.get("message") or data.get("error") or f"HTTP {st}")
    return data, None


def _apply_credentials(creds) -> str:
    os.environ["AWS_ACCESS_KEY_ID"] = creds["accessKeyId"]
    os.environ["AWS_SECRET_ACCESS_KEY"] = creds["secretAccessKey"]
    os.environ["AWS_SESSION_TOKEN"] = creds["sessionToken"]
    os.environ["AWS_REGION"] = creds["region"]
    return creds["backendUrl"]


def _import_dreamdb():
    try:
        import dreamdb as db
    except ImportError:
        _err("the DreamDB SDK is not installed. run: pip install dreamdb")
        return None
    return db


# ── schema ────────────────────────────────────────────────────────────────────

def _validate_fields(spec):
    """Validate a schema/manifest's field list. Return (fields, errors)."""
    fields = spec.get("fields") if isinstance(spec, dict) else spec
    if not isinstance(fields, list) or not fields:
        return None, ["schema must have a non-empty 'fields' array"]
    errors, seen = [], set()
    for i, f in enumerate(fields):
        if not isinstance(f, dict):
            errors.append(f"field #{i} must be an object"); continue
        n, t = f.get("name"), f.get("type")
        if not isinstance(n, str) or not FIELD_RE.match(n):
            errors.append(f"field #{i}: invalid name {n!r} (lowercase letters/digits/_ , must start alphanumeric)")
        elif n in seen:
            errors.append(f"duplicate field name '{n}'")
        seen.add(n)
        if t not in SCHEMA_TYPES:
            errors.append(f"field '{n}': unknown type {t!r} (allowed: {', '.join(sorted(SCHEMA_TYPES))})")
        if t == "video" and not f.get("mime"):
            errors.append(f"video field '{n}' needs a 'mime' (e.g. \"h264\")")
        if t == "embedding" and not isinstance(f.get("dim"), int):
            errors.append(f"embedding field '{n}' needs an integer 'dim'")
    return fields, errors


def _build_schema(db, fields):
    s = db.Schema()
    for f in fields:
        n, t = f["name"], f["type"]
        if t == "video":
            s = s.add_video(n, mime=f.get("mime", "h264"), required=False)
        elif t == "image":
            s = s.add_image(n, mime=f.get("mime", "jpeg"), required=False)
        elif t == "scalar_string":
            s = s.add_scalar_string(n, required=False)
        elif t == "scalar_int":
            s = s.add_scalar_int(n, required=False)
        elif t == "scalar_float":
            s = s.add_scalar_float(n, required=False)
        elif t == "scalar_bool":
            s = s.add_scalar_bool(n, required=False)
        elif t == "scalar_categorical":
            s = s.add_scalar_categorical(n, required=False)
        elif t == "scalar_timestamp":
            s = s.add_scalar_timestamp(n, required=False)
        elif t == "embedding":
            s = s.add_embedding(n, dim=int(f["dim"]))
    return s


def _load_vector(val, dim):
    """Resolve an embedding value: an inline list, or a path to a .npy file →
    a float32 numpy array. `dim` (if given) is checked."""
    import numpy as np
    if isinstance(val, str):
        arr = np.load(val).astype(np.float32).reshape(-1)   # path already absolute
    else:
        arr = np.asarray(val, dtype=np.float32).reshape(-1)
    if dim and arr.shape[0] != int(dim):
        raise ValueError(f"embedding has {arr.shape[0]} dims, schema declares {dim}")
    return arr


# ── ffmpeg: normalise + fragment + deterministic init ─────────────────────────

def _has_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _normalize(src, dst, width, height):
    """Re-encode to a uniform, browser-playable H.264 (pins everything that lands
    in the CMAF init: resolution, fps, SAR, profile, level, colour, fixed GOP)."""
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
          f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,fps=30,format=yuv420p,setsar=1")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf", vf, "-fps_mode", "cfr",
         "-c:v", "libx264", "-profile:v", "high", "-level:v", "3.1", "-preset", "veryfast", "-b:v", "2M",
         "-x264-params", "keyint=60:min-keyint=60:scenecut=0",
         "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-an", str(dst)],
        check=True,
    )


def _zero_btrt(init: bytes) -> bytes:
    """Zero every ``btrt`` box payload — the only per-clip variance in an
    otherwise-uniform init, so this makes the init byte-identical across clips."""
    b = bytearray(init)
    i = b.find(b"btrt")
    while i != -1:
        b[i + 4:i + 16] = b"\x00" * 12
        i = b.find(b"btrt", i + 4)
    return bytes(b)


def _ingest_clip(db, ds, field, video_path, anchor, width, height, tmp) -> int:
    """Normalise → CMAF-fragment → canonical (btrt-zeroed) init → ingest. Returns fragment count."""
    norm = Path(tmp) / f"n_{anchor}.mp4"
    _normalize(video_path, norm, width, height)
    fragdir = Path(tmp) / f"f_{anchor}"
    fragdir.mkdir(exist_ok=True)
    init_path, frags = db._fragment_video(str(norm), str(fragdir), 2.0, anchor, None)
    canon = Path(tmp) / f"init_{anchor}.mp4"
    canon.write_bytes(_zero_btrt(Path(init_path).read_bytes()))
    ds._inner._ingest_cmaf(field, str(canon), frags)
    return len(frags)


# ── subcommand: create ────────────────────────────────────────────────────────

def cmd_create(args) -> int:
    p = argparse.ArgumentParser(prog="dreamlake source create", add_help=True)
    p.add_argument("name", help="source name")
    p.add_argument("--ns", "--namespace", dest="ns", default=None, help="namespace slug (personal OR org)")
    a = p.parse_args(args)
    if not NAME_RE.match(a.name):
        _err("source name must match ^[a-z0-9][a-z0-9._-]{0,63}$"); return 1

    auth = _auth(a.ns)
    if not auth:
        return 1
    token, ns, remote = auth
    st, data = _http("POST", f"{remote}/namespaces/{ns}/sources", token,
                     {"name": a.name, "kind": "dreamdb", "config": {}})
    if st == 404:
        _err(f"namespace '{ns}' not found."); return 1
    if st == 401:
        _err("your login expired. run 'dreamlake login' again."); return 1
    if st == 403:
        _err(f"not allowed to create a source in '{ns}'. you must be its owner "
             f"(organisation members are read-only)."); return 1
    if st == 409:
        _err(f"a source named '{a.name}' already exists in '{ns}'."); return 1
    if st // 100 != 2:
        _err(f"create failed ({st}): {data.get('message') or data.get('error') or data}"); return 1

    src = data.get("source", data)
    _ok(f"created dreamdb source '{a.name}' in {ns}  (id {src.get('id')})")
    print(f"  {DIM}next: dreamlake source collection create <name> --source {a.name} --preset video{RESET}")
    return 0


# ── subcommand: collection create ─────────────────────────────────────────────

def cmd_collection_create(args) -> int:
    p = argparse.ArgumentParser(prog="dreamlake source collection create", add_help=True)
    p.add_argument("name", help="collection (dataset) name")
    p.add_argument("--source", required=True, help="dreamdb source name")
    p.add_argument("--ns", "--namespace", dest="ns", default=None)
    p.add_argument("--preset", choices=["video"], default=None, help="use a built-in schema (video + path)")
    p.add_argument("--schema", default=None, help="schema JSON file defining the fields")
    a = p.parse_args(args)
    if not NAME_RE.match(a.name):
        _err("collection name must match ^[a-z0-9][a-z0-9._-]{0,63}$"); return 1

    # Resolve the schema BEFORE touching the server.
    if a.schema:
        try:
            spec = json.loads(Path(a.schema).read_text(encoding="utf-8"))
        except FileNotFoundError:
            _err(f"schema file not found: {a.schema}"); return 1
        except Exception as e:
            _err(f"schema is not valid JSON: {e}"); return 1
        fields, errors = _validate_fields(spec)
        if errors:
            _errs("schema is invalid", errors); return 1
    else:
        fields = PRESET_VIDEO_FIELDS  # default preset

    auth = _auth(a.ns)
    if not auth:
        return 1
    token, ns, remote = auth
    src = _find_source(remote, ns, a.source, token)
    if src is None:
        return 1
    if src == "missing":
        _err(f"source '{a.source}' not found in '{ns}'. create it first:\n"
             f"  dreamlake source create {a.source}"); return 1
    if src.get("kind") != "dreamdb":
        _err(f"source '{a.source}' is kind '{src.get('kind')}', not dreamdb."); return 1

    creds, err = _mint_credentials(remote, ns, src["id"], token)
    if not creds:
        _err(f"could not get write credentials: {err}"); return 1
    backend = _apply_credentials(creds)
    db = _import_dreamdb()
    if not db:
        return 1

    schema = _build_schema(db, fields)
    try:
        db.Dataset.create(a.name, schema, backend)
    except Exception as e:
        msg = str(e)
        if "exist" in msg.lower() or "conflict" in msg.lower():
            _err(f"collection '{a.name}' already exists on source '{a.source}'. pick a new name."); return 1
        _err(f"could not create collection: {msg}"); return 1

    _ok(f"created collection '{a.name}' on '{a.source}'  ({len(fields)} field(s): "
        f"{', '.join(f['name'] for f in fields)})")
    print(f"  {DIM}next: dreamlake source push <dir> --source {a.source} --collection {a.name}{RESET}")
    return 0


# ── subcommand: push ──────────────────────────────────────────────────────────

def _classify(fields):
    """Split field names by how they're ingested."""
    ftype = {f["name"]: f["type"] for f in fields}
    return (
        {n for n, t in ftype.items() if t == "video"},       # sliced
        {n for n, t in ftype.items() if t in BLOB_TYPES},    # image/audio → file bytes
        {n for n, t in ftype.items() if t == "embedding"},   # vector
        {n for n, t in ftype.items() if t in SCALAR_TYPES},  # plain value
    )


def _records_from_dir(root: Path):
    vids = sorted(pp for pp in root.rglob("*") if pp.suffix.lower() in VIDEO_EXTS)
    records = [{"anchor": i * STRIDE_NS, "video": str(v), "path": str(v.relative_to(root))}
               for i, v in enumerate(vids)]
    return PRESET_VIDEO_FIELDS, records


def _records_from_manifest(path: Path):
    """Return (fields, records, errors). File-valued fields (video/image/audio,
    and .npy embeddings) are validated to exist and rewritten to absolute paths."""
    try:
        man = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, None, [f"manifest not found: {path}"]
    except Exception as e:
        return None, None, [f"manifest is not valid JSON: {e}"]

    fields, errors = _validate_fields(man)
    records = man.get("records") if isinstance(man, dict) else None
    if not isinstance(records, list) or not records:
        (errors or []).append("manifest needs a non-empty 'records' array")
    if errors:
        return None, None, errors

    names = {f["name"] for f in fields}
    dims = {f["name"]: f.get("dim") for f in fields if f["type"] == "embedding"}
    video_fields, blob_fields, emb_fields, _ = _classify(fields)
    file_fields = video_fields | blob_fields
    base = path.resolve().parent
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            errors.append(f"record #{i} must be an object"); continue
        if "anchor" not in rec or not isinstance(rec["anchor"], int):
            errors.append(f"record #{i}: missing integer 'anchor'")
        for k in rec:
            if k != "anchor" and k not in names:
                errors.append(f"record #{i}: '{k}' is not a declared field")
        for ff in file_fields:
            if ff in rec and not (base / rec[ff]).is_file():
                errors.append(f"record #{i}: file not found for '{ff}': {rec[ff]}")
        for ef in emb_fields:                       # embedding: inline list OR .npy path
            if ef in rec:
                v = rec[ef]
                if isinstance(v, str):
                    if not (base / v).is_file():
                        errors.append(f"record #{i}: embedding .npy not found for '{ef}': {v}")
                elif isinstance(v, list):
                    d = dims.get(ef)
                    if d and len(v) != int(d):
                        errors.append(f"record #{i}: embedding '{ef}' has {len(v)} dims, schema declares {d}")
                else:
                    errors.append(f"record #{i}: embedding '{ef}' must be a number list or a .npy path")
    if errors:
        return None, None, errors
    # rewrite file-valued paths (video/image/audio, and .npy embeddings) to absolute
    for rec in records:
        for ff in file_fields:
            if ff in rec:
                rec[ff] = str((base / rec[ff]).resolve())
        for ef in emb_fields:
            if ef in rec and isinstance(rec[ef], str):
                rec[ef] = str((base / rec[ef]).resolve())
    return fields, records, []


def cmd_push(args) -> int:
    p = argparse.ArgumentParser(prog="dreamlake source push", add_help=True)
    p.add_argument("dir", nargs="?", default=None, help="directory of videos (folder mode)")
    p.add_argument("--source", required=True)
    p.add_argument("--collection", required=True)
    p.add_argument("--ns", "--namespace", dest="ns", default=None)
    p.add_argument("--manifest", default=None, help="manifest JSON (custom fields + records)")
    p.add_argument("--width", type=int, default=854, help="uniform target width (default 854)")
    p.add_argument("--height", type=int, default=480, help="uniform target height (default 480)")
    a = p.parse_args(args)

    if not a.dir and not a.manifest:
        _err("give a <dir> of videos, or --manifest <file>"); return 1
    if a.dir and a.manifest:
        _err("use either <dir> OR --manifest, not both"); return 1

    if a.manifest:
        fields, records, errors = _records_from_manifest(Path(a.manifest))
        if errors:
            _errs("manifest is invalid", errors); return 1
    else:
        root = Path(a.dir)
        if not root.is_dir():
            _err(f"not a directory: {a.dir}"); return 1
        fields, records = _records_from_dir(root)
        if not records:
            _err(f"no video files under {root} (looked for {', '.join(sorted(VIDEO_EXTS))})"); return 1

    video_fields, blob_fields, emb_fields, scalar_fields = _classify(fields)
    if video_fields and not _has_ffmpeg():
        _err("ffmpeg/ffprobe not found in PATH. install: brew install ffmpeg (macOS) / apt install ffmpeg"); return 1

    auth = _auth(a.ns)
    if not auth:
        return 1
    token, ns, remote = auth
    src = _find_source(remote, ns, a.source, token)
    if src is None:
        return 1
    if src == "missing":
        _err(f"source '{a.source}' not found. create it first: dreamlake source create {a.source}"); return 1
    if src.get("kind") != "dreamdb":
        _err(f"source '{a.source}' is not a dreamdb source."); return 1

    creds, err = _mint_credentials(remote, ns, src["id"], token)
    if not creds:
        _err(f"could not get write credentials: {err}"); return 1
    backend = _apply_credentials(creds)
    db = _import_dreamdb()
    if not db:
        return 1
    if video_fields and not hasattr(db, "_fragment_video"):
        _err("this dreamdb build can't slice video (no _fragment_video). upgrade: pip install -U dreamdb"); return 1

    try:
        ds = db.Dataset.open(a.collection, backend=backend)
    except Exception:
        _err(f"collection '{a.collection}' not found on source '{a.source}'. create it first:\n"
             f"  dreamlake source collection create {a.collection} --source {a.source}"); return 1

    print(f"pushing {len(records)} record(s) → {a.source}/{a.collection}")
    total_frags = 0
    try:
        with tempfile.TemporaryDirectory(prefix="dl-source-") as tmp:
            for i, rec in enumerate(records):
                anchor = int(rec["anchor"])
                # video → slice; everything else goes in one append_many row
                for vf in video_fields:
                    if vf in rec:
                        total_frags += _ingest_clip(db, ds, vf, rec[vf], anchor, a.width, a.height, tmp)
                row = {"_anchor": anchor}
                for bf in blob_fields:                       # image / audio → raw file bytes
                    if bf in rec:
                        row[bf] = Path(rec[bf]).read_bytes()
                for ef in emb_fields:                        # embedding → float32 vector
                    if ef in rec:
                        row[ef] = _load_vector(rec[ef], next((f.get("dim") for f in fields if f["name"] == ef), None))
                for sf in scalar_fields:                     # scalars → plain value
                    if sf in rec:
                        row[sf] = rec[sf]
                if len(row) > 1:
                    ds.append_many([row])
                label = rec.get("path") or next((os.path.basename(rec[vf]) for vf in video_fields if vf in rec), f"record {i+1}")
                print(f"  {GREEN}✓{RESET} {label}")
    except subprocess.CalledProcessError:
        _err("ffmpeg failed while transcoding — is the source file a valid video?"); return 1
    except Exception as e:
        _err(f"push failed: {e}"); return 1

    _ok(f"pushed {len(records)} record(s), {total_frags} fragment(s) → {a.source}/{a.collection}")
    print(f"  {DIM}view it in the web UI: {ns} → Sources → {a.source} → {a.collection}{RESET}")
    return 0


# ── dispatch ──────────────────────────────────────────────────────────────────

def _help() -> None:
    print(dedent(f"""
        {BOLD}dreamlake source{RESET} — managed DreamDB video sources

        {BOLD}Steps:{RESET}
            {CYAN}source create{RESET} <name> [--ns <slug>]
                Create a dreamdb source (personal, or an org via --ns <org-slug>).

            {CYAN}source collection create{RESET} <name> --source <src> [--preset video | --schema s.json]
                Create a collection and define its schema. Default: --preset video.

            {CYAN}source push{RESET} <dir> --source <src> --collection <col> [--width W --height H]
            {CYAN}source push{RESET} --manifest m.json --source <src> --collection <col>
                Slice videos into 2s CMAF fragments and write them (folder or manifest).

        {BOLD}Example:{RESET}
            dreamlake login
            dreamlake source create my-videos
            dreamlake source collection create trip --source my-videos --preset video
            dreamlake source push ./clips --source my-videos --collection trip

        {BOLD}schema.json / manifest:{RESET}
            {{ "fields": [
                 {{ "name": "video", "type": "video", "mime": "h264" }},
                 {{ "name": "thumb", "type": "image", "mime": "jpeg" }},
                 {{ "name": "clip",  "type": "embedding", "dim": 512 }},
                 {{ "name": "label", "type": "scalar_categorical" }} ],
              "records": [ {{ "anchor": 0, "video": "a.mp4", "thumb": "a.jpg",
                              "clip": "a.npy", "label": "cat" }} ] }}
            types: video · image · embedding · scalar_string · scalar_int ·
                   scalar_float · scalar_bool · scalar_categorical · scalar_timestamp
            push --manifest uses records; file fields (video/image/audio, .npy
            embeddings) are paths relative to the manifest. embeddings may also
            be an inline number list.
    """).rstrip())


def main(args) -> int:
    if not args or args[0] in ("-h", "--help", "help"):
        _help()
        return 0 if args else 1
    sub = args[0]
    if sub == "create":
        return cmd_create(args[1:])
    if sub == "collection":
        if len(args) >= 2 and args[1] == "create":
            return cmd_collection_create(args[2:])
        _err("usage: dreamlake source collection create <name> --source <src> [--preset video | --schema s.json]")
        return 1
    if sub == "push":
        return cmd_push(args[1:])
    _err(f"unknown source subcommand '{sub}'. try: create | collection create | push")
    return 1
