"""Analysis: reads what the server answers for the experiment, judges YOUR criteria, writes a verdict-first report
with figures, and publishes it into the experiment folder's analysis/.

    python analysis.py                         # analyse and publish
    python analysis.py --analysis.local        # write analysis/out/<id>/ only, publish nothing

Exit status: 0 every criterion passed; 1 a criterion failed or could not be judged; 2 the analysis itself failed
(a technical error, recorded as such when it can still be published). The criteria below are this experiment's
own reading aids, not a significance test. The data of this template is SYNTHETIC: a report on it demonstrates
the workflow, never a research result.

Nothing here decides which attempts count: the contract (chains, evaluations) and the series (the exact-budget
endpoint) come from the server. The server fingerprints them as `inputs`; the report copies that fingerprint, so a
later attempt, resume, metric write or re-evaluation marks this report stale.
"""
import json
import shlex
import sys
import time
import traceback
from pathlib import Path

from params_proto import proto

from dreamlake import DreamLakeClient
from dreamlake.experiments.workflow import ExperimentFolder, collect_provenance, new_attempt_id

HERE = Path(__file__).resolve().parent


@proto.prefix
class Analysis:
    namespace: str = 'expwf'       # the same experiment as the launcher's --launch.namespace/project/folder
    project: str = 'rl-lab'
    folder: str = 'ppo/lr-sweep'
    metric: int = 0                # the manifest metric judged (0: eval return)
    local: bool = False            # write the report locally only (params-proto flags are switches: present = true)


# ── Criteria: each returns {name, id, result: pass|fail|inconclusive, value, threshold, reason} ──────────────────

def evaluations_pass(contract):
    """Every planned run has a current evaluation, and every one of them passes."""
    counts = contract['evaluations']
    planned = contract['counts']['planned']
    missing = {state: n for state, n in counts.items() if state not in ('passed', 'failed') and n}
    result = 'fail' if counts['failed'] else 'inconclusive' if missing or planned is None else 'pass'
    detail = ', '.join(f'{n} {state.replace("_", " ")}' for state, n in missing.items())
    return {'name': 'Every planned run passes its evaluation', 'id': 'evaluations_pass', 'result': result,
            'value': f'{counts["passed"]} of {planned}', 'threshold': 'all planned runs pass',
            'reason': f'{counts["passed"]} passed, {counts["failed"]} failed' + (f'; {detail}' if detail else '')}


def baseline_beats_others(contract, series):
    """The baseline's mean at the full budget beats every other config by more than the widest seed range."""
    endpoint = series.get('endpoint')
    name = {'name': 'Baseline leads by more than the seed spread', 'id': 'baseline_beats_others'}
    if not endpoint:
        return {**name, 'result': 'inconclusive', 'value': None, 'threshold': 'manifest budget set', 'reason': 'the manifest sets no budget'}
    groups = {g['config']: g for g in endpoint['configs']}
    base = next((g for g in endpoint['configs'] if g['baseline']), None)
    thin = [g['config'] for g in endpoint['configs'] if g['n'] < 2]
    if base is None or thin:
        return {**name, 'result': 'inconclusive', 'value': None, 'threshold': '>= 2 seeds per config at the budget',
                'reason': 'no baseline config' if base is None else f'fewer than 2 seeds at the budget: {", ".join(thin)}'}
    spread = max(max(v['value'] for v in g['values']) - min(v['value'] for v in g['values']) for g in groups.values())
    runner = max((g for g in groups.values() if not g['baseline']), key=lambda g: g['mean'])
    gap = base['mean'] - runner['mean']
    return {**name, 'result': 'pass' if gap > spread else 'fail', 'value': round(gap, 1), 'threshold': f'> {spread:.1f} (widest seed range)',
            'reason': f'baseline mean {base["mean"]:.1f} vs next best {runner["mean"]:.1f}: lead {gap:.1f} '
                      f'{"exceeds" if gap > spread else "does not exceed"} the widest seed range {spread:.1f}'}


# ── Figures (standard library SVG: no plotting dependency) ───────────────────────────────────────────────────────

def svg_endpoint(series, path):
    configs = series['endpoint']['configs'] if series.get('endpoint') else []
    values = [v['value'] for g in configs for v in g['values']] or [0, 1]
    lo, hi = min(values), max(values)
    w, h, left, top, bottom = 520, 260, 60, 30, 50
    y = lambda v: top + (hi - v) / ((hi - lo) or 1) * (h - top - bottom)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" font-family="sans-serif">',
             f'<rect width="{w}" height="{h}" fill="#fff"/>',
             f'<text x="{left}" y="18" font-size="12">{series["metric"].get("label", series["metric"]["key"])} at step {series["endpoint"]["budget"] if series.get("endpoint") else "?"}, per seed</text>',
             f'<text x="{w - 10}" y="18" font-size="10" fill="#b45309" text-anchor="end">SYNTHETIC TOY DATA</text>']
    step = (w - left - 20) / max(1, len(configs))
    for i, g in enumerate(configs):
        cx = left + step * (i + 0.5)
        for k, v in enumerate(g['values']):
            parts.append(f'<circle cx="{cx - 8 + k * 8:.1f}" cy="{y(v["value"]):.1f}" r="4" fill="#2563eb"/>')
        if g['mean'] is not None:
            parts.append(f'<line x1="{cx - 18:.1f}" x2="{cx + 18:.1f}" y1="{y(g["mean"]):.1f}" y2="{y(g["mean"]):.1f}" stroke="#111" stroke-width="2"/>')
        parts.append(f'<text x="{cx:.1f}" y="{h - bottom + 18}" font-size="10" text-anchor="middle">{g["config"][:18]}{" (base)" if g["baseline"] else ""}</text>')
    for v in (lo, hi):
        parts.append(f'<text x="{left - 6}" y="{y(v) + 4:.1f}" font-size="10" text-anchor="end">{v:.0f}</text>')
    parts.append('</svg>')
    path.write_text('\n'.join(parts))


