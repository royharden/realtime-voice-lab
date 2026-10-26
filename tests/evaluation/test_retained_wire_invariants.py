"""Canonical retained invariants survive redaction; no omitted content is reconstructed."""
from copy import deepcopy
import json

from fastapi.testclient import TestClient
import pytest

from voicelab.contracts import parse_server
from voicelab.evaluation.wire import validate_server_metadata
from voicelab.harness.privacy import observed_metadata
from voicelab.server.app import Settings, create_app


def envelope(kind, payload):
    return {'type': kind, 'protocol_version': 1, 'session_id': 'public-session',
            'connection_epoch': 0, 'response_epoch': 0, 'sequence': 0, 'payload': payload}


def check_valid(message):
    parse_server(json.dumps(message))
    validate_server_metadata(observed_metadata(message))


def check_invalid(message, reason):
    with pytest.raises(ValueError, match=reason):
        parse_server(json.dumps(message))
    # A recomputed producer receipt cannot replace these independent retained semantics.
    with pytest.raises(ValueError):
        validate_server_metadata(observed_metadata(message))


@pytest.mark.parametrize('persisted', [False, True])
def test_audio_persisted_flag_and_artifact_hash_must_agree(persisted):
    """what_bug_this_catches: PCM removal also removing the hash/persistence invariant."""
    message = envelope('audio', {'type': 'audio', 'sequence': 0, 'connection_epoch': 0,
        'response_epoch': 0, 'turn_id': 'public-turn', 'response_id': 'public-response',
        'sample_rate': 24000, 'pcm16': 'AAAA', 'synthetic': True, 'content_hash': '0' * 64,
        'artifact_persisted': persisted, 'artifact_hash': '1' * 64 if persisted else None})
    check_valid(message)
    message['payload']['artifact_persisted'] = not persisted
    check_invalid(message, 'audio_artifact_persistence_mismatch')


@pytest.mark.parametrize('opened', [False, True])
def test_licensed_open_approval_and_asset_observation_must_agree(opened):
    """what_bug_this_catches: omitting authorization fields also dropping retained open semantics."""
    control = {'kind': 'licensed_fixed_pcm', 'config_hash': '0' * 64, 'catalog_hash': '1' * 64,
        'asset_hashes': {'public-asset': '2' * 64}, 'authorization_reference': 'public-declaration',
        'capability_reference': 'public-capability',
        'approval_observation': 'external_verifier_accepted' if opened else 'configuration_only_not_verified',
        'asset_observation': 'local_wav_and_pcm_hashes_verified_at_open' if opened else None,
        'provider_generation_cost_usd': '0', 'provider_cost_scope': 'no_provider_component_only',
        'other_costs': 'unknown', 'task_applicability': 'not_applicable',
        'spoken_correctness': 'unknown', 'model_stages': 'not_applicable',
        'translation': 'identity_noop_no_duration'}
    admission = {'mode': 'mock', 'run_id': 'public-run', 'manifest_hashes': {},
        'requested_models': {}, 'expected_returned_models': {}, 'actually_returned_models': {},
        'budget_usd': '0', 'budget_owner': {'scope': 'mock_local_run', 'owner_id': 'public-boot',
                                          'lease_observation': 'unknown'}, 'licensed_control': control}
    message = envelope('ready', {'mode': 'mock', 'arm': 'K', 'evidence_label': 'public fixture',
        'trial_namespace': 'public-trial', 'trial_id': 'public-trial', 'run_id': 'public-run',
        'boot_id': 'public-boot', 'admission': admission,
        'evidence_reference': '/sessions/public-session/evidence'})
    check_valid(message)  # Declarations here test schema only; they authorize no actual asset read.
    control['asset_observation'] = None if opened else 'local_wav_and_pcm_hashes_verified_at_open'
    check_invalid(message, 'licensed_control_open_observation_mismatch')


@pytest.fixture
def ended(tmp_path):
    """Obtain a valid canonical terminal from an in-process keyless relay, never a listener."""
    nonce = 'retained-public-nonce-0123456789abcdef'
    origin = 'http://127.0.0.1:22300'
    app = create_app(Settings(nonce=nonce, allowed_origin=origin, artifact_root=tmp_path / 'm6-runtime'))
    with TestClient(app, base_url=origin) as client:
        with client.websocket_connect('ws://127.0.0.1:22300/ws?nonce=' + nonce, headers={'origin': origin}) as ws:
            ws.send_json({'type': 'hello', 'protocol_version': 1, 'mode': 'mock', 'arm': 'C', 'nonce': nonce})
            for _ in range(16):
                if ws.receive_json()['type'] == 'ready':
                    break
            else:
                raise AssertionError('bounded ready missing')
            ws.send_json({'type': 'end'})
            for _ in range(32):
                message = ws.receive_json()
                if message['type'] == 'ended':
                    return message
            raise AssertionError('bounded ended missing')


@pytest.mark.parametrize('mismatch', ['missing_ledger', 'unknown_reference'])
def test_ended_ledger_and_snapshot_reference_must_agree(ended, mismatch):
    """what_bug_this_catches: redaction dropping the terminal ledger/reference consistency guard."""
    check_valid(ended)
    message = deepcopy(ended)
    assert message['payload']['ledger'] is not None
    assert message['payload']['ledger_artifact']['observation'] == 'observed'
    if mismatch == 'missing_ledger':
        message['payload']['ledger'] = None
    else:
        message['payload']['ledger_artifact'] = {'observation': 'unknown', 'reason': 'ledger_snapshot_unavailable'}
    check_invalid(message, 'ended_ledger_snapshot_reference_mismatch')
