"""
Artifact commands — upload/list Claude-Artifacts-style content
(HTML / React / Markdown / code / SVG / Mermaid).

Artifacts are stored as records in a per-namespace DreamDB dataset on
DreamLake's artifacts S3 bucket, then browsed + rendered by the dreamlake-ai UI.

Usage:
    dreamlake artifact push <file> [--title T] [--kind K] [--id ID]
    dreamlake artifact list [--namespace NS]

The push path:
  1. asks dreamlake-server for short-lived, prefix-scoped AWS credentials
     (POST /namespaces/<ns>/artifacts/upload-credentials)
  2. sets AWS_* env and writes the artifact via dreamdb-py (which must SigV4-sign
     its conditional writes — anonymous conditional writes are rejected by S3)

Reads (`list`) go straight to the public bucket, unsigned.
"""

import os
import sys
import argparse
from pathlib import Path

from dreamlake.cli._config import ServerConfig

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
CYAN = "\033[36m"

# Public bucket defaults — must match dreamlake-server's ARTIFACTS_* config.
DEFAULT_ARTIFACTS_BUCKET = os.environ.get("DREAMLAKE_ARTIFACTS_BUCKET", "dreamlake-artifacts")
DEFAULT_ARTIFACTS_REGION = os.environ.get("DREAMLAKE_ARTIFACTS_REGION", "us-east-1")
REF_NAME = "artifacts"  # single-segment DreamDB ref name (dreamdb-ts rejects multi-segment refs)

EXT_TO_KIND = {
    ".html": "html", ".htm": "html",
    ".jsx": "react", ".tsx": "react",
    ".md": "markdown", ".markdown": "markdown",
    ".svg": "svg",
    ".mmd": "mermaid", ".mermaid": "mermaid",
    ".js": "code", ".ts": "code", ".py": "code", ".css": "code",
    ".json": "code", ".txt": "code", ".sh": "code", ".rs": "code", ".go": "code",
}
KINDS = {"html", "react", "markdown", "svg", "code", "mermaid"}


def print_help():
    print(f"""
{BOLD}dreamlake artifact{RESET} - Manage renderable artifacts (HTML/React/Markdown/code/SVG/Mermaid)

{BOLD}Usage:{RESET}
    dreamlake artifact push <file> [--title T] [--kind K] [--id ID]
    dreamlake artifact list [--namespace NS]

{BOLD}push options:{RESET}
    --title    Human title (default: file stem)
    --kind     {', '.join(sorted(KINDS))} (default: auto from extension)
    --id       Stable artifact id for versioning (default: slug of title)
    --namespace  Target namespace (default: your login namespace)

{BOLD}Examples:{RESET}
    dreamlake artifact push ./dashboard.html --title "Q1 Dashboard"
    dreamlake artifact push ./chart.jsx --kind react --id sales-chart
    dreamlake artifact list
""".strip())


def _slugify(text: str) -> str:
    out = "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out or "artifact"


def _detect_kind(file_path: Path, override: str | None) -> str | None:
    if override:
        return override if override in KINDS else None
    return EXT_TO_KIND.get(file_path.suffix.lower())


def _import_dreamdb():
    try:
        import dreamdb as db  # noqa
        return db
    except ImportError:
        print(
            f"{RED}error:{RESET} the 'dreamdb' package is required for artifacts.\n"
            f"       install it from the dreamdb-py repo (uv pip install -e path/to/dreamdb-py).",
            file=sys.stderr,
        )
        return None


def _artifact_schema(db):
    # One dataset PER ARTIFACT (users/<ns>/<artifactId>); rows are the artifact's
    # versions. The content blob mime is FIXED ("artifact"); the real type lives
    # in the `kind` scalar. Schema is shared by every artifact dataset.
    return (
        db.Schema()
        .add_image("content", mime="artifact")
        .add_scalar_string("artifact_id")
        .add_scalar_string("title")
        .add_scalar_categorical("kind")
        .add_scalar_int("version")
    )


def _next_version(ds, artifact_id: str) -> int:
    """Latest version for this artifact_id + 1 (immutable append = version history).

    DreamDB's iter_scalar requires a predicate, so we filter by artifact_id via
    where_eq (a missing value simply yields no rows → version 1).
    """
    try:
        max_v = 0
        for batch in ds.iter_scalar(where_eq={"artifact_id": artifact_id}):
            for v in batch.get("version", []):
                try:
                    max_v = max(max_v, int(v))
                except (TypeError, ValueError):
                    pass
        return max_v + 1
    except Exception:
        return 1


# ── push ──────────────────────────────────────────────────────────────────────

