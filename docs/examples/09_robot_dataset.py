"""Upload an annotated robot-training episode with dreamlake.dataset.

The real workflow this mirrors: your labeling pipeline ingests raw footage,
runs detection/segmentation, and ends up holding two Python dicts per
episode — per-frame joint positions and action segments. This script is the
last step: hand the camera file(s) plus those two dicts to the SDK, which
transcodes the video for browser playback and stores everything in one
DreamDB dataset. An episode may carry several cameras; they share one clock.

Run it three ways:

    # Self-contained demo — synthesizes a two-camera episode + mock annotations:
    python 09_robot_dataset.py --demo

    # Your own data:
    python 09_robot_dataset.py --video Ceramics.mov \
        --joints Ceramics.joints.json --subtasks Ceramics.subtasks.json

    # Platform mode — the dataset lives in the DreamLake bucket
    # (run `dreamlake login` once, or set DREAMLAKE_API_KEY):
    python 09_robot_dataset.py --demo --platform my-first-dataset

With `pip install "dreamlake[search]"` the demo also embeds the episode and
runs a natural-language search at the end.

For a local dataset, look at the result in a browser:

    npx http-server /tmp/dreamlake-datasets/demo -p 8791 --cors
    open 'http://localhost:3000/dataset-debug?space=http://localhost:8791/refs/main'

Requires: pip install dreamdb; ffmpeg on PATH.
"""

import argparse
import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

from dreamlake.dataset import Dataset, DatasetError

BACKEND = "file:///tmp/dreamlake-datasets/demo"


# ─── a stand-in for YOUR annotation pipeline ─────────────────────────────
#
# Everything in this section fakes the part you already have: something that
# looks at footage and produces joint detections and action segments. The
# only thing the SDK cares about is the SHAPE of the two dicts it returns.


