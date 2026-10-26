"""Emit the mock-only two-minute storyboard; never launch services or capture devices."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence


def storyboard() -> dict:
    """A prospective schedule, with observed-result fields deliberately unset."""
    return {
        'schema_version': 1,
        'evidence': 'MOCK / TYPED INPUT / SYNTHETIC TONE; storyboard only',
        'status': 'not_recorded_not_rehearsed',
        'target_duration_seconds': 120,
        'actual_duration_seconds': None,
        'recording': None,
        'speech_correctness': 'unknown',
        'physical_acoustic_latency': 'unmeasured',
        'live_or_heldout_acceptance': False,
        'capture_gate': [
            'Exact UI build and source hashes are pinned.',
            'Independent V/C interrupt, recovery, End and cleanup scenarios work on that build.',
            'Use the tested approved synthetic local launch route; do not fabricate rights/read authority.',
            'Only owned loopback listeners with fresh port claim/check; fake media only, no real devices.',
            'Video and duration become observed only after a completed actual capture and inspection.',
        ],
        'scenes': [
            {
                'start_seconds': 0, 'end_seconds': 15, 'title': 'Scope and setup',
                'actions': [
                    'Show MOCK, typed-input and diagnostic-tone labels.',
                    'Explain the fictional appointment task and software fake-media context.',
                    'Show that acoustic latency and speech correctness are unmeasured.',
                ],
                'observe': 'Pinned local page is initialized; no live mode or physical device.',
            },
            {
                'start_seconds': 15, 'end_seconds': 40, 'title': 'V mock task',
                'actions': [
                    'Select V and Start in the already verified fake-media browser.',
                    'Submit hours, list slots, select slot 1, yes confirm, status.',
                    'Use Explicit retry between drained responses only when required by the UI.',
                    'Inspect the fictional booking; End and wait for closed with owned handles released.',
                ],
                'observe': 'Actual response/task/terminal observations, not a speech or oracle verdict.',
            },
            {
                'start_seconds': 40, 'end_seconds': 65, 'title': 'C mock task',
                'actions': [
                    'After closed/cleanup, select C and Start a fresh namespace.',
                    'Repeat the identical five typed prompts through the deterministic cascade mock.',
                    'Keep receipt, rendered estimates and unknown speech correctness distinct.',
                ],
                'observe': 'Actual C task state; do not infer a timing winner or reuse V trial identity.',
            },
            {
                'start_seconds': 65, 'end_seconds': 85, 'title': 'Interrupt and reconnect',
                'actions': [
                    'Use the independently verified interruption point; show the actual fence observation.',
                    'Show that interrupting after commit preserves the fictional booking.',
                    'Simulate disconnect and explicitly retry within the bounded recovery window.',
                    'If recovery is unknown/refused, show that result; never narrate it as success.',
                    'End and inspect actual closed state and owned-handle cleanup.',
                ],
                'observe': 'Attempted buttons are not proof of applied cancellation, recovery or cleanup.',
            },
            {
                'start_seconds': 85, 'end_seconds': 110, 'title': 'Trace and failure accounting',
                'actions': [
                    'Show lifecycle trace with distinct receipt, endpoint, fence and rendered ACK events.',
                    'Use the same-origin protected post-End metadata inspector.',
                    'Show actual terminal/failure/unknown rows and the retained-window disclosure.',
                ],
                'observe': 'No cross-clock subtraction, complete soak claim or fabricated p50/p95.',
            },
            {
                'start_seconds': 110, 'end_seconds': 120, 'title': 'Cost and open gates',
                'actions': [
                    'Show all-attempt mock cost completeness; zero provider generation is not a live tariff.',
                    'Explain K as a separate diagnostic no-generation control, not a spoken-task oracle.',
                    'Close with live, rights/access, heldout, physical-device and independent acceptance gates open.',
                ],
                'observe': 'No recorded demo, measured duration or project completion is asserted by this plan.',
            },
        ],
    }


def markdown(plan: dict) -> str:
    rows = ['# Mock demo storyboard', '', plan['evidence'], '',
            'Target: 120 seconds. Actual duration: unmeasured. Recording: none.', '',
            '| Target seconds | Scene |', '|---|---|']
    for scene in plan['scenes']:
        rows.append(f"| {scene['start_seconds']}–{scene['end_seconds']} | {scene['title']} |")
    for scene in plan['scenes']:
        rows.extend(['', f"## {scene['title']}", ''])
        rows.extend('- ' + action for action in scene['actions'])
        rows.extend(['', 'Check: ' + scene['observe']])
    rows.extend(['', '## Before capture', ''])
    rows.extend('- ' + gate for gate in plan['capture_gate'])
    return '\n'.join(rows) + '\n'


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='Emit machine-readable planned scenes')
    parser.add_argument('--out', type=Path, help='New storyboard file; exclusive creation, never overwrite')
    args = parser.parse_args(argv)
    plan = storyboard()
    output = json.dumps(plan, indent=2, ensure_ascii=False) + '\n' if args.json else markdown(plan)
    if args.out is not None:
        # Parents must already exist. This command does not provision a runtime directory.
        with args.out.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(output)
    else:
        print(output, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
