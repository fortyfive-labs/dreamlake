#!/usr/bin/env python
"""Create a DreamLake dataset and upload a short test clip with annotations.

Uses a real annotated capture if you have one in ~/Downloads (trimmed to a few
seconds so the upload stays tiny), and otherwise synthesizes a stand-in. Then
it creates a platform dataset and uploads the clip + joint-pose + subtask
annotations, so you can open it in the web app.

    # from the dreamlake-py checkout, using its venv (which has the SDK):
    .venv/bin/python docs/examples/upload_test_dataset.py

    # options:
    .venv/bin/python docs/examples/upload_test_dataset.py --name my-dataset --seconds 5
    .venv/bin/python docs/examples/upload_test_dataset.py --local /tmp/ds   # no login
    .venv/bin/python docs/examples/upload_test_dataset.py --search          # + embed/search

Kept short on purpose: only the first --seconds of the source are trimmed and
uploaded, so there's no large-file CPU/bandwidth cost. Auth uses your
`dreamlake login` session automatically (or set DREAMLAKE_API_KEY).
Requires ffmpeg on PATH.
"""

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# A real annotated capture, if present. Trimmed — never uploaded whole.
DOWNLOADS = Path.home() / "Downloads"
REAL_VIDEO = DOWNLOADS / "claru_ego__Ceramics.mov"
REAL_JOINTS = DOWNLOADS / "claru_ego__Ceramics.json"
REAL_SUBTASKS = DOWNLOADS / "ours_subtasks_713488.json"  # a stand-in segmentation


# ── make the SDK find your existing `dreamlake login` ────────────────────────
def _adopt_cli_login() -> None:
    """Bridge the CLI's saved token (~/.config/dreamlake/auth.yml) into the env
    when the SDK's own store is empty, so this script just works after
    `dreamlake login`."""
    if os.environ.get("DREAMLAKE_API_KEY"):
        return
    try:
        from dreamlake import _session

        if _session.get_token_or_none():
            return
    except Exception:
        pass
    auth = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "dreamlake" / "auth.yml"
    if not auth.exists():
        return
    try:
        import yaml

        doc = yaml.safe_load(auth.read_text()) or {}
        env = (doc.get("envs") or {}).get(doc.get("current"))
        if env and env.get("token"):
            os.environ["DREAMLAKE_API_KEY"] = env["token"]
            if env.get("server") and not os.environ.get("DREAMLAKE_REMOTE"):
                os.environ["DREAMLAKE_REMOTE"] = env["server"]
    except Exception:
        pass


# ── real source: trim the video, slice the annotations to match ──────────────
def prepare_real(tmp: str, seconds: float):
    """Trim REAL_VIDEO to `seconds` and slice its annotations to that window.
    Returns (video_path, joints_dict, subtasks_dict) or None if not available."""
    if not (REAL_VIDEO.exists() and REAL_JOINTS.exists()):
        return None

    out = str(Path(tmp) / "trimmed.mp4")
    # Re-encode the trim (not -c copy) so the cut lands cleanly and fps/size are
    # preserved for the annotation alignment. A few seconds of 1080p is trivial.
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(REAL_VIDEO), "-t", str(seconds),
         "-c:v", "libx264", "-preset", "veryfast", "-an", out],
        check=True,
    )

    full = json.loads(REAL_JOINTS.read_text())
    fps = float(full.get("src_fps", 30.0))
    cutoff = seconds * fps
    frames = {k: v for k, v in full.get("frames", {}).items() if int(k) < cutoff}
    joints = {**full, "frames": frames, "total_frames": len(frames)}

    subtasks = None
    if REAL_SUBTASKS.exists():
        sub = json.loads(REAL_SUBTASKS.read_text())
        kept = []
        for seg in sub.get("labeled_subtasks", []):
            if seg["start_sec"] < seconds:
                kept.append({**seg, "end_sec": min(seg["end_sec"], seconds)})
        subtasks = {**sub, "labeled_subtasks": kept}
    return out, joints, subtasks


