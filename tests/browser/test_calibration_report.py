"""Calibration report gate over synthetic raw anchors (plan W3). NOT YET RUN.

Pure source contracts (no browser, listener or device): synthetic worklet anchors pass
through the harness's own calibration.gap_row and reporting.diagnostics.calibration_report,
exactly as _calibrate_page composes them. Values are author-chosen, not measurements.
"""
from __future__ import annotations

from decimal import Decimal

from voicelab.harness.calibration import gap_row
from voicelab.reporting.diagnostics import calibration_report

RATE, QUANTUM, PULSE, GAP = 48000, 128, 480, 4800  # 100 ms scheduled gap at 48 kHz


def _raw(start: int = 1000, *, second_pulse_shift: int = 0, complete: bool = True) -> dict:
    """One synthetic probe: two pulses, scheduled end, and monotonic output timestamps."""
    events = [{'kind': 'probe_started', 'frame': start, 'quantum': QUANTUM, 'gap_frames': GAP},
              {'kind': 'probe_pulse', 'pulse': 0, 'frame': start},
              {'kind': 'probe_pulse', 'pulse': 1, 'frame': start + PULSE + GAP + second_pulse_shift},
              {'kind': 'probe_done', 'frame': start + 2 * PULSE + GAP}]
    if not complete:
        events = events[:2]
    return {'request': {'sample_rate': RATE, 'pulse_frames': PULSE},
            'result': {'events': events},
            'before': {'context_time': 0.5, 'output_timestamp': {'contextTime': 0.5, 'performanceTime': 4500.0}},
            'after': {'context_time': 1.0, 'output_timestamp': {'contextTime': 1.0, 'performanceTime': 5000.0}},
            'options': {'kind': 'gap', 'gap_ms': 100, 'offset_ms': 0}}


def _rows(raws: list[dict]) -> list[dict]:
    return [gap_row(raw, index)[0] for index, raw in enumerate(raws)]


def test_gap_row_maps_raw_anchors_and_rejects_injected_offset():
    """what_bug_this_catches: a gap row not derived from the worklet pulse frames through the
    output-timestamp mapping (wrong gap, cutoff or monotonic flag), or an anchor check that
    accepts a deliberate +100 ms mis-mapping."""
    row, controls = gap_row(_raw(), 0)
    assert row['observation'] == 'observed' and row['mapping_monotonic'] is True
    assert abs(row['observed_gap_ms'] - 100) < 1e-6
    assert row['cutoff_error_samples'] == 0 and row['quantum_samples'] == QUANTUM
    assert controls['sample_anchor_valid'] is True and controls['injected_offset_rejected'] is True


def test_report_emits_every_raw_repeat_and_passes_a_clean_device():
    """what_bug_this_catches: a report that publishes only a summary statistic. All 30 raw
    per-repeat errors must be emitted alongside the gate decision."""
    report = calibration_report(_rows([_raw(1000 + 10000 * i) for i in range(30)]))
    assert report['repeat_count'] == 30 and report['required_repeats'] == 30
    assert len(report['errors_ms']) == 30
    assert all(abs(Decimal(error)) < Decimal('0.000001') for error in report['errors_ms'])
    assert report['complete'] is True and report['internal_gate_pass'] is True
    assert report['failures'] == []


def test_failing_repeat_rejects_device_instead_of_averaging_in():
    """what_bug_this_catches: averaging a failing repeat into the device summary. One 50 ms
    gap error keeps the sample SD under 15 ms, so only the per-repeat threshold catches it:
    the gate must fail with that repeat named, and its raw error must stay visible."""
    raws = [_raw(1000 + 10000 * i) for i in range(30)]
    raws[29] = _raw(1000 + 10000 * 29, second_pulse_shift=2400)  # +50 ms observed gap
    report = calibration_report(_rows(raws))
    assert Decimal(report['sample_sd_ms']) < 15
    assert report['internal_gate_pass'] is False
    assert {'repeat': 29, 'reason': 'gap error exceeds threshold'} in report['failures']
    assert abs(Decimal(report['errors_ms'][29]) - 50) < Decimal('0.000001')


def test_missing_observation_or_short_run_is_incomplete_not_passed():
    """what_bug_this_catches: an unobserved repeat (absent anchors) or a run that stopped
    early being treated as a pass. Missing data is a failure and the run is incomplete."""
    raws = [_raw(1000 + 10000 * i) for i in range(30)]
    raws[5] = _raw(1000 + 10000 * 5, complete=False)
    report = calibration_report(_rows(raws))
    assert report['complete'] is False and report['internal_gate_pass'] is False
    assert {'repeat': 5, 'reason': 'missing observation or incompatible clock domain'} in report['failures']
    short = calibration_report(_rows([_raw(1000 + 10000 * i) for i in range(12)]))
    assert short['complete'] is False and short['internal_gate_pass'] is False
