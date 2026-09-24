"""Batch thumbnail renderer -- the CLI's push-time render step.

The dreamlake CLI (TypeScript) shells out to::

    python -m dreamlake.assets_tools.render_thumbnails \\
        --jobs jobs.json --out-dir DIR [--size 640]

``jobs.json`` is a JSON array of job records::

    [{"id": "<asset id>", "entry": "/abs/path/to/entry.xml",
      "kind": "mjcf"}]

* ``id`` -- the asset id; the output lands at ``<out-dir>/<id>.webp``
  (ids must match the manifest asset-id charset, so they are safe
  filenames).
* ``entry`` -- absolute path to the asset's entry file in the user's
  source dir.
* ``kind`` -- only ``"mjcf"`` renders today (via
  :func:`~dreamlake.assets_tools.thumbnails.render_thumbnail`); every
  other kind is reported under ``skipped``.
* ``category`` (optional) -- picks the per-category view angle via
  :func:`~dreamlake.assets_tools.thumbnails.view_angles`. Unknown keys
  are ignored.

stdout carries EXACTLY ONE JSON line, printed at the end -- the
contract the CLI parses::

    {"rendered": ["id", ...],
     "skipped": [{"id": ..., "reason": ...}, ...],
     "failed":  [{"id": ..., "reason": ...}, ...]}

All human chatter (per-job progress, render warnings) goes to stderr.
Per-job failures never abort the batch; the exit code is 0 unless the
jobs file itself is unreadable (missing/invalid JSON/not an array).
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

from .manifest import _ASSET_ID_RE
from .thumbnails import THUMBNAIL_MAX_DIM, render_thumbnail, view_angles


def render_jobs(
    jobs: list, out_dir: str | Path, size: int = THUMBNAIL_MAX_DIM,
) -> dict:
    """Render each job to ``<out_dir>/<id>.webp``; never raises per job.

    Returns the report dict printed by :func:`main` (see the module
    docstring for the exact shape).
    """
    out_dir = Path(out_dir)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        # per-job renders will fail (and report) individually
        print(f"warning: cannot create {out_dir}: {e}", file=sys.stderr)

    rendered: list[str] = []
    skipped: list[dict] = []
    failed: list[dict] = []
    total = len(jobs)
    for i, job in enumerate(jobs):
        if not isinstance(job, dict):
            failed.append({"id": "", "reason": f"jobs[{i}]: not an object"})
            continue
        asset_id = job.get("id")
        if not isinstance(asset_id, str) or not asset_id:
            failed.append(
                {"id": "", "reason": f"jobs[{i}]: missing or invalid id"})
            continue
        prefix = f"[{i + 1}/{total}] {asset_id}"
        if not _ASSET_ID_RE.match(asset_id):
            failed.append({"id": asset_id, "reason": "invalid asset id"})
            print(f"{prefix}: FAILED (invalid asset id)", file=sys.stderr)
            continue
        kind = job.get("kind")
        if kind != "mjcf":
            reason = f"unsupported kind {kind!r} (only mjcf renders today)"
            skipped.append({"id": asset_id, "reason": reason})
            print(f"{prefix}: skipped ({reason})", file=sys.stderr)
            continue
        entry = job.get("entry")
        if not isinstance(entry, str) or not entry:
            failed.append(
                {"id": asset_id, "reason": "missing or invalid entry"})
            print(f"{prefix}: FAILED (missing entry)", file=sys.stderr)
            continue
        entry_path = Path(entry)
        if not entry_path.is_file():
            reason = f"entry not found: {entry}"
            failed.append({"id": asset_id, "reason": reason})
            print(f"{prefix}: FAILED ({reason})", file=sys.stderr)
            continue

        kwargs: dict = {}
        category = job.get("category")
        if isinstance(category, str) and category:
            azimuth, elevation = view_angles(category)
            kwargs = {"azimuth": azimuth, "elevation": elevation}

        out_path = out_dir / f"{asset_id}.webp"
        # render_thumbnail never raises; it warns and returns False.
        # Capture the warning so the failure reason reaches the report
        # (and still echo it to stderr for humans).
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ok = render_thumbnail(entry_path, out_path, size=size, **kwargs)
        for w in caught:
            print(f"{prefix}: warning: {w.message}", file=sys.stderr)
        if ok:
            rendered.append(asset_id)
            print(f"{prefix}: ok -> {out_path}", file=sys.stderr)
        else:
            reason = str(caught[-1].message) if caught else "render failed"
            failed.append({"id": asset_id, "reason": reason})
            print(f"{prefix}: FAILED", file=sys.stderr)

    return {"rendered": rendered, "skipped": skipped, "failed": failed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m dreamlake.assets_tools.render_thumbnails",
        description=(
            "Render asset thumbnails in batch for the dreamlake CLI: "
            "reads a jobs.json array of {id, entry, kind} records, "
            "writes <out-dir>/<id>.webp per rendered job, and prints "
            "one JSON report line to stdout."
        ),
    )
    parser.add_argument(
        "--jobs", type=Path, required=True,
        help="path to the jobs.json array")
    parser.add_argument(
        "--out-dir", type=Path, required=True,
        help="directory receiving <id>.webp thumbnails")
    parser.add_argument(
        "--size", type=int, default=THUMBNAIL_MAX_DIM,
        help="thumbnail max dimension in pixels (rendered at 2x and "
             f"downscaled; default: {THUMBNAIL_MAX_DIM})")
    args = parser.parse_args(argv)

    try:
        jobs = json.loads(args.jobs.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"error: cannot read jobs file {args.jobs}: {e}",
              file=sys.stderr)
        return 1
    if not isinstance(jobs, list):
        print(f"error: jobs file {args.jobs} must hold a JSON array",
              file=sys.stderr)
        return 1

    report = render_jobs(jobs, args.out_dir, size=args.size)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
