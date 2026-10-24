"""Producer privacy projection contract (plan section 7; D7 R2 closed projection). NOT YET RUN."""
from __future__ import annotations

import json

from voicelab.harness.privacy import observed_metadata

SENTINELS = ('NONCE-SENTINEL', 'AUTH-SENTINEL', 'HEADER-SENTINEL', 'TEXT-SENTINEL',
             'PCM-SENTINEL', 'TOKEN-SENTINEL', 'OPAQUE-SENTINEL', 'TRANSCRIPT-SENTINEL')


def _producer_record() -> dict:
    nested_payload = json.dumps({
        'type': 'transcript', 'text': 'TEXT-SENTINEL', 'audio': 'PCM-SENTINEL',
        'nonce': 'NONCE-SENTINEL', 'headers': {'Authorization': 'AUTH-SENTINEL'},
        'detail': {'authorization': 'AUTH-SENTINEL', 'code': 'provider_error',
                   'opaque_vendor_blob': {'anything': 'OPAQUE-SENTINEL'}}})
    return {
        'kind': 'provider_event', 'run_id': 'regression-run', 'sequence': 3,
        'payload': nested_payload,
        'message_metadata': {'byte_length': 42, 'X-Voicelab-Nonce': 'NONCE-SENTINEL',
                             'transcript': 'TRANSCRIPT-SENTINEL', 'direction': 'inbound'},
        'events': [{'kind': 'audio_received', 'pcm16': 'PCM-SENTINEL',
                    'session_token': 'TOKEN-SENTINEL', 'sequence': 4}],
        'request': {'headers': {'Cookie': 'HEADER-SENTINEL'}, 'status': 'observed'},
        'vendor_extension': {'opaque': 'OPAQUE-SENTINEL'},
        'raw_audio': 'PCM-SENTINEL', 'transcripts': False,
    }


def test_projection_strips_nested_and_opaque_content_before_persistence():
    """what_bug_this_catches: a producer copying a provider payload verbatim - nested JSON
    strings, headers, nonce, auth, transcript text or PCM, and unknown vendor fields - into
    persisted evidence. privacy.py is a closed allowlist projection: content keys are dropped
    at every depth, JSON payload strings are decoded and re-projected, unknown keys vanish."""
    projected = observed_metadata(_producer_record())
    serialized = json.dumps(projected, sort_keys=True)
    for sentinel in SENTINELS:
        assert sentinel not in serialized, sentinel
    assert 'vendor_extension' not in projected
    assert projected['transcripts'] is False
    assert 'raw_audio' not in projected
    assert projected['payload'] == {'type': 'transcript', 'detail': {'code': 'provider_error'}}
    assert projected['message_metadata'] == {'byte_length': 42, 'direction': 'inbound'}
    assert projected['events'] == [{'kind': 'audio_received', 'sequence': 4}]
    assert projected['request'] == {'status': 'observed'}


def test_unparseable_or_non_object_payload_is_dropped_not_kept_as_text():
    """what_bug_this_catches: an undecodable payload string (opaque provider text) persisted
    as-is because JSON decoding failed. JSON containers keep only decoded objects."""
    for payload in ('OPAQUE-SENTINEL not json', json.dumps(['OPAQUE-SENTINEL'])):
        projected = observed_metadata({'kind': 'provider_event', 'payload': payload})
        assert projected == {'kind': 'provider_event'}