def cmd_push(args: list) -> int:
    p = argparse.ArgumentParser(prog="dreamlake artifact push", add_help=True)
    p.add_argument("file")
    p.add_argument("--title", default=None)
    p.add_argument("--kind", default=None)
    p.add_argument("--id", dest="artifact_id", default=None)
    p.add_argument("--namespace", default=None)
    p.add_argument(
        "--visibility",
        choices=["public", "private"],
        default=None,
        help="read visibility (default: private). public artifacts are readable without login.",
    )
    p.add_argument(
        "--share",
        action="store_true",
        help="issue a share token so the artifact can be read via a ?share= link.",
    )
    ns = p.parse_args(args)

    file_path = Path(ns.file)
    if not file_path.exists() or not file_path.is_file():
        print(f"{RED}error:{RESET} file not found: {ns.file}", file=sys.stderr)
        return 1

    kind = _detect_kind(file_path, ns.kind)
    if not kind:
        print(
            f"{RED}error:{RESET} cannot determine kind for '{file_path.suffix}'. "
            f"use --kind ({', '.join(sorted(KINDS))}).",
            file=sys.stderr,
        )
        return 1

    title = ns.title or file_path.stem
    artifact_id = ns.artifact_id or _slugify(title)
    content = file_path.read_bytes()

    token = ServerConfig.resolve_token()
    if not token:
        print(f"{RED}error:{RESET} not authenticated. run 'dreamlake login' first.", file=sys.stderr)
        return 1
    namespace = ns.namespace or ServerConfig.resolve_namespace()
    if not namespace:
        print(f"{RED}error:{RESET} namespace not resolved. run 'dreamlake login'.", file=sys.stderr)
        return 1

    # 1) broker: short-lived, prefix-scoped upload credentials + backend target
    import httpx
    remote = ServerConfig.remote
    try:
        r = httpx.post(
            f"{remote}/namespaces/{namespace}/artifacts/upload-credentials",
            headers={"Authorization": f"Bearer {token}"},
            json={"artifactId": artifact_id},  # server returns this artifact's own dataset backend
            timeout=30,
        )
        r.raise_for_status()
        broker = r.json()
    except Exception as e:
        print(f"{RED}error:{RESET} could not get upload credentials: {e}", file=sys.stderr)
        return 1

    creds = broker["credentials"]
    os.environ["AWS_ACCESS_KEY_ID"] = creds["accessKeyId"]
    os.environ["AWS_SECRET_ACCESS_KEY"] = creds["secretAccessKey"]
    os.environ["AWS_SESSION_TOKEN"] = creds["sessionToken"]
    os.environ["AWS_REGION"] = broker.get("region", DEFAULT_ARTIFACTS_REGION)
    backend = broker["backendUrl"]
    ref = broker.get("refName", REF_NAME)

    # 2) write via dreamdb-py
    db = _import_dreamdb()
    if db is None:
        return 1

    schema = _artifact_schema(db)
    try:
        ds = db.Dataset.open(ref, schema, backend)
    except Exception:
        ds = db.Dataset.create(ref, schema, backend=backend)

    version = _next_version(ds, artifact_id)

    print(f"Uploading {CYAN}{file_path.name}{RESET} ({kind})")
    print(f"  {DIM}namespace:{RESET} {namespace}")
    print(f"  {DIM}artifact:{RESET}  {artifact_id} v{version}")
    print(f"  {DIM}size:{RESET}      {len(content)} bytes")

    try:
        ds.append_many([{
            "content": content,
            "artifact_id": artifact_id,
            "title": title,
            "kind": kind,
            "version": version,
        }])
    except Exception as e:
        print(f"{RED}error:{RESET} upload failed: {e}", file=sys.stderr)
        return 1

    print(f"{GREEN}✓ Pushed:{RESET} {artifact_id} v{version} — '{title}'")

    # 3) register the artifact in the server catalog (metadata + visibility).
    # Always runs so the catalog (which drives the gallery + `list`) stays in
    # sync; --visibility/--share tune access. Non-fatal if it fails.
    body = {"title": title, "kind": kind, "version": version}
    if ns.visibility:
        body["visibility"] = ns.visibility
    if ns.share:
        body["share"] = True
    try:
        vr = httpx.post(
            f"{remote}/namespaces/{namespace}/artifacts/{artifact_id}",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
            timeout=30,
        )
        vr.raise_for_status()
        cat = vr.json()
        print(f"  {DIM}visibility:{RESET} {cat.get('visibility')}")
        if cat.get("shareToken"):
            print(f"  {DIM}share:{RESET}      ?share={cat['shareToken']}")
    except Exception as e:
        print(f"{RED}warning:{RESET} upload ok but could not register in catalog: {e}", file=sys.stderr)

    return 0


# ── list ──────────────────────────────────────────────────────────────────────

def cmd_list(args: list) -> int:
    p = argparse.ArgumentParser(prog="dreamlake artifact list", add_help=True)
    p.add_argument("--namespace", default=None)
    ns = p.parse_args(args)

    namespace = ns.namespace or ServerConfig.resolve_namespace()
    if not namespace:
        print(f"{RED}error:{RESET} namespace not resolved. run 'dreamlake login'.", file=sys.stderr)
        return 1

    # Read from the server catalog (source of truth now that each artifact lives
    # in its own dataset). Authed → all your artifacts; anonymous → public only.
    # Works after the bucket is locked down (no unsigned S3 reads).
    import httpx
    remote = ServerConfig.remote
    token = ServerConfig.resolve_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        r = httpx.get(f"{remote}/namespaces/{namespace}/artifacts", headers=headers, timeout=30)
        r.raise_for_status()
        artifacts = r.json().get("artifacts", [])
    except Exception as e:
        print(f"{RED}error:{RESET} could not list artifacts: {e}", file=sys.stderr)
        return 1

    if not artifacts:
        print(f"{DIM}no artifacts in namespace '{namespace}'.{RESET}")
        return 0

    print(f"{BOLD}Artifacts in {namespace}:{RESET}")
    for a in artifacts:
        vis = a.get("visibility", "private")
        vtag = "" if vis == "private" else f"  {DIM}[{vis}]{RESET}"
        print(
            f"  {CYAN}{a['artifactId']}{RESET}  {DIM}v{a.get('latestVersion')} · {a.get('kind')}{RESET}"
            f"  {a.get('title')}{vtag}"
        )
    return 0


def main(args: list) -> int:
    if not args or args[0] in ("-h", "--help", "help"):
        print_help()
        return 0 if args else 1

    sub, rest = args[0], args[1:]
    if sub == "push":
        return cmd_push(rest)
    if sub == "list":
        return cmd_list(rest)

    print(f"{RED}error:{RESET} unknown artifact subcommand '{sub}'", file=sys.stderr)
    print_help()
    return 1
