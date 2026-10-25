"""Driver-to-plan joins must retain independent session, boot and epoch identities."""
from copy import deepcopy
import json

import pytest

from voicelab.contracts import parse_server
from voicelab.evaluation.driver import convert_driver_events
from voicelab.harness.browser import BrowserDriver


class Socket:
    def __init__(self):
        self.callbacks = {}

    def on(self, kind, callback):
        self.callbacks[kind] = callback


class Sink:
    closed = False

    def __init__(self):
        self.events = []

    def emit(self, kind, **fields):
        self.events.append({'run_id': 'public-run', 'mode': 'mock', 'split': 'dev',
                            'sequence': len(self.events), 'timestamp': len(self.events),
                            'clock_domain': 'driver_monotonic_ns', 'kind': kind, **fields})


def setup(count=1):
    tasks = [{'task_id': f'task-{i}', 'trial_id': f'task-{i}', 'arm': 'C',
              'logical_turn_ids': [f'planned-{i}']} for i in range(count)]
    plan = {'run_id': 'public-run', 'tasks': tasks,
            'turn_assignments': [{'logical_turn_id': t['logical_turn_ids'][0],
                                  'trial_id': t['trial_id'], 'task_id': t['task_id']} for t in tasks]}
    sink = Sink()
    for index, task in enumerate(tasks):
        admission = {'mode': 'mock', 'run_id': 'observed-run', 'manifest_hashes': {},
                     'requested_models': {}, 'expected_returned_models': {}, 'actually_returned_models': {},
                     'budget_usd': '0', 'budget_owner': {'scope': 'mock_local_run', 'owner_id': 'boot-a',
                                                       'lease_observation': 'unknown'}}
        ready = {'type': 'ready', 'session_id': f'session-{index}', 'connection_epoch': 0,
                 'response_epoch': 0, 'sequence': 1, 'payload': {'mode': 'mock', 'arm': 'C',
                 'evidence_label': 'public inline fixture', 'trial_namespace': f'observed-trial-{index}',
                 'trial_id': f'observed-trial-{index}', 'run_id': 'observed-run', 'boot_id': 'boot-a',
                 'admission': admission, 'evidence_reference': f'/sessions/session-{index}/evidence'}}
        parse_server(json.dumps(ready))
        socket = Socket()
        BrowserDriver({}, 'unused-synthetic', sink).observe_socket(socket, task)
        socket.callbacks['framereceived'](json.dumps(ready))
        sink.emit('plan_turn_mapping', task_id=task['task_id'], logical_turn_id=f'planned-{index}',
                  session_id=f'session-{index}', observed_boot_id='boot-a', observed_run_id='observed-run',
                  observed_trial_id=f'observed-trial-{index}', observed_logical_turn_id='wire-turn',
                  observed_transport_turn_id='wire-turn', connection_epoch=0, response_epoch=0)
    return plan, sink


def lineage(sink, index=0, **overrides):
    fields = {'task_id': f'task-{index}', 'attempt_id': f'attempt-{index}',
              'session_id': f'session-{index}', 'observed_boot_id': 'boot-a',
              'observed_run_id': 'observed-run', 'observed_trial_id': f'observed-trial-{index}',
              'observed_logical_turn_id': 'wire-turn', 'observed_transport_turn_id': 'wire-turn',
              'connection_epoch': 0, 'response_epoch': 0}
    fields.update(overrides)
    sink.emit('attempt_lineage_observed', **fields)


def test_plan_turn_mapping_joins_repeated_transport_ids_by_session():
    """what_bug_this_catches: ignored Progress.audit mappings or turn-only cross-session joins."""
    plan, sink = setup(2)
    lineage(sink, 0)
    lineage(sink, 1)
    result = convert_driver_events(sink.events, plan)
    assert {(r['attempt_id'], r['logical_turn_id']) for r in result['attempt_correlations']} == {
        ('attempt-0', 'planned-0'), ('attempt-1', 'planned-1')}
    assert len(result['correlations']) == 2


@pytest.mark.parametrize('change', [
    {'observed_boot_id': 'another-owner'}, {'connection_epoch': 1}, {'response_epoch': 1},
    {'observed_trial_id': 'another-trial'}, {'session_id': 'another-session'},
])
def test_reservation_lineage_mismatch_never_joins(change):
    """what_bug_this_catches: reservation ownership/epochs inferred from a nearby Ready."""
    plan, sink = setup()
    lineage(sink, **change)
    try:
        result = convert_driver_events(sink.events, plan)
    except ValueError:
        return  # Explicit refusal is equally valid to unknown/unresolved attribution.
    assert not result['attempt_correlations']
    assert result['issues'] or result['attempt_lineage_unresolved']


def test_conflicting_plan_mapping_is_not_last_writer_wins():
    """what_bug_this_catches: one observed transport identity reassigned to another plan turn."""
    plan, sink = setup()
    plan['tasks'][0]['logical_turn_ids'].append('planned-other')
    plan['turn_assignments'].append({'logical_turn_id': 'planned-other', 'trial_id': 'task-0', 'task_id': 'task-0'})
    conflicting = deepcopy(sink.events[-1])
    conflicting.update(logical_turn_id='planned-other', sequence=len(sink.events), timestamp=len(sink.events))
    sink.events.append(conflicting)
    lineage(sink)
    try:
        result = convert_driver_events(sink.events, plan)
    except ValueError:
        return
    assert not result['attempt_correlations']
    assert result['issues'] or result['attempt_lineage_unresolved']


def test_legacy_unvalidated_ready_is_unknown_not_admitted():
    """what_bug_this_catches: old shallow-only Ready testimony gaining session authority."""
    plan, sink = setup()
    received = next(e for e in sink.events if e['kind'] == 'wire_received')
    received.pop('wire_validation', None)
    try:
        result = convert_driver_events([received], plan)
    except ValueError:
        return
    assert result['task_admissions'] == []
    assert result['issues']


def test_changed_projected_metadata_invalidates_parser_receipt():
    """what_bug_this_catches: a valid ingress receipt reused for altered identity metadata."""
    plan, sink = setup()
    received = next(e for e in sink.events if e['kind'] == 'wire_received')
    received['message_metadata']['payload']['boot_id'] = 'substituted-boot'
    try:
        result = convert_driver_events([received], plan)
    except ValueError:
        return
    assert result['task_admissions'] == []
    assert result['issues']
