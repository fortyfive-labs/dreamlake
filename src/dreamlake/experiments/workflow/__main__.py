"""``python -m dreamlake.experiments.workflow``: scaffold the experiment template, show an experiment's contract
counts, or export its receipts. Selection is the server's; this only prints what it answers."""
import argparse
import json
import shutil
import sys
from pathlib import Path

from . import TEMPLATE, ExperimentFolder


def _folder(args):
    from dreamlake import DreamLakeClient
    return ExperimentFolder(DreamLakeClient().experiments.project(args.namespace, args.project), args.folder)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m dreamlake.experiments.workflow',
                                     description='Plan, launch and review DreamLake experiment sweeps (generate → commit → launch → review).')
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init', help='copy the generator / train / launcher / README / .dashrc template into DIR')
    init.add_argument('dir')
    for name, help_text in (('status', 'print the server contract counts and diagnostics'),
                            ('export', 'write results/runs.jsonl (one line per attempt) from the server contract')):
        command = sub.add_parser(name, help=help_text)
        command.add_argument('--namespace', required=True)
        command.add_argument('--project', required=True)
        command.add_argument('--folder', required=True, help='the experiment folder path in the project')
        if name == 'export':
            command.add_argument('--out', default='results/runs.jsonl')
    args = parser.parse_args(argv)
    if args.command == 'init':
        target = Path(args.dir)
        if target.exists() and any(target.iterdir()):
            parser.error(f'{target} is not empty')
        shutil.copytree(TEMPLATE, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', 'runs', 'results'))
        print(f'{target}: edit train.py and the sweep in *_generate.py, then: python *_generate.py && git add grids && git commit')
    elif args.command == 'status':
        contract = _folder(args).contract()
        print(json.dumps({'experiment': contract['experiment'], 'complete': contract['complete'], 'counts': contract['counts'],
                          'diagnostics': [d['code'] for d in contract['diagnostics']]}, indent=2))
    else:
        print(_folder(args).export_receipts(args.out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