# ── Report ─────────────────────────────────────────────────────────────────────────────────────────────────────

OUTCOME = {'pass': 'Criteria met', 'fail': 'Criteria not met', 'inconclusive': 'Inconclusive'}


def outcome_of(criteria):
    return 'fail' if any(c['result'] == 'fail' for c in criteria) else 'inconclusive' if not criteria or any(
        c['result'] == 'inconclusive' for c in criteria) else 'pass'


def report_md(report, series, outcome):
    rows = '\n'.join(f'| {c["name"]} | **{c["result"]}** | {c.get("value")} | {c.get("threshold", "")} | {c["reason"]} | `{c.get("code", "")}` |'
                     for c in report['criteria'])
    configs = series['endpoint']['configs'] if series.get('endpoint') else []
    lines = []
    for group in configs:
        mean = 'N/A' if group['mean'] is None else f"{group['mean']:.1f}"
        seeds = ', '.join(f"{v['value']:.1f}" for v in group['values']) or 'none'
        lines.append(f"| {group['config']}{' (baseline)' if group['baseline'] else ''} | {group['n']} of {group['planned']} | {mean} | {seeds} |")
    table = '\n'.join(lines)
    g = report['generated']
    return f"""# Analysis of {Analysis.folder}

Outcome: **{OUTCOME[outcome]}** (derived from the criteria below; the statement is in report.json).

| Criterion | Result | Value | Threshold | Reason | Code |
|---|---|---|---|---|---|
{rows}

## {series['metric'].get('label', series['metric']['key'])} at the full budget

Values observed at exactly the budget step, as the server composed them (completed runs only).

| Config | Seeds at budget | Mean | Seeds |
|---|---|---|---|
{table}

Figure: `endpoint.svg` (drawn by `analysis.py:svg_endpoint`).

## Provenance

- Regenerate: `{shlex.join(g['command'])}` in `{g.get('cwd', '')}`
- Commit `{g.get('commit') or 'not a git checkout'}`{' — **uncommitted changes**: this commit alone does not reproduce the report' if g.get('dirty') else ''}
- Untracked files (names only, not captured): {', '.join(g.get('untracked') or []) or 'none'}
- Inputs read: {len(report['inputs']['runs'])} runs, fingerprint `{report['inputs']['key'][:12]}`
"""


@proto.cli
def main():
    folder = ExperimentFolder(DreamLakeClient().experiments.project(Analysis.namespace, Analysis.project), Analysis.folder)
    gen = new_attempt_id()
    out = HERE / 'analysis' / 'out' / gen
    out.mkdir(parents=True, exist_ok=True)
    provenance = collect_provenance(cwd=str(HERE))
    generated = {'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'command': [sys.executable, *sys.argv], 'cwd': str(HERE), 'commit': provenance['commit'],
                 'dirty': provenance['dirty'], 'untracked': provenance['untracked'], 'python': provenance['python']}
    inputs = None
    try:
        inputs = folder.inputs()          # first: what this analysis is about to read
        contract = folder.contract()
        series = folder.series(Analysis.metric)
        criteria = [{**evaluations_pass(contract), 'code': 'analysis.py:evaluations_pass'},
                    {**baseline_beats_others(contract, series), 'code': 'analysis.py:baseline_beats_others', 'figure': 'endpoint'}]
        svg_endpoint(series, out / 'endpoint.svg')
        outcome = outcome_of(criteria)
        lead = criteria[1]
        statement = (f'{criteria[0]["reason"].capitalize()}. {lead["reason"].capitalize()}. '
                     'Synthetic toy data: this demonstrates the workflow, not a research result.')
        report = {'id': gen, 'status': 'complete', 'statement': statement, 'criteria': criteria,
                  'figures': [{'name': 'endpoint', 'label': 'Value at the full budget, per seed', 'file': 'endpoint.svg',
                               'code': 'analysis.py:svg_endpoint'}],
                  'report': 'report.md', 'generated': generated, 'inputs': inputs}
        (out / 'report.md').write_text(report_md(report, series, outcome))
        (out / 'report.json').write_text(json.dumps(report, indent=2))
        print(f'{OUTCOME[outcome]}: ' + '; '.join(f'{c["name"]} {c["result"]}' for c in criteria))
        if not Analysis.local:
            published = folder.publish_analysis(out, report)
            print(f'published analysis {gen}' + (' (STALE: the experiment changed while this ran; run it again)' if published['stale'] else ''))
        else:
            print(f'written {out} (not published)')
        return 0 if outcome == 'pass' else 1
    except Exception as error:  # a technical error of the analysis itself: never shown as criteria not met
        message = f'{type(error).__name__}: {error}'[:2000]
        traceback.print_exc()
        if not Analysis.local and inputs is not None:
            try:
                folder.publish_analysis(out, {'id': gen, 'status': 'error', 'error': message, 'criteria': [], 'figures': [],
                                              'report': None, 'generated': generated, 'inputs': inputs})
                print(f'analysis ERROR recorded as {gen}: {message}', file=sys.stderr)
            except Exception as publish_error:
                print(f'analysis ERROR (not recorded: {publish_error}): {message}', file=sys.stderr)
        else:
            print(f'analysis ERROR: {message}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
