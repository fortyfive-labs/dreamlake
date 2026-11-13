"""Plain training code: params-proto v3 config, ordinary files out, no DreamLake import.

Writes <out>/train.jsonl and <out>/eval.jsonl (one row per log, with the absolute `step`) and
<out>/ckpt/<step>.json checkpoints. `--resume <ckpt>` continues from that checkpoint's absolute step.
This toy "PPO" is a deterministic function of lr, seed and step so the example is reproducible; it is
not a research result.
"""
import json
import math
import struct
import zlib
import random
import sys
from pathlib import Path

from params_proto import proto


@proto.prefix
class Train:
    lr: float = 3e-4         # learning rate
    seed: int = 0            # random seed
    steps: int = 100_000     # full training budget, in environment steps
    log_every: int = 2_000
    eval_every: int = 10_000
    out: str = 'runs/out'    # where plain files go (the launcher uploads them)
    resume: str = ''         # checkpoint to continue from (absolute step inside)
    fail_at: int = -1        # inject a crash at this step (demonstrates a failed attempt)


def curve(step: int, lr: float, seed: int, rng: random.Random) -> float:
    rate = {1e-4: 0.6, 3e-4: 1.0, 1e-3: 0.8}.get(lr, 0.7)
    return 3000 * rate * (1 - math.exp(-step / 30_000)) + rng.gauss(0, 60) + 40 * seed


def write_png(path: Path, values: list, width: int = 240, height: int = 90):
    """A tiny line plot of `values` as a PNG (standard library only): the example's stand-in for a rollout frame."""
    lo, hi = min(values), max(values)
    rows = [[255] * width for _ in range(height)]
    points = [(int(i * (width - 1) / max(1, len(values) - 1)), height - 1 - int((v - lo) / ((hi - lo) or 1) * (height - 1)))
              for i, v in enumerate(values)]
    for (x0, y0), (x1, y1) in zip(points, points[1:] or points):
        for x in range(x0, x1 + 1):
            y = y0 + (y1 - y0) * (x - x0) // max(1, x1 - x0)
            for dy in (-1, 0, 1):
                if 0 <= y + dy < height:
                    rows[y + dy][x] = 30
    raw = b''.join(b'\x00' + bytes(row) for row in rows)
    chunk = lambda kind, data: struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0)) +
                     chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


@proto.cli
def main():
    out = Path(Train.out)
    (out / 'ckpt').mkdir(parents=True, exist_ok=True)
    start = 0
    if Train.resume:
        start = json.loads(Path(Train.resume).read_text())['step']
    rng = random.Random(f'{Train.seed}-{start}')
    with open(out / 'train.jsonl', 'a') as train, open(out / 'eval.jsonl', 'a') as evals:
        for step in range(start + Train.log_every, Train.steps + 1, Train.log_every):
            if 0 <= Train.fail_at <= step:
                print(f'simulated crash at step {step}', file=sys.stderr)
                sys.exit(3)
            ret = curve(step, Train.lr, Train.seed, rng)
            train.write(json.dumps({'step': step, 'loss': 1 / (1 + step / 5000) + rng.random() * 0.01, 'return': ret}) + '\n')
            if step % Train.eval_every == 0:
                evals.write(json.dumps({'step': step, 'return': curve(step, Train.lr, Train.seed, random.Random(step))}) + '\n')
                (out / 'ckpt' / f'{step}.json').write_text(json.dumps({'step': step, 'lr': Train.lr, 'seed': Train.seed}))
            train.flush(); evals.flush()
    # Reached the full budget: a synthetic "rollout" image of the eval curve (media/rollout.png).
    evals_rows = [json.loads(line) for line in (out / 'eval.jsonl').read_text().splitlines() if line.strip()]
    if evals_rows and evals_rows[-1]['step'] >= Train.steps:
        write_png(out / 'media' / 'rollout.png', [row['return'] for row in evals_rows])


if __name__ == '__main__':
    main()