# ── synthetic fallback (no Downloads files needed) ───────────────────────────
def prepare_synth(tmp: str, seconds: float):
    w, h, fps = 1280, 720, 30.0
    out = str(Path(tmp) / "synth.mp4")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", f"testsrc2=duration={seconds}:size={w}x{h}:rate=30",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", out],
        check=True,
    )
    total = round(seconds * fps)
    frames = {}
    for k in range(total):
        t = k / fps
        cx = w / 2 + w / 3 * math.sin(2 * math.pi * 0.10 * t)
        cy = h / 2 + h / 4 * math.sin(2 * math.pi * 0.23 * t)
        pts = [[cx, cy]] + [
            [cx + 60 * math.cos(a + t), cy - 70 + 15 * math.sin(3 * t)]
            for a in (-0.6, -0.2, 0.2, 0.6)
        ]
        frames[str(k)] = [{"is_right": 1, "det_conf": 0.9,
                           "keypoints_2d": [[round(x, 1), round(y, 1)] for x, y in pts]}]
    joints = {
        "width": w, "height": h, "src_fps": fps, "total_frames": total,
        "joint_order": ["wrist", "index_tip", "middle_tip", "ring_tip", "pinky_tip"],
        "bones": [[0, 1], [0, 2], [0, 3], [0, 4]], "frames": frames,
    }
    labels = ["reach for object", "grasp object", "move to target", "release"]
    step = seconds / len(labels)
    subtasks = {"task": "demo pick-and-place", "labeled_subtasks": [
        {"start_sec": round(i * step, 2), "end_sec": round((i + 1) * step, 2), "subtask": s}
        for i, s in enumerate(labels)]}
    return out, joints, subtasks


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="test-dataset", help="dataset name (default: test-dataset)")
    ap.add_argument("--episode-id", default=None, help="id for this episode (default: auto)")
    ap.add_argument("--seconds", type=float, default=5.0, help="how much to trim + upload (default: 5)")
    ap.add_argument("--local", metavar="DIR", default=None,
                    help="write to a local folder instead of the platform")
    ap.add_argument("--search", action="store_true", help="also embed + run a sample search")
    args = ap.parse_args()

    _adopt_cli_login()
    from dreamlake.dataset import VideoAnnotationDataset as Dataset, DatasetError

    # 1. Open or create the dataset.
    if args.local:
        backend = f"file://{Path(args.local).resolve()}"
        try:
            ds = Dataset.open(backend=backend)
            print(f"opened local dataset at {backend}")
        except DatasetError:
            ds = Dataset.create(backend=backend)
            print(f"created local dataset at {backend}")
    else:
        try:
            ds = Dataset.open(args.name)
            print(f"opened dataset '{args.name}' on the platform")
        except DatasetError:
            try:
                ds = Dataset.create(args.name)
            except DatasetError as e:
                print(f"error: {e}", file=sys.stderr)
                if "auth" in str(e).lower() or "login" in str(e).lower():
                    print("→ run `dreamlake login` first, or set DREAMLAKE_API_KEY.", file=sys.stderr)
                return 1
            print(f"created dataset '{args.name}' on the platform")

    # 2. Get a short clip + its annotations (real if available, else synthetic).
    tmp = tempfile.mkdtemp(prefix="dl-test-")
    prepared = prepare_real(tmp, args.seconds)
    if prepared:
        print(f"using a real capture, trimmed to {args.seconds:g}s ({REAL_VIDEO.name})")
    else:
        prepared = prepare_synth(tmp, args.seconds)
        print(f"synthesized a {args.seconds:g}s test clip (no ~/Downloads capture found)")
    video, joints, subtasks = prepared
    size_mb = os.path.getsize(video) / 1e6
    episode_id = args.episode_id or f"clip-{len(ds.episodes())}"

    # 3. Upload — one call transcodes for browser playback and stores
    #    everything, returning the episode's handle (ingest summary on
    #    .report). A single path = one camera ("main"); pass a dict of
    #    camera name -> path for multi-camera episodes. Labels are explicit:
    #    meta={"task": ...} — nothing is inferred from the annotations.
    print(f"uploading '{episode_id}' ({size_mb:.1f} MB clip) …")
    epo = ds.add_episode(video, episode_id=episode_id, joints_pose=joints, subtasks=subtasks,
                         meta={"task": (subtasks or {}).get("task")})
    report = epo.report
    annotated = sum(j["annotated_frames"] for j in report.get("joints_pose", {}).values())
    print(f"  slot {epo.gid}: "
          f"{report['cameras']['main']['preview']['fragments']} playback fragments, "
          f"{annotated} annotated frames, "
          f"{report.get('subtasks', {}).get('segments', 0)} subtasks")

    # 4. Optional: make it searchable (one episode: the handle's embed;
    #    ds.embed_episodes() sweeps them all).
    if args.search:
        print("embedding for search …")
        epo.embed()
        for q in ("a hand reaching", "shaping clay"):
            hits = ds.search(q, top_k=2)
            print(f"  search({q!r}): " +
                  ", ".join(f"{h['episode_id']}@{h['time_sec']:.0f}s" for h in hits))

    # 5. Recap — ds.episodes() returns Episode handles.
    print("\ndataset now holds:")
    for e in ds.episodes():
        cams = ", ".join(f"{n} {c.get('width')}x{c.get('height')}"
                         for n, c in e.cameras.items())
        print(f"  [{e.gid}] {e.episode_id}  ({cams})  {e.duration_s:.1f}s")

    if args.local:
        print(f"\nserve it:  npx http-server {args.local} -p 8791 --cors")
        print("view it:   http://localhost:3000/dataset-debug?space=http://localhost:8791/refs/main")
    else:
        print(f"\nOpen the web app → Datasets → '{args.name}' to view it.")

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
