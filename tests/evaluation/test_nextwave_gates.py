"""Synthetic negative controls for public rights, split and W7/W8 authority seams.

No real corpus, custody record, credential, provider or external issuer is read.
Test authorities below are explicit doubles and cannot issue real permission.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from voicelab.evaluation.artifacts import digest
from voicelab.evaluation.custody import import_custody
from voicelab.evaluation.exposure import ExposureRefused, PublicReadAccess, root_binding
from voicelab.evaluation.gates import freeze_pins
from voicelab.evaluation.trust import externally_authorized, verify_gate_record
from voicelab.harness.access import public_reader
from voicelab.harness.cli import split_audit


class Authority:
    """Only a recording test double; not an external identity/rights verifier."""
    def __init__(self, result=True):
        self.result, self.calls = result, []

    def verify(self, purpose, record, scope):
        self.calls.append((purpose, deepcopy(record), deepcopy(scope)))
        return self.result


@pytest.mark.parametrize('purpose', ['manual_decision', 'custody_freeze', 'custody_release'])
@pytest.mark.parametrize('defect', ['absent_authority', 'coding_lineage', 'no_appointment', 'refused'])
def test_w7_w8_metadata_cannot_self_certify(purpose, defect):
    """what_bug_this_catches: a JSON approval/reference or coding actor supplying W7/W8."""
    record = {'actor_ref': 'coding-seat', 'issuer_actor_ref': 'coding-seat',
              'appointment_ref': 'synthetic-appointment', 'approved': True,
              'independent': True, 'product_acceptance': True}
    authority = Authority(defect != 'refused')
    if defect == 'no_appointment':
        record.pop('appointment_ref')
    assert externally_authorized(None if defect == 'absent_authority' else authority,
        purpose, record, {'run_id': 'test-only'},
        frozenset({'coding-seat'}) if defect == 'coding_lineage' else frozenset()) is False
    if defect != 'refused':
        assert authority.calls == []


def test_external_w7_w8_boundary_receives_lineage_exclusions():
    """what_bug_this_catches: omitting nonauthor/parent/fork exclusions at the verifier seam."""
    authority = Authority()
    assert externally_authorized(authority, 'manual_decision',
        {'actor_ref': 'independent-test-double', 'appointment_ref': 'test-only'}, {},
        frozenset({'author', 'author-child', 'author-parent'})) is True
    scope = authority.calls[0][2]
    assert scope['coding_lineage_may_not_supply_W7_W8'] is True
    assert scope['excluded_actor_refs'] == ['author', 'author-child', 'author-parent']


@pytest.mark.parametrize('value', [None, {}, {'approved': True},
    {'independent_release_verified': True, 'freeze_authority_verified': True}])
def test_cached_or_self_asserted_receipt_cannot_open_public_reader(tmp_path, value):
    """what_bug_this_catches: accepting a serialized success receipt as actual access."""
    with pytest.raises(ValueError, match='actual_D5_PublicReadAccess_required'):
        public_reader(tmp_path, {}, value)
    assert list(tmp_path.iterdir()) == []


def public_scope(root):
    now = datetime.now(timezone.utc)
    return {'run_id': 'test-only', **{key: '0' * 64 for key in
        ('code_hash', 'build_hash', 'config_hash', 'fixture_manifest_hash', 'trial_plan_hash')},
        'split': 'dev', 'access_classification': 'public_dev_only',
        'owned_root_binding': root_binding(root), 'ownership_reference': 'test-only',
        'artifact_scope_hash': digest({}),
        'window': {'starts_at': (now - timedelta(minutes=1)).isoformat(),
                   'expires_at': (now + timedelta(minutes=1)).isoformat()}}


def records(scope):
    return {purpose: {'schema_version': 1, 'purpose': purpose, 'reference': 'test-only',
                     'issuer_actor_ref': 'test-double', 'appointment_ref': 'test-only',
                     'bindings': deepcopy(scope)}
            for purpose in ('cohort_authorization', 'fixture_rights')}


@pytest.mark.parametrize('defect', ['missing_authority', 'rights_refused', 'expired',
    'future', 'inventory_changed', 'source_changed', 'missing_rights', 'heldout', 'private'])
def test_public_synthetic_input_still_requires_current_exact_rights(tmp_path, defect):
    """what_bug_this_catches: a public/synthetic label bypassing scope, rights or expiry."""
    scope = public_scope(tmp_path)
    issued = records(scope)
    hashes = {key: digest(value) for key, value in issued.items()}
    class SelectiveAuthority(Authority):
        def verify(self, purpose, record, scope):
            result = super().verify(purpose, record, scope)
            return result and not (defect == 'rights_refused' and purpose == 'fixture_rights')

    authority = SelectiveAuthority()
    if defect == 'expired':
        scope['window']['expires_at'] = '2000-01-01T00:00:00Z'
    if defect == 'future':
        scope['window']['starts_at'] = '2999-01-01T00:00:00Z'
    if defect == 'inventory_changed':
        scope['artifact_scope_hash'] = 'f' * 64
    if defect == 'source_changed':
        scope['code_hash'] = 'f' * 64
    if defect == 'missing_rights':
        issued.pop('fixture_rights')
    if defect == 'heldout':
        scope['split'] = 'heldout'
    if defect == 'private':
        scope['access_classification'] = 'private'
    access = PublicReadAccess(None if defect == 'missing_authority' else authority,
                              issued, hashes, scope)
    with pytest.raises(ExposureRefused):
        access.reader(tmp_path, {})
    if defect == 'rights_refused':
        assert [call[0] for call in authority.calls] == ['cohort_authorization', 'fixture_rights']
    assert list(tmp_path.iterdir()) == []


def test_exact_test_authority_positive_control_is_not_product_acceptance(tmp_path):
    """what_bug_this_catches: an always-refuse test or an unbound rights verifier call."""
    scope = public_scope(tmp_path)
    issued = records(scope)
    authority = Authority()
    access = PublicReadAccess(authority, issued, {k: digest(v) for k, v in issued.items()}, scope)
    reader = access.reader(tmp_path, {})
    assert reader.references == {}
    assert [c[0] for c in authority.calls] == ['cohort_authorization', 'fixture_rights']
    assert all(c[2]['code_hash'] == scope['code_hash'] for c in authority.calls)


@pytest.mark.parametrize('purpose', ['manual_decision', 'custody_freeze', 'custody_release'])
def test_generic_rights_gate_cannot_substitute_for_canonical_w7_w8_workflow(purpose):
    """what_bug_this_catches: routing independent custody/manual checks through public rights."""
    with pytest.raises(ValueError, match='canonical manual/custody workflow'):
        verify_gate_record(purpose, {}, {}, expected_record_hash='0' * 64,
                           authority=Authority())


@pytest.mark.parametrize('dimension', ['wording_family', 'speaker', 'interruption_schedule'])
def test_split_collision_and_unknown_are_distinct_from_disjoint(dimension):
    """what_bug_this_catches: counting missing dimensions as a clean heldout split."""
    dev = {'wording_family': 'dev-family', 'speaker': 'dev-speaker',
           'interruption_schedule': 'dev-interruption'}
    heldout = {key: 'heldout-' + value for key, value in dev.items()}
    positive = split_audit({'development': [dev], 'heldout': [heldout]})
    assert positive['disjoint'] is True and positive['product_acceptance'] is False
    heldout[dimension] = dev[dimension]
    collision = split_audit({'development': [dev], 'heldout': [heldout]})
    assert collision['disjoint'] is False
    assert collision['dimension_collisions'][dimension] == [dev[dimension]]
    heldout.pop(dimension)
    unknown = split_audit({'development': [dev], 'heldout': [heldout]})
    assert unknown['disjoint'] is None and unknown['dimensions_complete'] is False
    assert unknown['heldout_release'] is False


def test_imported_public_custody_metadata_never_redeems_or_reads_without_authority(tmp_path):
    """what_bug_this_catches: treating a hash-correct import as custody release/acceptance."""
    manifest = {'run_id': 'synthetic-only', 'mode': 'mock', 'split': 'heldout',
                **{key: '0' * 64 for key in ('code_hash', 'build_hash', 'config_hash',
                'schema_hash', 'fixture_manifest_hash', 'trial_plan_hash', 'manifest_sha256')},
                'fixture_seal': 'synthetic-seal', 'thresholds': {}, 'endpoint': {}, 'onset': {}}
    freeze = {'pins': freeze_pins(manifest), 'frozen_before_exposure': True,
              'issuer_actor_ref': 'coding-seat', 'appointment_ref': 'self-asserted'}
    release = {'run_id': manifest['run_id'], 'build_hash': manifest['build_hash'],
               'freeze_hash': digest(freeze), 'fixture_manifest_hash': manifest['fixture_manifest_hash'],
               'seal': manifest['fixture_seal'], 'candidate_hash': manifest['code_hash'],
               'single_use_authorized': True, 'reference': 'self-asserted'}
    references = {key: {'path': key + '.json', 'kind': key, 'sha256': '0' * 64}
                  for key in ('manifest', 'fixtures')}
    receipt, use = import_custody({'public_manifest': manifest, 'public_freeze': freeze,
        'public_release': release, 'artifacts': references}, tmp_path,
        expected_freeze_hash=digest(freeze), expected_release_hash=digest(release))
    assert receipt['freeze_authority_verified'] is False
    assert receipt['independent_release_verified'] is False
    assert receipt['product_acceptance'] is False and receipt['split_audit'] is None
    with pytest.raises(ExposureRefused, match='real_authority_and_durable_journal_required'):
        with use:
            pytest.fail('must refuse before entering the private-use lifetime')
    assert list(tmp_path.iterdir()) == []
