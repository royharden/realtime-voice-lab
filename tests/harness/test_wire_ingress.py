"""Independent raw-wire validation and privacy regression at browser ingress."""
import json

import pytest

from voicelab.contracts import parse_server
from voicelab.harness.browser import BrowserDriver
from voicelab.harness.evidence import EvidenceSink
from voicelab.evaluation.driver import convert_driver_events


class Socket:
    def __init__(self):
        self.callbacks = {}

    def on(self, name, callback):
        self.callbacks[name] = callback


class Sink:
    closed = False

    def __init__(self):
        self.events = []

    def emit(self, kind, **fields):
        self.events.append({'kind': kind, **fields})


def envelope(kind, payload):
    return {'type': kind, 'protocol_version': 1, 'session_id': 'session-public',
            'connection_epoch': 0, 'response_epoch': 0, 'sequence': 1, 'payload': payload}


@pytest.mark.parametrize('message', [
    envelope('ready', {'run_id': 'forged-run', 'trial_id': 'forged-trial', 'boot_id': 'forged-boot'}),
    envelope('state', {'state': 'invented-state'}),
    envelope('transcript', {'type': 'transcript', 'text': 'PUBLIC-SENTINEL'}),
    envelope('audio', {'type': 'audio', 'pcm16': 'AAAA', 'sample_rate': 48000}),
])
def test_malformed_canonical_payload_refused_before_identity_projection(message):
    """what_bug_this_catches: a shallow envelope check trusting malformed payload identity."""
    raw = json.dumps(message)
    with pytest.raises(ValueError):
        parse_server(raw)
    sink, socket = Sink(), Socket()
    task = {'task_id': 'public-task', 'arm': 'C'}
    BrowserDriver({}, 'synthetic-unused', sink).observe_socket(socket, task)
    socket.callbacks['framereceived'](raw)
    assert [event['kind'] for event in sink.events] == ['wire_parse_error']
    assert '_session_id' not in task and '_ready_metadata' not in task
    assert 'PUBLIC-SENTINEL' not in json.dumps(sink.events)


@pytest.mark.parametrize('kind,payload', [
    ('transcript', {'type': 'transcript', 'sequence': 1, 'connection_epoch': 0,
                    'response_epoch': 0, 'turn_id': 'wire-turn', 'text': 'PUBLIC-WORDS-SENTINEL',
                    'source': 'mock_debug_text'}),
    ('audio', {'type': 'audio', 'sequence': 1, 'connection_epoch': 0, 'response_epoch': 0,
               'turn_id': 'wire-turn', 'response_id': 'response-a', 'sample_rate': 24000,
               'pcm16': 'AQACAAMA', 'synthetic': True, 'artifact_hash': None,
               'content_hash': '0' * 64, 'artifact_persisted': False}),
])
def test_valid_contentful_message_validates_then_projects_without_content(kind, payload):
    """what_bug_this_catches: applying canonical parsing after removing required text/PCM."""
    raw = json.dumps(envelope(kind, payload))
    parse_server(raw)  # Independent positive control: full bytes are a valid canonical variant.
    sink, socket = Sink(), Socket()
    task = {'task_id': 'public-task', 'arm': 'C'}
    BrowserDriver({}, 'synthetic-unused', sink).observe_socket(socket, task)
    socket.callbacks['framereceived'](raw)
    assert any(e['kind'] == 'wire_received' for e in sink.events)
    assert not any(e['kind'] == 'wire_parse_error' for e in sink.events)
    serialized = json.dumps(sink.events)
    assert 'PUBLIC-WORDS-SENTINEL' not in serialized and 'AQACAAMA' not in serialized
    assert '"text"' not in serialized and '"pcm16"' not in serialized


@pytest.mark.parametrize('kind,payload', [
    ('transcript', {'type': 'transcript', 'sequence': 1, 'connection_epoch': 0,
                    'response_epoch': 0, 'turn_id': 'wire-turn', 'text': 'PUBLIC-WORDS-SENTINEL',
                    'source': 'mock_debug_text'}),
    ('audio', {'type': 'audio', 'sequence': 1, 'connection_epoch': 0, 'response_epoch': 0,
               'turn_id': 'wire-turn', 'response_id': 'response-a', 'sample_rate': 24000,
               'pcm16': 'AQACAAMA', 'synthetic': True, 'artifact_hash': None,
               'content_hash': '0' * 64, 'artifact_persisted': False}),
])
def test_real_evidence_sink_retains_validation_without_raw_content(tmp_path, kind, payload):
    """what_bug_this_catches: privacy re-projection losing receipt fields or persisting omitted content."""
    root = tmp_path / 'm6-owned-public-evidence'
    sink = EvidenceSink(root, {'run_id': 'public-run', 'mode': 'mock', '_manifest_hash': '0' * 64})
    socket = Socket()
    task = {'task_id': 'public-task', 'trial_id': 'public-task', 'arm': 'C',
            'logical_turn_ids': ['planned-turn'], '_logical_turn_id': 'planned-turn'}
    driver = BrowserDriver({}, 'unused-synthetic', sink)
    driver.observe_socket(socket, task)
    socket.callbacks['framereceived'](json.dumps(envelope(kind, payload)))
    socket.callbacks['close']()
    sink.close(completed=True, reason='public_test_completed')
    raw = (root / 'driver.jsonl').read_text()
    assert 'PUBLIC-WORDS-SENTINEL' not in raw and 'AQACAAMA' not in raw
    assert '"text"' not in raw and '"pcm16"' not in raw
    records = [json.loads(line) for line in raw.splitlines()]
    received = next(e for e in records if e['kind'] == 'wire_received')
    assert received['wire_validation']['validation'] == 'passed'
    plan = {'run_id': 'public-run', 'tasks': [task], 'turn_assignments': [
        {'task_id': 'public-task', 'trial_id': 'public-task', 'logical_turn_id': 'planned-turn'}]}
    converted = convert_driver_events(records, plan)
    assert converted['issues'] == []
