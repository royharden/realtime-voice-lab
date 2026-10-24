"""Synthetic wire fixtures through the actual V/C parser and accounting path. NOT YET RUN.

fixtures/wire/ is author-created diagnostic data (D4-owned, read-only here), never recorded
provider traffic. Plan W2: missing usage is unknown, never zero; raw provider text never leaks.
"""
from __future__ import annotations

import base64
from decimal import Decimal
import json
from pathlib import Path

import pytest

from voicelab.accounting.ledger import AttemptLedger
from voicelab.adapters.providers import parse_realtime, parse_transcription, serialize_command

WIRE = Path(__file__).resolve().parents[2] / 'fixtures' / 'wire'


def _lines(name: str) -> list[str]:
    return [line for line in (WIRE / name).read_text(encoding='utf-8').splitlines() if line.strip()]


def _native_ledger(path: Path) -> AttemptLedger:
    """One reserved synthetic native attempt with token-dimensioned bounds (no real tariff)."""
    ledger = AttemptLedger(path, Decimal('1.00'), owner_id='wire-owner')
    terms = {'mode': 'live', 'component': 'native', 'model': 'synthetic-wire-model',
             'currency': 'USD', 'price_version': 'synthetic-v1', 'pricing_reference': 'synthetic-pricing',
             'capability_reference': 'synthetic-capability', 'enforcement_reference': 'synthetic-enforcement',
             'bound_usd': '0.30', 'basis': 'synthetic token bound',
             'rates': {'input_tokens': '0.0001', 'output_tokens': '0.0002'},
             'usage_bounds': {'input_tokens': '1000', 'output_tokens': '1000'},
             'usage_units': {'input_tokens': 'tokens', 'output_tokens': 'tokens'}}
    ledger.reserve('attempt-wire', amount=Decimal('0.30'), logical_turn_id='turn-1', arm='V',
                   component='native', price_version='synthetic-v1', basis='synthetic token bound',
                   terms=terms, trial_id='trial-1', session_id='session-1',
                   transport_turn_id='transport-1', connection_epoch=0, response_epoch=0)
    return ledger


def test_native_success_fixture_normalizes_every_event():
    """what_bug_this_catches: a V parser regression that drops tool calls, mis-decodes PCM or
    loses response usage on the documented success path (providers.parse_realtime)."""
    transcript, tool, audio, done = (parse_realtime(line) for line in _lines('native-success.jsonl'))
    assert transcript == {'type': 'transcript.final', 'text': 'list slots', 'item_id': 'input-1'}
    assert tool == {'type': 'tool.call', 'operation_id': 'call-1', 'name': 'list_slots', 'arguments': {}}
    assert audio['type'] == 'audio.delta' and audio['sample_rate'] == 24000
    assert audio['response_id'] == 'response-1'
    assert len(base64.b64decode(audio['pcm16'])) % 2 == 0
    assert done == {'type': 'response.done', 'response_id': 'response-1', 'status': 'completed',
                    'usage': {'input_tokens': 0, 'output_tokens': 0}}


def test_fault_fixture_rate_limit_cancel_empty_and_truncate():
    """what_bug_this_catches: provider error text copied into telemetry, a cancelled or empty
    response reported with zero usage, or truncate acknowledgements lost (parse_realtime)."""
    error, cancelled, empty, truncated = (parse_realtime(line) for line in _lines('native-faults.jsonl'))
    assert error == {'type': 'error', 'code': 'rate_limit_exceeded',
                     'message': 'Provider error; raw content omitted'}
    assert 'synthetic fault' not in json.dumps(error)
    assert cancelled['status'] == 'cancelled' and cancelled['usage'] is None
    assert empty['status'] == 'completed' and empty['usage'] is None
    assert truncated == {'type': 'cancel.ack', 'item_id': 'output-1', 'audio_end_ms': 40}


def test_cancel_and_truncate_serialize_rendered_cutoff():
    """what_bug_this_catches: truncate computed from generated rather than rendered samples, or
    a wrong sample-to-ms conversion. 960 rendered samples at 24 kHz is the fixture's 40 ms."""
    assert json.loads(serialize_command('cancel', response_id='response-1')) == {
        'type': 'response.cancel', 'response_id': 'response-1'}
    assert json.loads(serialize_command('truncate', item_id='output-1', rendered_cutoff=960)) == {
        'type': 'conversation.item.truncate', 'item_id': 'output-1', 'content_index': 0, 'audio_end_ms': 40}
    with pytest.raises(ValueError, match='invalid rendered cutoff'):
        serialize_command('truncate', item_id='output-1', rendered_cutoff=-1)


@pytest.mark.parametrize('line', [1, 2], ids=['cancelled', 'empty_completed'])
def test_missing_usage_becomes_unknown_liability_not_zero(tmp_path, line):
    """what_bug_this_catches: usage=None on a cancelled/empty response settled as a zero charge,
    hiding canceled-but-billed liability. The live adapter forwards response.done usage to
    AttemptLedger.settle_usage, which must leave the full reservation as unknown exposure."""
    ledger = _native_ledger(tmp_path / 'ledger.sqlite')
    event = parse_realtime(_lines('native-faults.jsonl')[line])
    row = ledger.settle_usage('attempt-wire', usage=event['usage'], source='provider_terminal_usage')
    assert row['state'] == 'unknown' and row['settled'] is None
    assert row['reason'] == 'missing_authoritative_usage'
    summary = ledger.summary()
    assert Decimal(summary['known_cost']) == 0
    assert Decimal(summary['unknown_exposure']) == Decimal('0.30')
    assert summary['complete'] is False


def test_observed_usage_settles_measured_amount(tmp_path):
    """what_bug_this_catches: an over-broad 'unknown' fix that refuses to settle authoritative
    observed usage; zero is legitimate only when the counters are actually present."""
    ledger = _native_ledger(tmp_path / 'ledger.sqlite')
    done = parse_realtime(_lines('native-success.jsonl')[3])
    row = ledger.settle_usage('attempt-wire', usage=done['usage'], source='provider_terminal_usage')
    assert row['state'] == 'settled' and Decimal(row['settled']) == 0
    assert ledger.summary()['complete'] is True


def test_transcription_fixture_and_malformed_frames():
    """what_bug_this_catches: the C STT parser accepting unmapped event types, oversize
    frames, non-object JSON or odd-length PCM instead of raising a fixed protocol error."""
    delta, final = (parse_transcription(line) for line in _lines('transcription-success.jsonl'))
    assert delta == {'type': 'transcript.delta', 'text': 'select ', 'item_id': 'input-2'}
    assert final == {'type': 'transcript.final', 'text': 'select slot 1', 'item_id': 'input-2'}
    for raw in ('[]', 'not json', json.dumps({'type': 'x' * 200}), 'x' * 131073):
        with pytest.raises(ValueError, match='provider_protocol_error'):
            parse_realtime(raw)
    with pytest.raises(ValueError, match='explicit mapping required'):
        parse_realtime(json.dumps({'type': 'session.updated'}))
    odd = base64.b64encode(b'\x00\x01\x02').decode('ascii')
    with pytest.raises(ValueError, match='unsupported PCM16 chunk'):
        parse_realtime(json.dumps({'type': 'response.output_audio.delta', 'response_id': 'r',
                                   'delta': odd}))
