"""Canonical saved trace rows cannot cross the frozen driver's observed identity."""
import pytest

from voicelab.evaluation.assembly import _trace_rows


def inputs():
    assignment = {'planned-turn': {'trial_id': 'planned-task', 'arm': 'C',
                                   'observed_boot_id': 'boot-a'}}
    correlation = {'observed_run_id': 'observed-run', 'observed_trial_id': 'observed-trial',
                   'session_id': 'session-a', 'observed_boot_id': 'boot-a',
                   'observed_logical_turn_id': 'wire-turn', 'observed_transport_turn_id': 'wire-turn',
                   'connection_epoch': 0, 'response_epoch': 0, 'logical_turn_id': 'planned-turn'}
    event = {'run_id': 'observed-run', 'trial_id': 'observed-trial', 'arm': 'C',
             'session_id': 'session-a', 'boot_id': 'boot-a', 'logical_turn_id': 'wire-turn',
             'transport_turn_id': 'wire-turn', 'connection_epoch': 0, 'response_epoch': 0,
             'sequence': 0, 'kind': 'turn_started', 'timestamp': 1, 'clock_domain': 'server_monotonic_ns'}
    return {'run_id': 'planned-run'}, assignment, correlation, event


def test_exact_trace_mapping_positive_control():
    """what_bug_this_catches: refusing legitimate alias joins instead of validating context."""
    manifest, assignment, correlation, event = inputs()
    rows, issues = _trace_rows([event], manifest, assignment, [correlation], [])
    assert issues == []
    assert rows[0]['_scheduled_logical_turn_id'] == 'planned-turn'


@pytest.mark.parametrize('change', [
    {'connection_epoch': 1}, {'response_epoch': 1}, {'transport_turn_id': 'another-transport'},
    {'boot_id': 'another-boot'},
])
def test_trace_mapping_mismatch_is_unknown_or_refused(change):
    """what_bug_this_catches: trace start/terminal facts borrowing an incompatible plan mapping."""
    manifest, assignment, correlation, event = inputs()
    event.update(change)
    try:
        rows, issues = _trace_rows([event], manifest, assignment, [correlation], [])
    except ValueError:
        return
    assert issues or not rows


def test_explicit_recovery_transport_mapping_positive():
    """what_bug_this_catches: fixing fallback by rejecting a separately evidenced recovery."""
    manifest, assignment, original, event = inputs()
    retry = {**original, 'observed_transport_turn_id': 'retry-transport',
             'connection_epoch': 1, 'response_epoch': 1}
    event.update(transport_turn_id='retry-transport', connection_epoch=1, response_epoch=1)
    rows, issues = _trace_rows([event], manifest, assignment, [original, retry], [])
    assert issues == [] and rows[0]['_scheduled_logical_turn_id'] == 'planned-turn'


def test_recovery_transport_with_wrong_current_epoch_refuses():
    """what_bug_this_catches: a known retry transport borrowing a sibling epoch's mapping."""
    manifest, assignment, original, event = inputs()
    retry = {**original, 'observed_transport_turn_id': 'retry-transport',
             'connection_epoch': 1, 'response_epoch': 1}
    event.update(transport_turn_id='retry-transport', connection_epoch=2, response_epoch=1)
    try:
        rows, issues = _trace_rows([event], manifest, assignment, [original, retry], [])
    except ValueError:
        return
    assert issues or not rows
