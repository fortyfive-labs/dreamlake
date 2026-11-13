"""Plain training code: params-proto v3 config, ordinary files out, no DreamLake import.

Writes <out>/train.jsonl and <out>/eval.jsonl (one row per log, with the absolute `step`) and
<out>/ckpt/<step>.json checkpoints. `--resume <ckpt>` continues from that checkpoint's absolute step.
This toy "PPO" is a deterministic function of lr, seed and step so the example is reproducible; it is
not a research result.
"""
import json
import math
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


if __name__ == '__main__':
    main()
