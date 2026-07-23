"""
Workflow commands — push/list WorkflowSpec v1 JSON files.

A workflow spec's versions are stored as records in the workflow's OWN DreamDB
dataset at workflows/<ns>/<name> on DreamLake's storage bucket (per-workflow
prefix isolation, exactly like artifacts), then rendered by the dreamlake-ai
workflow canvas. Content lives ONLY in DreamDB — the server catalog holds
metadata (specVersion / specMeta).

Usage:
    dreamlake workflow push <file.json> [--name N] [--namespace NS]
    dreamlake workflow list [--namespace NS]

The push path:
  1. VALIDATES the spec against the bundled WorkflowSpec v1 JSON Schema
     (fails before any upload)
  2. asks dreamlake-server for short-lived, prefix-scoped AWS credentials
     (POST /namespaces/<ns>/workflows/<name>/spec/upload-credentials)
  3. sets AWS_* env and appends the spec via dreamdb-py (SigV4-signed
     conditional writes), version = max(existing) + 1
  4. registers the version in the server catalog (metadata only)
"""

import json
import os
import re
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

REF_NAME = "workflows"  # single-segment DreamDB ref name
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def print_help():
    print(f"""
{BOLD}dreamlake workflow{RESET} - Push and list workflow specs (WorkflowSpec v1 JSON)

{BOLD}Usage:{RESET}
    dreamlake workflow push <file> [--name N] [--namespace NS]
    dreamlake workflow list [--namespace NS]

<file> is a WorkflowSpec v1 JSON. The '.workflow.json' suffix is optional:
'dreamlake workflow push bimanual-pretrain-set' finds
'bimanual-pretrain-set.workflow.json'.

{BOLD}push options:{RESET}
    --name       Workflow name (default: the spec's "name" field)
    --namespace  Target namespace (default: your login namespace)

{BOLD}Examples:{RESET}
    dreamlake workflow push bimanual-pretrain-set
    dreamlake workflow push ./bimanual-pretrain-set.workflow.json
    dreamlake workflow list
""".strip())


def _web_base(remote: str) -> str:
    """Dashboard origin (override DREAMLAKE_WEB_URL; else strip leading api.)."""
    override = os.environ.get("DREAMLAKE_WEB_URL")
    if override:
        return override.rstrip("/")
    try:
        from urllib.parse import urlparse

        u = urlparse(remote)
        host = u.hostname or ""
        if host.startswith("api."):
            port = f":{u.port}" if u.port else ""
            return f"{u.scheme}://{host[len('api.'):]}{port}"
    except Exception:
        pass
    return remote.rstrip("/")


def _import_dreamdb():
    try:
        import dreamdb as db  # noqa
        return db
    except ImportError:
        print(
            f"{RED}error:{RESET} the 'dreamdb' package is required for workflows.\n"
            f"       install it from the dreamdb-py repo (uv pip install -e path/to/dreamdb-py).",
            file=sys.stderr,
        )
        return None


def _load_bundled_schema() -> dict | None:
    try:
        from importlib import resources

        ref = resources.files("dreamlake").joinpath("schemas/workflow-spec.schema.json")
        return json.loads(ref.read_text(encoding="utf-8"))
    except Exception:
        return None


def _validate_spec(spec: object) -> list[str]:
    """Validate against the bundled JSON Schema when `jsonschema` is available;
    otherwise run minimal structural checks. Returns a list of error strings
    (empty = valid)."""
    schema = _load_bundled_schema()
    if schema is not None:
        try:
            import jsonschema  # type: ignore

            validator = jsonschema.Draft202012Validator(schema)
            errors = sorted(validator.iter_errors(spec), key=lambda e: list(e.absolute_path))
            return [
                f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
                for e in errors[:20]
            ]
        except ImportError:
            pass  # fall through to structural checks

    errs: list[str] = []
    if not isinstance(spec, dict):
        return ["spec must be a JSON object"]
    if spec.get("version") != 1:
        errs.append('version: must be the literal 1')
    name = spec.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        errs.append("name: must match ^[a-z0-9][a-z0-9._-]{0,63}$")
    stages = spec.get("stages")
    if not isinstance(stages, list) or not stages:
        errs.append("stages: must be a non-empty array")
    for field in ("nodes", "edges"):
        if not isinstance(spec.get(field), list):
            errs.append(f"{field}: must be an array")
    stage_ids = {s.get("id") for s in stages or [] if isinstance(s, dict)}
    for n in spec.get("nodes") or []:
        if isinstance(n, dict) and n.get("stageId") not in stage_ids:
            errs.append(f"nodes/{n.get('id')}: stageId '{n.get('stageId')}' not in stages")
    return errs


