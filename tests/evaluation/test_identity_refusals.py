"""Planned/actual identity and fixture mismatches refuse rather than relabel. NOT YET RUN.

Plan sections 3/6: RunManifest pins identities; provider/fixture changes require a new cohort.
"""
from __future__ import annotations

import hashlib
import json
import time

import pytest

from voicelab.adapters.licensed import validate_k_config
from voicelab.contracts import LicensedControlConfig, ReturnedIdentity
from voicelab.reporting.reports import build_report

STATES = ('information', 'slots_offered', 'awaiting_confirmation', 'booked', 'complete')


def test_terminal_row_trial_identity_conflict_refuses(report_builders):
    """what_bug_this_catches: a terminal row whose observed trial differs from the
    prespecified assignment being silently relabeled with the planned trial (or vice versa),
    moving a result between cohorts. reports.py raises instead of overwriting."""
    b = report_builders
    manifest = b.run_manifest(['turn-00'], [b.scheduled_turn('turn-00')])
    row = b.clean_success_row('turn-00', 400.0, trial_id='trial-other')
    with pytest.raises(ValueError, match='conflicts with prespecified turn assignment: trial_id'):
        build_report(manifest, [row], b.empty_ledger())


def test_ledger_attempt_arm_conflict_refuses(report_builders):
    """what_bug_this_catches: a V-arm attempt cost being attributed to a C-arm scheduled turn
    (cross-arm cost relabeling). The prespecified arm wins only by refusal, not rewrite."""
    b = report_builders
    manifest = b.run_manifest(['turn-00'], [b.scheduled_turn('turn-00')])
    ledger = b.empty_ledger()
    ledger['attempts'] = [{'attempt_id': 'attempt-1', 'logical_turn_id': 'turn-00', 'arm': 'V',
                           'component': 'native', 'price_version': 'synthetic-v1',
                           'state': 'settled', 'settled': '0', 'reservation': '0'}]
    with pytest.raises(ValueError, match='ledger arm conflicts with prespecified assignment'):
        build_report(manifest, [b.clean_success_row('turn-00', 400.0)], ledger)


def test_row_from_another_run_refuses(report_builders):
    """what_bug_this_catches: pooling a result row from a different run into this run's
    denominator because its logical turn id happens to match."""
    b = report_builders
    manifest = b.run_manifest(['turn-00'], [b.scheduled_turn('turn-00')])
    row = b.clean_success_row('turn-00', 400.0, run_id='other-run')
    with pytest.raises(ValueError, match='result row from different run'):
        build_report(manifest, [row], b.empty_ledger())


def _k_spec(asset_root: str, catalog_sha256: str) -> dict:
    """Synthetic declaration only; validate_k_config reads no files and grants no approval."""
    asset = {'asset_id': 'k-asset', 'path': 'k-asset.wav', 'sha256': '1' * 64,
             'pcm_sha256': '2' * 64, 'format': 'PCM16LE', 'channels': 1, 'sample_rate': 24000,
             'frame_count': 24000, 'duration_seconds': '1',
             'license_reference': 'synthetic-license', 'output_rights_reference': 'synthetic-rights',
             'approval_reference': 'synthetic-approval'}
    return {'run_id': 'regression-run', 'budget_usd': '0', 'asset_root': asset_root,
            'catalog_sha256': catalog_sha256, 'assets': [asset],
            'state_assets': {state: 'k-asset' for state in STATES},
            'authorization_reference': 'synthetic-authorization',
            'capability_reference': 'synthetic-capability',
            'owner_lease_reference': 'synthetic-lease', 'expires_at_unix': int(time.time()) + 3600}


def _catalog_hash(spec: dict) -> str:
    # Mirrors the documented canonical catalog digest (sorted keys, compact, ASCII, LF).
    value = LicensedControlConfig.model_validate(spec).model_dump(mode='json')
    catalog = {'assets': value['assets'], 'state_assets': value['state_assets']}
    canonical = json.dumps(catalog, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                           allow_nan=False) + '\n'
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _never_approves(*_args, **_kwargs) -> bool:
    return False


def test_k_fixture_catalog_mismatch_refuses(tmp_path):
    """what_bug_this_catches: a licensed K fixture catalog whose content differs from the
    pinned catalog hash being accepted (or re-hashed and relabeled) as the approved control.
    licensed.py refuses with licensed_control_catalog_hash_mismatch."""
    spec = _k_spec(str(tmp_path.resolve()), '0' * 64)
    with pytest.raises(ValueError, match='licensed_control_catalog_hash_mismatch'):
        validate_k_config({**spec, 'approval_verifier': _never_approves})


def test_k_config_match_stays_configuration_only_not_verified(tmp_path):
    """what_bug_this_catches: a matching declaration being relabeled as externally verified
    approval. Declaration validation alone must report configuration_only_not_verified."""
    spec = _k_spec(str(tmp_path.resolve()), '0' * 64)
    spec['catalog_sha256'] = _catalog_hash(spec)
    config = validate_k_config({**spec, 'approval_verifier': _never_approves})
    assert config['licensed_control']['catalog_hash'] == spec['catalog_sha256']
    assert config['licensed_control']['approval_observation'] == 'configuration_only_not_verified'
    assert 'asset_observation' not in config['licensed_control']


@pytest.mark.parametrize('raw, code', [
    ({'value': None, 'observation': 'observed', 'source': 'provider_session_updated'},
     'returned_identity_observation_mismatch'),
    ({'value': 'synthetic-model', 'observation': 'observed', 'source': 'unknown'},
     'returned_identity_observation_mismatch'),
    ({'value': 'synthetic-model', 'observation': 'unknown', 'source': 'unknown'},
     'unknown_returned_identity_has_no_value'),
])
def test_returned_model_identity_cannot_be_asserted_without_observation(raw, code):
    """what_bug_this_catches: copying the requested/planned model name into the returned
    identity (relabeling) without a provider session.updated observation."""
    with pytest.raises(ValueError, match=code):
        ReturnedIdentity.model_validate(raw)
