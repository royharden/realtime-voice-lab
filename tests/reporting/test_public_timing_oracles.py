"""Independent clock and latency qualification oracles on synthetic dictionaries."""
from decimal import Decimal

import pytest

from voicelab.reporting.reports import build_report, quantile
from voicelab.reporting.timing import stage_rows


@pytest.mark.parametrize('n,failures,rank,attained', [(19, 1, 19, False), (20, 1, 19, True), (20, 2, 19, False)])
def test_exact_nearest_rank_boundary(n, failures, rank, attained):
    """what_bug_this_catches: an approximate five-percent rule replacing ceil(.95*n)."""
    result = quantile([Decimal(100)] * (n - failures) + [None] * failures, Decimal('.95'))
    assert result['n'] == n and result['rank'] == rank
    assert (result['value_ms'] is not None) is attained


@pytest.mark.parametrize('change', [
    {'end_clock_domain': 'server_monotonic_ns'},
    {'end_clock_scope': 'another-boot'},
    {'observation': 'unknown'},
    {'name': 'english_identity_translation'},
    {'end': -1}, {'end': float('nan')}, {'clock_scope': ''},
])
def test_invalid_stage_cannot_gain_a_duration(change):
    """what_bug_this_catches: cross-clock/no-op/nonfinite spans fabricated as observed."""
    stage = {'name': 'controller', 'clock_domain': 'browser_performance_ms',
             'clock_scope': 'session-a', 'observation': 'observed', 'start': 10, 'end': 20}
    stage.update(change)
    result = stage_rows({'logical_turn_id': 'public-turn', 'stages': [stage]})[0]
    assert result['value_ms'] is None and result['reason']


def test_server_nanoseconds_and_samples_are_converted_explicitly():
    """what_bug_this_catches: nanoseconds or sample offsets mislabeled milliseconds."""
    spans = [{'name': 'controller', 'clock_domain': 'server_monotonic_ns', 'clock_scope': 'boot-a',
              'observation': 'observed', 'start': 1_000_000, 'end': 4_000_000},
             {'name': 'playback', 'clock_domain': 'audio_context_samples', 'clock_scope': 'context-a',
              'observation': 'estimated', 'start': 0, 'end': 480, 'sample_rate': 24000}]
    assert [Decimal(r['value_ms']) for r in stage_rows({'logical_turn_id': 't', 'stages': spans})] == [3, 20]


@pytest.mark.parametrize('change', [
    {'spoken_correct': False}, {'task_correct': None}, {'calibration_valid': False},
    {'timing_domain': 'unknown'}, {'render_latency_ms': 10001},
])
def test_fast_unqualified_response_stays_nonattainment(report_builders, change):
    """what_bug_this_catches: fast wrong/uncalibrated answers gaining headline credit."""
    b = report_builders
    manifest = b.run_manifest(['public-turn'], [b.scheduled_turn('public-turn')])
    row = b.clean_success_row('public-turn', 1)
    row.update(change)
    report = build_report(manifest, [row], b.empty_ledger())
    result = report['cohorts'][0]['all_started_clean_acceptable_render']['p95']
    assert result['n'] == 1 and result['value_ms'] is None
