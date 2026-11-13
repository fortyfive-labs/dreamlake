"""Plain evaluation code: plays a checkpoint without training noise, writes ordinary files, no DreamLake import.

Writes <out>/summary.json (named checks, each pass, fail or inconclusive with its reason) and <out>/play.png.
The launcher runs it after a training attempt COMPLETES and records the result on that attempt; a failed check is
a result about the policy, never a failed training run. A crash (non-zero exit) is a technical evaluation error.

This toy "play" is the same deterministic function as train.py without its noise. It is not a research result.
"""
import json
import math
import sys
from pathlib import Path

from params_proto import proto

from train import write_png


@proto.prefix
class Play:
    checkpoint: str = ''          # the checkpoint file to evaluate (written by train.py)
    out: str = 'runs/eval'        # where summary.json and play.png go
    steps: int = 100_000          # the full training budget: an earlier checkpoint fails `Full-budget checkpoint`
    episodes: int = 20
    min_return: float = 2000.0    # the policy check: mean play return at least this
    fail: bool = False            # inject a crash (demonstrates a technical evaluation error)


@proto.cli
def main():
    if Play.fail:
        print('simulated evaluation crash: renderer unavailable', file=sys.stderr)
        sys.exit(4)
    ckpt = json.loads(Path(Play.checkpoint).read_text())
    rate = {1e-4: 0.6, 3e-4: 1.0, 1e-3: 0.8}.get(ckpt['lr'], 0.7)
    base = 3000 * rate * (1 - math.exp(-ckpt['step'] / 30_000)) + 40 * ckpt['seed']
    # No exploration noise: each episode differs only by its fixed start state.
    returns = [base + 25 * math.sin(episode) for episode in range(Play.episodes)]
    mean = sum(returns) / len(returns)
    checks = [
        {'name': 'Full-budget checkpoint', 'id': 'full_budget', 'result': 'pass' if ckpt['step'] >= Play.steps else 'fail',
         'value': ckpt['step'], 'threshold': f'>= {Play.steps} steps',
         'reason': f'the checkpoint is from step {ckpt["step"]} of {Play.steps}'},
        {'name': 'Finite play returns', 'id': 'finite_returns', 'result': 'pass' if all(map(math.isfinite, returns)) else 'fail',
         'value': len(returns), 'threshold': 'every episode finite', 'reason': f'{len(returns)} episodes played'},
        {'name': 'Mean play return', 'id': 'min_return', 'result': 'pass' if mean >= Play.min_return else 'fail',
         'value': round(mean, 1), 'threshold': f'>= {Play.min_return:g}',
         'reason': f'mean return {mean:.1f} over {len(returns)} episodes ({"at least" if mean >= Play.min_return else "below"} {Play.min_return:g})'},
    ]
    out = Path(Play.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'summary.json').write_text(json.dumps({'checkpoint': Play.checkpoint, 'returns': returns, 'checks': checks}, indent=2))
    write_png(out / 'play.png', returns)


if __name__ == '__main__':
    main()
