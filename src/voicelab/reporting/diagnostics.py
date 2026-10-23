"""Inspect actual future artifacts. These functions never manufacture observations."""
from __future__ import annotations

from decimal import Decimal


def calibration_report(rows: list[dict]) -> dict:
    errors, failures = [], []
    for index, row in enumerate(rows):
        required = ('scheduled_gap_ms', 'observed_gap_ms', 'observed_quantum_ms', 'cutoff_error_samples', 'quantum_samples')
        if any(row.get(k) is None for k in required) or row.get('clock_domain') != 'browser_performance_ms':
            failures.append({'repeat': index, 'reason': 'missing observation or incompatible clock domain'})
            continue
        expected, observed, quantum = (Decimal(str(row[k])) for k in required[:3])
        if not all(v.is_finite() for v in (expected, observed, quantum)) or quantum <= 0:
            failures.append({'repeat': index, 'reason': 'invalid timing values'})
            continue
        error = observed - expected
        errors.append(error)
        if abs(error) > max(Decimal(20), 2 * quantum):
            failures.append({'repeat': index, 'reason': 'gap error exceeds threshold'})
        if abs(row['cutoff_error_samples']) > row['quantum_samples'] or row.get('mapping_monotonic') is not True:
            failures.append({'repeat': index, 'reason': 'cutoff or monotonic mapping invalid'})
    sd = None
    if len(errors) > 1:
        mean = sum(errors) / len(errors)
        sd = (sum((e - mean) ** 2 for e in errors) / (len(errors) - 1)).sqrt()
    complete = len(rows) >= 30 and len(errors) == len(rows)
    if sd is None or sd > 15:
        failures.append({'reason': 'gap sample SD unavailable or above 15ms'})
    return {'kind': 'calibration_artifact_analysis', 'repeat_count': len(rows), 'required_repeats': 30,
            'complete': complete, 'internal_gate_pass': complete and not failures,
            'sample_sd_ms': str(sd) if sd is not None else None, 'errors_ms': [str(e) for e in errors],
            'failures': failures, 'acoustic_uncertainty': 'unknown; internal timing cannot establish physical sound onset'}


def soak_report(samples: list[dict], summary: dict) -> dict:
    required = ('rss_bytes', 'browser_heap_bytes', 'owned_handles', 'queue_seconds', 'monotonic_seconds',
                'sockets', 'files', 'async_tasks', 'event_loop_lag_ms')
    failures = []
    complete_metrics = bool(samples) and all(all(r.get(k) is not None for k in required) for r in samples)
    if not complete_metrics:
        failures.append('resource series incomplete; missing instrumentation remains unknown')
    if complete_metrics:
        if any(r['queue_seconds'] > 2 for r in samples):
            failures.append('audio queue exceeds two seconds')
        if samples[-1]['owned_handles'] != 0:
            failures.append('owned handles remain after End')
        context_ids = set(samples[0].get('browser_heaps_by_context', {}))
        if len(context_ids) != 3 or any(set(row.get('browser_heaps_by_context', {})) != context_ids for row in samples):
            failures.append('three per-context heap series required')
        warm = [r for r in samples if r.get('after_warmup') is True]
        if len(warm) < 5:
            failures.append('fewer than five post-warmup checkpoints')
        else:
            for metric, bound in [('rss_bytes', 50 * 1024 * 1024), ('browser_heap_bytes', 20 * 1024 * 1024)]:
                series = [r[metric] for r in warm]
                if max(series) - series[0] > bound or all(a < b for a, b in zip(series[-5:], series[-4:])):
                    failures.append(f'{metric} growth gate failed')
            for context_id in context_ids:
                values = [row.get('browser_heaps_by_context', {}).get(context_id) for row in warm]
                if any(v is None for v in values):
                    failures.append(f'context {context_id} heap unknown')
                elif max(values) - values[0] > 20 * 1024 * 1024 or all(a < b for a, b in zip(values[-5:], values[-4:])):
                    failures.append(f'context {context_id} heap growth gate failed')
    coverage = (summary.get('aggregate_turns', 0) >= 200 and summary.get('interruptions', 0) >= 40
                and summary.get('reconnects', 0) >= 10 and summary.get('contexts') == 3
                and summary.get('fourth_session_refused') is True
                and summary.get('coverage_actions_are_attempts') is not True)
    return {'kind': 'offline_soak_artifact_analysis', 'coverage_complete': coverage,
            'resource_gate_pass': complete_metrics and not failures, 'failures': failures,
            'passed': coverage and complete_metrics and not failures,
            'summary': summary, 'samples': samples,
            'limitation': 'Analyzer only; does not launch browsers or establish provenance of supplied observations'}
