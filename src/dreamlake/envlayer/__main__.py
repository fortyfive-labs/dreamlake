"""``python3 -m dreamlake.envlayer compose <stack.json> --out <dir>``.

The CLI <-> engine boundary (RFC 0007): the DreamLake CLI resolves
registry refs into local cache paths and invokes this entry point; the
engine is network-free. The LAST stdout line is always the JSON report:

* success (exit 0): ``{"ok": true, "entry": ..., "stats": {"nbody": ..,
  "njnt": .., "nu": ..}, "warnings": [...]}``
* failure (exit 1): ``{"ok": false, "error": "...", "layer": <i|null>}``
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from .engine import compose_stack
from .stack import ComposeError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m dreamlake.envlayer",
        description=(
            "Materialize a RESOLVED dreamlake.env-layers/v2 stack into a "
            "self-contained env directory (entry XML + flat meshes/ + "
            "pinned dreamlake.layers.json). Every layer source must "
            "already be a local path -- registry refs are resolved by the "
            "DreamLake CLI, not here. Requires mujoco "
            "(pip install 'dreamlake[compose]')."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    compose = sub.add_parser(
        "compose",
        help="compose a resolved stack file into --out",
        description=(
            "Execute the stack's layers (merge/attach/override) in order "
            "over one MjSpec and write the compiled result. The last "
            "stdout line is a JSON report; exit 0 on success, 1 on any "
            "compose failure."
        ),
    )
    compose.add_argument("stack", type=Path, help="resolved stack JSON file")
    compose.add_argument(
        "--out", type=Path, required=True, metavar="DIR",
        help="output env directory (created if missing)")
    args = parser.parse_args(argv)

    try:
        report = compose_stack(args.stack, args.out)
    except ComposeError as e:
        print(json.dumps({"ok": False, "error": str(e), "layer": e.layer}))
        return 1
    except Exception as e:  # never a stack trace as the last stdout line
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}",
                          "layer": None}))
        return 1
    print(json.dumps(report.to_json()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
