"""Generator: defines the combinations and writes the committed plan, grids/lr.jsonl.

    python ppo_lr_generate.py && git add grids/lr.jsonl && git commit -m "plan: ppo lr x seed"

Each row freezes the full training config (defaults plus the swept values), so the plan stays the plan even if
a default in train.py changes later. The launcher never redefines combinations; it selects rows by slug.
"""
from params_proto import Sweep

from dreamlake.experiments.workflow import grid_rows, write_grid
from train import Train

# Execution-only fields (output dir, resume checkpoint, fault injection) are not part of a configuration.
PLANNED = ('lr', 'seed', 'steps', 'log_every', 'eval_every')
defaults = {f'train.{key}': value for key, value in dict(Train).items() if key in PLANNED}

with Sweep(Train).product as sweep:
    Train.lr = [1e-4, 3e-4, 1e-3]
    Train.seed = [0, 1, 2]

if __name__ == '__main__':
    rows = grid_rows(({**defaults, **override} for override in sweep.list), changed=['train.lr'], seed_key='train.seed')
    print(f"{len(rows)} rows -> {write_grid('grids/lr.jsonl', rows)}")