def synthesize_video(path: str, seconds: float = 8.0, size: str = "1280x720") -> None:
    """A test-pattern video, so the demo needs no input files."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc2=duration={seconds}:size={size}:rate=30",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", path],
        check=True,
    )


def fake_joint_detector(width: int, height: int, fps: float, seconds: float) -> dict:
    """Mock per-frame joint detections: a 5-joint 'hand' circling the frame.

    The dict below is EXACTLY the shape `add_episode(joints_pose=...)`
    expects — wire-compatible with the web viewer's skeleton overlay:

      width/height  the pixel space the coordinates live in (the PRIMARY
                    camera's original size, upright orientation)
      src_fps       the frame rate the detector saw — frame index k maps to
                    playback time k / src_fps, so this must be the true rate
      joint_order   names, index-aligned with each detection's keypoints_2d
      bones         skeleton edges as index pairs into keypoints_2d
      frames        SPARSE map: "frame index" -> list of detections; frames
                    with nothing detected are simply absent
    """
    total = round(seconds * fps)
    frames = {}
    for k in range(total):
        t = k / fps
        cx = width / 2 + width / 3 * math.sin(2 * math.pi * 0.10 * t)
        cy = height / 2 + height / 4 * math.sin(2 * math.pi * 0.23 * t)
        # wrist + four fingertips
        pts = [[cx, cy]] + [
            [cx + 60 * math.cos(a + t), cy - 70 + 15 * math.sin(3 * t)]
            for a in (-0.6, -0.2, 0.2, 0.6)
        ]
        frames[str(k)] = [{
            "is_right": 1,
            "det_conf": 0.9,
            "keypoints_2d": [[round(x, 1), round(y, 1)] for x, y in pts],
        }]
    return {
        "width": width, "height": height, "src_fps": fps,
        "total_frames": total,
        "joint_order": ["wrist", "index_tip", "middle_tip", "ring_tip", "pinky_tip"],
        "bones": [[0, 1], [0, 2], [0, 3], [0, 4]],
        "frames": frames,
    }


def fake_segmenter(seconds: float) -> dict:
    """Mock action segmentation — the shape `add_episode(subtasks=...)` expects.

    start_sec/end_sec are seconds on the episode's own clock; gaps between
    segments are fine (nothing is rendered there).
    """
    labels = ["reach for object", "grasp object", "move to target", "release"]
    step = seconds / len(labels)
    return {
        "task": "demo pick-and-place",
        "labeled_subtasks": [
            {"start_sec": round(i * step, 2),
             "end_sec": round((i + 1) * step, 2),
             "subtask": label}
            for i, label in enumerate(labels)
        ],
    }


# ─── the actual SDK usage ────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--demo", action="store_true",
                    help="synthesize a two-camera episode + mock annotations")
    ap.add_argument("--video", help="source video (any codec) — single camera")
    ap.add_argument("--joints", help="joints_pose JSON file (optional)")
    ap.add_argument("--subtasks", help="subtasks JSON file (optional)")
    ap.add_argument("--id", dest="episode_id", help="stable episode id (default: filename stem)")
    ap.add_argument("--task", help="task label for the episode meta (nothing is inferred)")
    ap.add_argument("--backend", default=BACKEND, help=f"dataset location (default: {BACKEND})")
    ap.add_argument("--platform", metavar="NAME",
                    help="store the dataset in the DreamLake platform bucket under NAME "
                         "(needs `dreamlake login` or DREAMLAKE_API_KEY)")
    args = ap.parse_args()

    # 1. Open the dataset, creating it on first use. One dataset = one task's
    #    worth of episodes. Each camera track keeps one aspect ratio, but
    #    different cameras can differ (head 16:9 + wrist 4:3 is fine).
    if args.platform:
        try:
            ds = Dataset.open(args.platform)
            print(f"opened platform dataset '{args.platform}'")
        except DatasetError:
            ds = Dataset.create(args.platform)
            print(f"created platform dataset '{args.platform}'")
    else:
        try:
            ds = Dataset.open(backend=args.backend)
            print(f"opened dataset at {args.backend}")
        except DatasetError:
            ds = Dataset.create(backend=args.backend)
            print(f"created dataset at {args.backend}")

    # 2. Produce (or load) the camera file(s) and annotations.
    tmpdir = None
    if args.demo:
        tmpdir = tempfile.mkdtemp(prefix="dreamlake-demo-")
        seconds = 8.0
        head = str(Path(tmpdir) / "head.mp4")
        wrist = str(Path(tmpdir) / "wrist.mp4")
        print("synthesizing a two-camera test episode …")
        synthesize_video(head, seconds, size="1280x720")
        synthesize_video(wrist, seconds, size="640x480")  # per-camera aspect is fine
        videos = {"head": head, "wrist": wrist}
        # Here your real pipeline would run detection + segmentation. The SDK
        # takes their in-memory dicts directly — no intermediate files needed.
        # A bare joints doc binds to the PRIMARY camera (first in the dict:
        # "head"); pass {"head": doc1, "wrist": doc2} to annotate several —
        # each lands in that camera's joints_pose__{camera} track, which is
        # how the viewer knows which video to overlay it on.
        joints = fake_joint_detector(1280, 720, 30.0, seconds)
        subtasks = fake_segmenter(seconds)
        task = args.task or "demo pick-and-place"
        episode_id = args.episode_id or f"demo-{len(ds.episodes())}"
    else:
        if not args.video:
            ap.error("--video is required (or use --demo)")
        videos = args.video               # a single path = one camera, "main"
        joints = args.joints              # a path also works — add_episode reads it
        subtasks = args.subtasks
        task = args.task
        episode_id = args.episode_id or Path(args.video).stem

    # 3. Upload. One call: transcodes every camera for browser playback and
    #    commits annotations + metadata (pass raw=True to also archive the
    #    lossless originals). All cameras land on the episode's shared clock.
    #    Labels are explicit — meta={"task": ...}; nothing is inferred.
    #    The return is the episode's HANDLE; the ingest summary is on .report.
    print(f"adding episode '{episode_id}' …")
    epo = ds.add_episode(
        videos,
        episode_id=episode_id,
        joints_pose=joints,
        subtasks=subtasks,
        meta={"task": task} if task else None,
    )
    report = epo.report
    frag_note = ", ".join(
        f"{cam}: {rep['preview']['fragments']} fragments"
        for cam, rep in report["cameras"].items()
    )
    annotated = sum(j["annotated_frames"] for j in report.get("joints_pose", {}).values())
    print(f"  slot {epo.gid}  ({frag_note})"
          + (f", {annotated} annotated frames" if annotated else "")
          + (f", {report['subtasks']['segments']} segments"
             if "subtasks" in report else ""))

    # 3b. Episodes are extensible through their handle: late cameras via
    #     epo.add_cameras (video is add-only), annotation/label revisions via
    #     epo.revise — atomic, one committed row, history kept.
    if args.demo:
        epo.revise(meta={"scene": "demo-bench"})
        print("  revised: scene='demo-bench' via epo.revise")

    # 4. Read back — the same calls a viewer or a training loader starts with.
    #    ds.episodes() returns Episode handles; ds.episode(id) fetches one.
    print("\nepisodes in this dataset:")
    for e in ds.episodes():
        cams = ", ".join(
            f"{name} {c.get('width')}x{c.get('height')}"
            for name, c in e.cameras.items()
        )
        print(f"  [{e.gid}] {e.episode_id}  ({cams})"
              f"  {e.duration_s:.1f}s  task={e.task}")

    info = epo.info()
    print(f"\ninfo('{episode_id}'): "
          f"{json.dumps({k: v for k, v in info.items() if k not in ('anchor', '_rev', 'cameras')})}")

    # 5. Make it searchable and search it — needs `pip install "dreamlake[search]"`.
    #    epo.embed() = sample this episode's primary-camera frames -> CLIP +
    #    segment texts -> BGE + upload (ds.embed_episodes() is the all-episode
    #    sweep). Vectors are searchable the moment they land; no build step.
    try:
        counts = epo.embed()
        print(f"\nembedded: {counts}")
        for q in ("reach for the object", "release"):
            hits = ds.search(q, top_k=3)
            print(f"search({q!r}):")
            for h in hits:
                extra = f'  "{h["subtask"]}"' if "subtask" in h else ""
                print(f'  {h["episode_id"]} @ {h["time_sec"]:.1f}s  [{h["source"]}]{extra}')
    except DatasetError as e:
        print(f"\n(skipping search demo: {e})")

    # 6. Visualize.
    local_dir = args.backend.removeprefix("file://")
    print(
        "\nTo see it in the browser:\n"
        f"  npx http-server {local_dir} -p 8791 --cors\n"
        "  open 'http://localhost:3000/dataset-debug"
        "?space=http://localhost:8791/refs/main'\n"
        "(run `pnpm dev` in dreamlake-ai for the viewer at :3000)"
    )

    if tmpdir:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
