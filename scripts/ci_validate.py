"""Bounded local validation; browser/CLI listener suites require operator port preflight.

Run with the project's installed Python, from any cwd. Outputs include exact candidate,
commands, phase status, timeout and pytest JUnit counts. No install, cleanup or provider call.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def junit_counts(report: Path) -> dict[str, int]:
    """Check pytest's flat JUnit suites against the testcases actually reported."""
    root = ET.parse(report).getroot()
    if root.tag not in ('testsuite', 'testsuites'):
        raise ValueError('expected testsuite or testsuites root')
    suites = [root] if root.tag == 'testsuite' else root.findall('testsuite')
    if not suites or len(list(root.iter('testsuite'))) != len(suites):
        raise ValueError('expected nonempty flat pytest testsuites')
    totals = dict.fromkeys(('tests', 'failures', 'errors', 'skipped'), 0)

    def check_counts(element, actual, required=True):
        for key, count in actual.items():
            raw = element.get(key)
            if raw is None and not required:
                continue
            if raw is None or not raw.isascii() or not raw.isdigit():
                raise ValueError(f'invalid or missing {key} counter')
            if int(raw) != count:
                raise ValueError(f'{key} counter contradicts actual testcases')

    for suite in suites:
        cases = suite.findall('testcase')
        if len(list(suite.iter('testcase'))) != len(cases):
            raise ValueError('testcases must be direct testsuite children')
        actual = dict.fromkeys(totals, 0)
        actual['tests'] = len(cases)
        for case in cases:
            outcomes = [child.tag for child in case if child.tag in ('failure', 'error', 'skipped')]
            if len(outcomes) > 1:
                raise ValueError('testcase has contradictory outcome elements')
            for tag, key in (('failure', 'failures'), ('error', 'errors'), ('skipped', 'skipped')):
                actual[key] += outcomes.count(tag)
        check_counts(suite, actual)
        for key in totals:
            totals[key] += actual[key]
    if len(list(root.iter('testcase'))) != totals['tests']:
        raise ValueError('testcases outside declared suites')
    if root.tag == 'testsuites':
        check_counts(root, totals, required=False)
    return totals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('unit', 'cli', 'browser', 'all'), default='unit')
    parser.add_argument('--out', type=Path, required=True, help='Fresh output directory')
    parser.add_argument('--timeout', type=int, default=300)
    parser.add_argument('--source', type=Path, default=ROOT,
                        help='Exact product checkout supplying source/config (tests stay here)')
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.source = args.source.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    phases = ('unit', 'cli', 'browser') if args.phase == 'all' else (args.phase,)
    result = {'candidate': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
              'diff_stat': subprocess.check_output(['git', '-C', str(ROOT), 'diff', '--stat'], text=True),
              'working_tree': subprocess.check_output(['git', '-C', str(ROOT), 'status', '--short'], text=True),
              'source_checkout': str(args.source),
              'source_commit': subprocess.check_output(['git', '-C', str(args.source), 'rev-parse', 'HEAD'], text=True).strip(),
              'source_status': subprocess.check_output(['git', '-C', str(args.source), 'status', '--short'], text=True),
              'evidence_label': 'offline synthetic development validation; no live/product acceptance',
              'phases': []}
    for phase in phases:
        report = args.out / f'{phase}.xml'
        targets = {'unit': ['tests', '--ignore=tests/cli', '--ignore=tests/browser/test_ui_journey.py'],
                   'cli': ['tests/cli'], 'browser': ['tests/browser/test_ui_journey.py']}[phase]
        targets = [('--ignore=' + str(ROOT / t.split('=', 1)[1])) if t.startswith('--ignore=')
                   else str(ROOT / t) for t in targets]
        command = [sys.executable, '-m', 'pytest', *targets, '-c', str(args.source / 'pyproject.toml'),
                   '-o', f'pythonpath={args.source / "src"}', '-q', f'--junitxml={report}',
                   f'--basetemp={args.out / (phase + "-tmp")}']
        item = {'phase': phase, 'command': command}
        if phase in ('cli', 'browser') and not all(os.environ.get(k) for k in
                ('VOICELAB_TEST_PORT', 'VOICELAB_TEST_PORT_CLAIM', 'VOICELAB_PORT_CHECKED')):
            item.update(status='not_run', reason='operator agent-ports claim/check required', returncode=None)
        else:
            try:
                env = dict(os.environ)
                env['PYTHONPATH'] = str(args.source / 'src')
                completed = subprocess.run(command, capture_output=True, timeout=args.timeout, env=env)
                (args.out / f'{phase}.log').write_bytes(completed.stdout + completed.stderr)
                item.update(returncode=completed.returncode,
                            status='incomplete' if completed.returncode == 0 else 'failed')
                item['report'] = str(report)
                try:
                    item['counts'] = junit_counts(report)
                except (OSError, ET.ParseError, ValueError) as error:
                    item['reason'] = f'invalid JUnit report: {error}'
                else:
                    counts = item['counts']
                    if counts['failures'] or counts['errors']:
                        item.update(status='failed', reason='JUnit reports failures or errors')
                    elif not counts['tests']:
                        item['reason'] = 'JUnit reports no actual testcases'
                    elif counts['skipped']:
                        item['reason'] = 'JUnit reports skipped testcases'
                    elif completed.returncode == 0:
                        item['status'] = 'passed'
            except subprocess.TimeoutExpired as error:
                (args.out / f'{phase}.log').write_bytes((error.stdout or b'') + (error.stderr or b''))
                item.update(status='timeout', returncode=None)
        result['phases'].append(item)
        (args.out / 'validation.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
    return 0 if all(p['status'] == 'passed' for p in result['phases']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
