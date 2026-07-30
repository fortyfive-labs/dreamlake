"""Upload an annotated robot-training video with dreamlake.dataset.

The real workflow this mirrors: your labeling pipeline ingests raw footage,
runs detection/segmentation, and ends up holding two Python dicts per video —
per-frame joint positions and action segments. This script is the last step:
hand the video file plus those two dicts to the SDK, which transcodes the
video for browser playback and stores everything in one DreamDB dataset.

Run it three ways:

    # Self-contained demo — synthesizes a test video and mock annotations:
    python 09_robot_dataset.py --demo

    # Your own data:
    python 09_robot_dataset.py --video Ceramics.mov \
        --joints Ceramics.joints.json --subtasks Ceramics.subtasks.json

    # Platform mode — the dataset lives in the DreamLake bucket
    # (run `dreamlake login` once, or set DREAMLAKE_API_KEY):
    python 09_robot_dataset.py --demo --platform my-first-dataset

With `pip install "dreamlake[search]"` the demo also embeds the video and
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
# looks at a video and produces joint detections and action segments. The
# only thing the SDK cares about is the SHAPE of the two dicts it returns.


def synthesize_video(path: str, seconds: float = 8.0) -> None:
    """A test-pattern video, so the demo needs no input files."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc2=duration={seconds}:size=1280x720:rate=30",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", path],
        check=True,
    )


def fake_joint_detector(width: int, height: int, fps: float, seconds: float) -> dict:
    """Mock per-frame joint detections: a 5-joint 'hand' circling the frame.

    The dict below is EXACTLY the shape `add_video(joints_pose=...)` expects —
    wire-compatible with the web viewer's skeleton overlay:

      width/height  the pixel space the coordinates live in (the ORIGINAL
                    video's size, upright orientation)
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
    """Mock action segmentation — the shape `add_video(subtasks=...)` expects.

    start_sec/end_sec are seconds on the video's own clock; gaps between
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
    ap.add_argument("--demo", action="store_true", help="synthesize a video + mock annotations")
    ap.add_argument("--video", help="source video (any codec)")
    ap.add_argument("--joints", help="joints_pose JSON file (optional)")
    ap.add_argument("--subtasks", help="subtasks JSON file (optional)")
    ap.add_argument("--id", dest="video_id", help="stable video id (default: filename stem)")
    ap.add_argument("--backend", default=BACKEND, help=f"dataset location (default: {BACKEND})")
    ap.add_argument("--platform", metavar="NAME",
                    help="store the dataset in the DreamLake platform bucket under NAME "
                         "(needs `dreamlake login` or DREAMLAKE_API_KEY)")
    args = ap.parse_args()

    # 1. Open the dataset, creating it on first use. One dataset = one task's
    #    worth of videos; all of them must share an aspect ratio.
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

    # 2. Produce (or load) the video and its annotations.
    tmpdir = None
    if args.demo:
        tmpdir = tempfile.mkdtemp(prefix="dreamlake-demo-")
        video = str(Path(tmpdir) / "demo_episode.mp4")
        seconds = 8.0
        print("synthesizing a test video …")
        synthesize_video(video, seconds)
        # Here your real pipeline would run detection + segmentation. The SDK
        # takes their in-memory dicts directly — no intermediate files needed.
        joints = fake_joint_detector(1280, 720, 30.0, seconds)
        subtasks = fake_segmenter(seconds)
        video_id = args.video_id or f"demo-{len(ds.videos())}"
    else:
        if not args.video:
            ap.error("--video is required (or use --demo)")
        video = args.video
        joints = args.joints        # a path also works — add_video reads it
        subtasks = args.subtasks
        video_id = args.video_id or Path(video).stem

    # 3. Upload. One call: transcodes for browser playback, stores the
    #    lossless original alongside, and commits annotations + metadata.
    print(f"adding '{video_id}' …")
    result = ds.add_video(
        video,
        video_id=video_id,
        joints_pose=joints,
        subtasks=subtasks,
    )
    print(f"  slot {result['gid']}, "
          f"{result['video_preview']['fragments']} playback fragments"
          + (f", {result['joints_pose']['annotated_frames']} annotated frames"
             if "joints_pose" in result else "")
          + (f", {result['subtasks']['segments']} segments"
             if "subtasks" in result else ""))

    # 4. Read back — the same calls a viewer or a training loader starts with.
    print("\nvideos in this dataset:")
    for row in ds.videos():
        print(f"  [{row['gid']}] {row['video_id']}"
              f"  {row.get('width')}x{row.get('height')}"
              f"  {row.get('duration_s', 0):.1f}s"
              f"  task={row.get('task')}")

    info = ds.info(video_id)
    print(f"\ninfo('{video_id}'): {json.dumps({k: v for k, v in info.items() if k != 'anchor'})}")

    # 5. Make it searchable and search it — needs `pip install "dreamlake[search]"`.
    #    embed_videos = sample frames -> CLIP + segment texts -> BGE + upload.
    #    Vectors are searchable the moment they land; there is no build step.
    try:
        report = ds.embed_videos(video_id=video_id)
        print(f"\nembedded: {report}")
        for q in ("reach for the object", "release"):
            hits = ds.search(q, top_k=3)
            print(f"search({q!r}):")
            for h in hits:
                extra = f'  "{h["subtask"]}"' if "subtask" in h else ""
                print(f'  {h["video_id"]} @ {h["time_sec"]:.1f}s  [{h["source"]}]{extra}')
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
