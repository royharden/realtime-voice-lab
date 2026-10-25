"""Retained cross-field semantics remain mandatory after content is projected away."""
from copy import deepcopy
import hashlib
import json

import pytest

from voicelab.contracts import parse_server
from voicelab.evaluation.artifacts import canonical_bytes
from voicelab.evaluation.driver import convert_driver_events
from voicelab.harness.privacy import observed_metadata


def convert(message):
    message = observed_metadata(message)
    event = {'kind': 'wire_received', 'run_id': 'public-run', 'mode': 'mock', 'split': 'dev',
             'sequence': 0, 'timestamp': 0, 'clock_domain': 'driver_monotonic_ns',
             'task_id': 'public-task', 'message_metadata': message, 'wire_validation': {
                'registry': 'server_payload_v1', 'validation': 'passed',
                'scope': 'producer_wire_validation_before_privacy_projection',
                'metadata_sha256': hashlib.sha256(canonical_bytes(message)).hexdigest()}}
    return convert_driver_events([event], {'run_id': 'public-run', 'tasks': [
        {'task_id': 'public-task', 'arm': 'C'}], 'turn_assignments': []})


def envelope(kind, payload):
    return {'type': kind, 'protocol_version': 1, 'session_id': 'session-a',
            'connection_epoch': 0, 'response_epoch': 0, 'sequence': 0, 'payload': payload}


def test_descriptive_control_projection_positive():
    """what_bug_this_catches: banning valid descriptive control metadata to avoid semantic validation."""
    message = envelope('task', {'control_only': True, 'task_applicability': 'not_applicable',
                                'spoken_correctness': 'unknown'})
    parse_server(json.dumps(message))
    assert convert(message)['issues'] == []


def test_rehashed_control_projection_cannot_claim_task_commit():
    """what_bug_this_catches: projected model losing retained control/task cross-field validators."""
    message = envelope('task', {'control_only': True, 'task_applicability': 'not_applicable',
                                'spoken_correctness': 'unknown', 'ok': True, 'effect_committed': True})
    with pytest.raises(ValueError, match='control_task_view_cannot_claim_task_result'):
        parse_server(json.dumps(message))
    try:
        result = convert(message)
    except ValueError:
        return
    assert result['issues']


@pytest.mark.parametrize('change', ['trial', 'admission_run', 'admission_mode'])
def test_rehashed_ready_projection_preserves_identity_semantics(change):
    """what_bug_this_catches: independently typed projection accepting mismatched Ready identities."""
    admission = {'mode': 'mock', 'run_id': 'observed-run', 'manifest_hashes': {},
                 'requested_models': {}, 'expected_returned_models': {}, 'actually_returned_models': {},
                 'budget_usd': '0', 'budget_owner': {'scope': 'mock_local_run', 'owner_id': 'boot-a',
                                                   'lease_observation': 'unknown'}}
    message = envelope('ready', {'mode': 'mock', 'arm': 'C', 'evidence_label': 'public test',
        'trial_namespace': 'observed-trial', 'trial_id': 'observed-trial', 'run_id': 'observed-run',
        'boot_id': 'boot-a', 'admission': admission, 'evidence_reference': '/sessions/session-a/evidence'})
    parse_server(json.dumps(message))
    assert len(convert(message)['task_admissions']) == 1
    message = deepcopy(message)
    if change == 'trial':
        message['payload']['trial_id'] = 'another-trial'
    elif change == 'admission_run':
        message['payload']['admission']['run_id'] = 'another-run'
    else:
        message['payload']['admission']['mode'] = 'live'
    with pytest.raises(ValueError, match='ready_identity_mismatch'):
        parse_server(json.dumps(message))
    try:
        result = convert(message)
    except ValueError:
        return
    assert result['task_admissions'] == []
    assert result['issues']
