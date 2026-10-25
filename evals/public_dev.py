"""Offline synthetic instrumentation eval. No speech, external authority or acceptance.

Only inline, team-authored data is evaluated. This is not a saved-artifact access
reader and supplies no rights/custody verifier. SQLite files created here belong
only to the explicitly new output directory. Nothing is deleted or overwritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path
import subprocess
import sys

from voicelab.accounting.ledger import AttemptLedger
from voicelab.core.task import TaskAuthority
from voicelab.evaluation.artifacts import canonical_bytes
from voicelab.reporting.reports import build_report

RUN = 'public-dev-instrumentation'


def inputs():
    turns = [f'public-turn-{i:02d}' for i in range(19)]
    assignments = [{'logical_turn_id': t, 'trial_id': 'public-trial', 'arm': 'C',
                    'cohort': 'clean', 'network_profile': 'offline', 'model_version': 'inline-fake',
                    'admission': 'started', 'admission_evidence_reference': 'inline-public-start-' + t}
                   for t in turns]
    manifest = {'run_id': RUN, 'mode': 'mock', 'code_hash': '0' * 64, 'build_hash': '0' * 64,
                'config_hash': '0' * 64, 'schema_hash': '0' * 64, 'fixture_manifest_hash': '0' * 64,
                'split': 'dev', 'fixture_seal': None, 'task_version': 'public-test-v1',
                'prompt_version': 'public-test-v1', 'tool_version': 'public-test-v1',
                'adapters': {}, 'models': {}, 'audio': {}, 'endpoint': {}, 'onset': {},
                'resampler': {}, 'browser': 'not_run', 'operating_system': 'synthetic',
                'device': 'not_run', 'network': {}, 'fault_seed': 0, 'calibration_hash': None,
                'trial_plan_hash': '0' * 64, 'thresholds': {}, 'pricing_manifest_hash': None,
                'authorization_reference': None, 'evidence_label': 'inline synthetic scorer contract only',
                'created_at': '2026-10-25T00:00:00Z', 'scheduled_turns': turns,
                'turn_assignments': assignments}
    # Deliberately fabricated qualification exercises the scorer, never a speech oracle claim.
    rows = [{'run_id': RUN, 'trial_id': 'public-trial', 'logical_turn_id': t, 'arm': 'C',
             'outcome': 'success', 'task_correct': True, 'spoken_correct': True,
             'receipt_latency_ms': 100, 'render_latency_ms': 200,
             'timing_boundary': 'browser_output_timestamp_estimate', 'calibration_valid': True,
             'admission': 'started', 'cohort': 'clean', 'timing_domain': 'browser_performance_ms'}
            for t in turns[:18]]
    ledger = {'known_cost': '0', 'unknown_exposure': '0', 'unknown_count': 1,
              'complete': False, 'attempts': [{'attempt_id': 'public-unknown-attempt',
                 'logical_turn_id': turns[-1], 'arm': 'C', 'component': 'stt',
                 'price_version': 'mock-zero-v1', 'state': 'unknown', 'settled': None,
                 'reservation': '0'}]}
    return manifest, rows, ledger


def seed(root: Path):
    task = TaskAuthority(root / 'm6-task.sqlite')
    task.execute('m6-public-trial', 'list_slots', {}, logical_turn_id='offer', response_epoch=0)
    task.execute('m6-public-trial', 'select_slot', {'slot_id': 'slot-a'},
                 logical_turn_id='select', response_epoch=0)
    selected = task.snapshot('m6-public-trial')
    args = {k: selected[k] for k in ('slot_id', 'challenge')}
    args['state_version'] = selected['state_version']
    outcome = task.execute('m6-public-trial', 'confirm_slot', args, logical_turn_id='confirm',
                           response_epoch=0, explicit_confirmation=True)
    assert outcome['ok'] is True and outcome['effect_committed'] is True
    task.close()
    ledger = AttemptLedger(root / 'm6-ledger.sqlite', Decimal('0'), owner_id='m6-public-owner')
    for attempt in ('m6-settled', 'm6-unknown'):
        ledger.reserve(attempt, amount=Decimal('0'), logical_turn_id='confirm', arm='C', component='stt',
                       price_version='mock-zero-v1', basis='public-dev-no-egress', trial_id='m6-public-trial',
                       session_id='m6-public-session', transport_turn_id='wire-confirm',
                       connection_epoch=0, response_epoch=0)
        ledger.authorize_dispatch(attempt)
    ledger.settle('m6-settled', amount=Decimal('0'), usage={'provider_requests': 0}, source='mock_observation')
    ledger.mark_unknown('m6-unknown', reason='missing_usage')


def verify(root: Path):
    # A new interpreter reopens the exact durable files; no reset/migration fixture tricks.
    task = TaskAuthority(root / 'm6-task.sqlite')
    before = task.snapshot('m6-public-trial')
    assert before['booking']['slot_id'] == 'slot-a'
    original = next(r for r in task.history('m6-public-trial')['original_operations']
                    if r['command'] == 'confirm_slot')
    duplicate = task.execute('m6-public-trial', 'confirm_slot', original['arguments'],
                            logical_turn_id='confirm', response_epoch=0, explicit_confirmation=True)
    assert duplicate['duplicate'] is True
    assert task.snapshot('m6-public-trial')['booking'] == before['booking']
    task.close()
    ledger = AttemptLedger(root / 'm6-ledger.sqlite', Decimal('0'), owner_id='m6-public-owner')
    ledger.settle('m6-unknown', amount=Decimal('0'), usage={'provider_requests': 0}, source='late_mock_observation')
    ledger.settle('m6-settled', amount=Decimal('0'), usage={'provider_requests': 0}, source='duplicate_mock_observation')
    snapshot = ledger.summary()
    assert len(snapshot['attempts']) == 2
    assert snapshot['known_cost'] == '0' and snapshot['unknown_count'] == 1
    assert snapshot['complete'] is False
    assert next(r for r in snapshot['attempts'] if r['attempt_id'] == 'm6-unknown')['state'] == 'unknown'


def evaluate(out: Path):
    out.mkdir(parents=True, exist_ok=False)
    checks = []

    def check(name, operation):
        try:
            details = operation()
            checks.append({'name': name, 'status': 'passed', 'details': details})
        except Exception as exc:
            checks.append({'name': name, 'status': 'failed', 'error_type': type(exc).__name__,
                           'reason': str(exc)[:1000]})

    def report_oracle():
        manifest, rows, ledger = inputs()
        one = build_report(manifest, rows, ledger)
        two = build_report(manifest, rows, ledger)
        a, b = canonical_bytes(one), canonical_bytes(two)
        assert a == b
        cohort = one['cohorts'][0]
        assert cohort['started'] == 19
        assert cohort['all_started_clean_acceptable_render']['p95']['value_ms'] is None
        assert one['costs']['unknown_count'] == 1 and one['costs']['complete'] is False
        assert len(one['rows']) == 19
        (out / 'canonical-report.json').write_bytes(a)
        return {'report_sha256': hashlib.sha256(a).hexdigest(), 'rows': 19,
                'missing_terminal_retained': True, 'p95': '>10 s / not attained',
                'unknown_attempts': 1, 'repeated_bytes_identical': True}

    def durability():
        for phase in ('seed', 'verify'):
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--phase', phase,
                                     '--out', str(out)], capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise AssertionError(f'{phase} exit {result.returncode}: {result.stderr[-2000:]}')
        return {'fresh_interpreter_reopen': True, 'booking_effects': 1, 'ledger_attempts': 2,
                'unknown_liability_retained': True, 'no_network_required': True}

    check('canonical_report_denominators_costs_reproduction', report_oracle)
    check('sqlite_task_and_attempt_restart_journey', durability)
    summary = {'schema_version': 1, 'split': 'public_dev', 'mode': 'offline_synthetic',
               'checks': checks, 'passed': all(c['status'] == 'passed' for c in checks),
               'limitations': ['No speech/browser/acoustic measurement or real provider calls',
                              'No independent rights, custody, holdout or product acceptance',
                              'Inline qualification labels test scorer arithmetic only',
                              'Saved-artifact CLI report remains gated; not run as a success']}
    (out / 'eval-summary.json').write_bytes(canonical_bytes(summary))
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True, help='fresh owned directory; existing path refuses')
    parser.add_argument('--phase', choices=('seed', 'verify'), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.phase:
        {'seed': seed, 'verify': verify}[args.phase](args.out)
        return 0
    return evaluate(args.out)


if __name__ == '__main__':
    raise SystemExit(main())
