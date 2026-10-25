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
                item.update(returncode=completed.returncode, status='passed' if completed.returncode == 0 else 'failed')
                if report.is_file():
                    suites = ET.parse(report).getroot().iter('testsuite')
                    item['counts'] = {k: 0 for k in ('tests', 'failures', 'errors', 'skipped')}
                    for suite in suites:
                        for key in item['counts']:
                            item['counts'][key] += int(suite.get(key, '0'))
                    if item['counts']['skipped']:
                        item['status'] = 'incomplete'
            except subprocess.TimeoutExpired as error:
                (args.out / f'{phase}.log').write_bytes((error.stdout or b'') + (error.stderr or b''))
                item.update(status='timeout', returncode=None)
        result['phases'].append(item)
        (args.out / 'validation.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
    return 0 if all(p['status'] == 'passed' for p in result['phases']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