def _workflow_schema(db):
    # One dataset PER WORKFLOW (workflows/<ns>/<name>); rows are spec versions.
    # NOTE: DreamDB modality segments must be lowercase [a-z0-9_] — no hyphens.
    return (
        db.Schema()
        .add_image("content", mime="workflow_spec")
        .add_scalar_string("workflow_id")
        .add_scalar_string("title")
        .add_scalar_int("version")
        .add_scalar_string("meta_json")
    )


def _next_version(ds, workflow_id: str) -> int:
    """Latest version for this workflow + 1 (immutable append = version history).
    A read error PROPAGATES — never silently reset to v1 and clobber history."""
    max_v = 0
    for batch in ds.iter_scalar(where_eq={"workflow_id": workflow_id}):
        for v in batch.get("version", []):
            try:
                max_v = max(max_v, int(v))
            except (TypeError, ValueError):
                pass
    return max_v + 1


# ── push ──────────────────────────────────────────────────────────────────────

def cmd_push(args: list) -> int:
    p = argparse.ArgumentParser(prog="dreamlake workflow push", add_help=True)
    p.add_argument("file")
    p.add_argument("--name", default=None)
    p.add_argument("--namespace", default=None)
    ns = p.parse_args(args)

    # The '.workflow.json' (or '.json') suffix is optional — resolve the first
    # candidate that exists, so `dreamlake workflow push <name>` just works.
    candidates = [Path(ns.file)]
    if not ns.file.endswith(".json"):
        candidates += [Path(ns.file + ".workflow.json"), Path(ns.file + ".json")]
    file_path = next((p for p in candidates if p.is_file()), None)
    if file_path is None:
        tried = " or ".join(str(p) for p in candidates)
        print(f"{RED}error:{RESET} file not found: {tried}", file=sys.stderr)
        return 1

    try:
        spec = json.loads(file_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"{RED}error:{RESET} not valid JSON: {e}", file=sys.stderr)
        return 1

    # 0) validate BEFORE any upload
    errors = _validate_spec(spec)
    if errors:
        print(f"{RED}error:{RESET} spec failed validation ({len(errors)} problem(s)):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    name = ns.name or spec.get("name")
    if not name or not NAME_RE.match(name):
        print(f"{RED}error:{RESET} workflow name must match ^[a-z0-9][a-z0-9._-]{{0,63}}$", file=sys.stderr)
        return 1

    token = ServerConfig.resolve_token()
    if not token:
        print(f"{RED}error:{RESET} not authenticated. run 'dreamlake login' first.", file=sys.stderr)
        return 1
    namespace = ns.namespace or ServerConfig.resolve_namespace()
    if not namespace:
        print(f"{RED}error:{RESET} namespace not resolved. run 'dreamlake login'.", file=sys.stderr)
        return 1

    content = json.dumps(spec, indent=2).encode("utf-8")
    meta = {
        "description": spec.get("description") or "",
        "stageCount": len(spec.get("stages") or []),
        "nodeCount": len(spec.get("nodes") or []),
        "edgeCount": len(spec.get("edges") or []),
    }

    # 1) broker: short-lived, prefix-scoped upload credentials + backend target
    import httpx
    remote = ServerConfig.remote
    try:
        r = httpx.post(
            f"{remote}/namespaces/{namespace}/workflows/{name}/spec/upload-credentials",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        r.raise_for_status()
        broker = r.json()
    except Exception as e:
        body = getattr(getattr(e, "response", None), "text", "")
        print(f"{RED}error:{RESET} could not get upload credentials: {e}", file=sys.stderr)
        if body:
            print(f"       server said: {body[:200]}", file=sys.stderr)
        return 1

    try:
        creds = broker["credentials"]
        os.environ["AWS_ACCESS_KEY_ID"] = creds["accessKeyId"]
        os.environ["AWS_SECRET_ACCESS_KEY"] = creds["secretAccessKey"]
        # Static-endpoint (MinIO) broker path returns an empty session token —
        # setting an empty AWS_SESSION_TOKEN breaks SigV4, so only set non-empty.
        session = creds.get("sessionToken") or ""
        if session:
            os.environ["AWS_SESSION_TOKEN"] = session
        else:
            os.environ.pop("AWS_SESSION_TOKEN", None)
        os.environ["AWS_REGION"] = broker.get("region", "us-east-1")
        backend = broker["backendUrl"]
        ref = broker.get("refName", REF_NAME)
    except (KeyError, TypeError) as e:
        print(f"{RED}error:{RESET} unexpected upload-credentials response (missing {e}).", file=sys.stderr)
        return 1

    # 2) write via dreamdb-py
    db = _import_dreamdb()
    if db is None:
        return 1

    schema = _workflow_schema(db)
    try:
        ds = db.Dataset.open(ref, schema, backend)
    except Exception:
        ds = db.Dataset.create(ref, schema, backend=backend)

    try:
        version = _next_version(ds, name)
    except Exception as e:
        print(f"{RED}error:{RESET} could not determine the next version: {e}", file=sys.stderr)
        return 1

    print(f"Uploading {CYAN}{file_path.name}{RESET} (workflow-spec)")
    print(f"  {DIM}namespace:{RESET} {namespace}")
    print(f"  {DIM}workflow:{RESET}  {name} v{version}")
    print(f"  {DIM}size:{RESET}      {len(content)} bytes · {meta['stageCount']} stages · {meta['nodeCount']} nodes · {meta['edgeCount']} edges")

    try:
        ds.append_many([{
            "content": content,
            "workflow_id": name,
            "title": name,
            "version": version,
            "meta_json": json.dumps(meta),
        }])
    except Exception as e:
        print(f"{RED}error:{RESET} upload failed: {e}", file=sys.stderr)
        return 1

    print(f"{GREEN}✓ Pushed:{RESET} {name} v{version}")

    # 3) register in the server catalog (metadata only — content stays in DreamDB)
    try:
        vr = httpx.post(
            f"{remote}/namespaces/{namespace}/workflows/{name}/spec",
            headers={"Authorization": f"Bearer {token}"},
            json={"version": version, "meta": meta},
            timeout=30,
        )
        vr.raise_for_status()
    except Exception as e:
        body_txt = getattr(getattr(e, "response", None), "text", "")
        print(f"{RED}error:{RESET} uploaded the content but failed to register it in the catalog: {e}", file=sys.stderr)
        if body_txt:
            print(f"       server said: {body_txt[:200]}", file=sys.stderr)
        print("       it won't show on the workflow page until you re-push.", file=sys.stderr)
        return 1

    url = f"{_web_base(remote)}/{namespace}/workflows/{name}"
    print(f"  {DIM}open:{RESET}      {CYAN}{url}{RESET}")
    return 0


# ── list ──────────────────────────────────────────────────────────────────────

def cmd_list(args: list) -> int:
    p = argparse.ArgumentParser(prog="dreamlake workflow list", add_help=True)
    p.add_argument("--namespace", default=None)
    ns = p.parse_args(args)

    token = ServerConfig.resolve_token()
    if not token:
        print(f"{RED}error:{RESET} not authenticated. run 'dreamlake login' first.", file=sys.stderr)
        return 1
    namespace = ns.namespace or ServerConfig.resolve_namespace()
    if not namespace:
        print(f"{RED}error:{RESET} namespace not resolved.", file=sys.stderr)
        return 1

    import httpx
    remote = ServerConfig.remote
    try:
        r = httpx.get(
            f"{remote}/namespaces/{namespace}/workflows",
            headers={"Authorization": f"Bearer {token}"},
            params={"pageSize": 200},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"{RED}error:{RESET} list failed: {e}", file=sys.stderr)
        return 1

    rows = data.get("workflows", [])
    if not rows:
        print(f"{DIM}no workflows in {namespace}{RESET}")
        return 0
    print(f"{BOLD}{'NAME':<32} {'SPEC':<8} {'NODES':<7} {'RUNS':<6}{RESET}")
    for w in rows:
        spec_v = f"v{w['specVersion']}" if w.get("specVersion") else "-"
        nodes = str((w.get("specMeta") or {}).get("nodeCount", "-"))
        runs = str(w.get("runCount", 0))
        print(f"{w['name']:<32} {spec_v:<8} {nodes:<7} {runs:<6}")
    return 0


def main(args: list) -> int:
    if not args or args[0] in ("-h", "--help", "help"):
        print_help()
        return 0
    sub, rest = args[0], args[1:]
    if sub == "push":
        return cmd_push(rest)
    if sub == "list":
        return cmd_list(rest)
    print(f"{RED}unknown subcommand:{RESET} {sub}", file=sys.stderr)
    print_help()
    return 1
