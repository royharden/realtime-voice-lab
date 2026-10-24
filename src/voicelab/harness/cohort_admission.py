"""Consumer of canonical D5 gates/trust and D4 capability/pricing admission."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


class AdmissionRefused(ValueError):
    """Closed code only, never attach provider/asset contents to a refusal."""


def _require_shared_records(authority, records: dict, hashes: dict, scope: dict,
                            excluded_actor_refs: frozenset[str], purposes: tuple[str, ...]) -> bool:
    from voicelab.evaluation.trust import verify_gate_record
    for purpose in purposes:
        if not verify_gate_record(purpose, records.get(purpose, {}), scope,
                expected_record_hash=hashes.get(purpose, ''), authority=authority,
                excluded_actor_refs=excluded_actor_refs):
            raise AdmissionRefused('external_' + purpose + '_unverified')
    return True


def utc(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise AdmissionRefused('cohort_window_timezone_missing')
    return result.astimezone(timezone.utc)


def scope_for(spec: dict, manifest: dict, plan: dict) -> dict:
    from voicelab.evaluation.artifacts import digest
    cohort = spec.get('cohort', {})
    if spec.get('mode') == 'live' and set(spec.get('live_config', {})) - {
            'mode', 'run_id', 'manifests', 'models', 'voice', 'session_update'}:
        raise AdmissionRefused('runtime_capabilities_or_unknown_fields_not_manifest_data')
    if spec.get('cohort_hash') != digest(cohort):
        raise AdmissionRefused('cohort_freeze_hash_mismatch')
    if (spec['run_id'] != manifest['run_id'] or spec['mode'] != manifest['mode']
            or cohort.get('mode') != spec['mode'] or manifest['split'] != 'dev'):
        raise AdmissionRefused('cohort_mode_run_or_split_mismatch')
    if any(spec.get(k) != manifest[k] for k in ('code_hash', 'config_hash')):
        raise AdmissionRefused('cohort_candidate_config_mismatch')
    if not spec.get('fixture_catalog') or not isinstance(spec.get('fixture_catalog_hash'), str):
        raise AdmissionRefused('frozen_reviewed_fixture_catalog_required')
    if cohort.get('models') != manifest['models'] or cohort.get('network') != manifest['network']:
        raise AdmissionRefused('cohort_model_network_pins_mismatch')
    if cohort.get('fault_seed') != manifest['fault_seed']:
        raise AdmissionRefused('cohort_fault_seed_mismatch')
    expected_commit = 'fixture_stream_typed_commit' if spec['mode'] == 'mock' else 'energy_600ms'
    if (cohort.get('commit_policy') != expected_commit
            or manifest['endpoint'].get('commit_policy') != expected_commit):
        raise AdmissionRefused('frozen_commit_policy_differs_from_actual_driver')
    models = cohort['models']
    import re
    if set(models) != {'V', 'C', 'K'} or any(not isinstance(v, str) or not re.fullmatch(r'[A-Za-z0-9_.:/@+-]{1,192}', v) for v in models.values()):
        raise AdmissionRefused('all_pipeline_identities_must_be_explicit')
    profiles = {task['network_profile'] for task in plan['tasks']}
    if len(profiles) != 1 or any(task['model_version'] != models[task['arm']] for task in plan['tasks']):
        raise AdmissionRefused('cohort_tasks_mix_network_or_model_versions')
    from .network import validate_network
    validate_network(cohort['network'], next(iter(profiles)), manifest['fault_seed'])
    start, end = utc(cohort['window']['starts_at']), utc(cohort['window']['expires_at'])
    if not start < end or (end - start).total_seconds() > 21600:
        raise AdmissionRefused('bounded_cohort_window_required')
    if not start <= datetime.now(timezone.utc) < end:
        raise AdmissionRefused('cohort_window_not_open')
    return {'run_id': manifest['run_id'], 'code_hash': manifest['code_hash'],
        'build_hash': manifest['build_hash'], 'config_hash': manifest['config_hash'],
        'schema_hash': manifest['schema_hash'], 'fixture_manifest_hash': manifest['fixture_manifest_hash'],
        'trial_plan_hash': plan['plan_hash'], 'catalog_hash': spec['fixture_catalog_hash'],
        'cohort_hash': spec['cohort_hash'], 'mode': spec['mode'], 'models': models,
        'pricing_manifest_hash': manifest['pricing_manifest_hash'],
        'authorization_reference': manifest['authorization_reference'],
        'live_config_hash': digest(spec.get('live_config')) if spec['mode'] == 'live' else None,
        'k_control_config_hash': digest(spec.get('k_control_config')) if spec['mode'] == 'live' else None,
        'live_owner_root_hash': hashlib.sha256(str(Path(
            spec.get('live_budget_owner', {}).get('root', '')).resolve()).encode()).hexdigest() if spec['mode'] == 'live' else None,
        'network': cohort['network'], 'fault_seed': manifest['fault_seed'],
        'window': cohort['window']}


@dataclass(frozen=True)
class AdmissionGrant:
    """Programmatic trusted verifier binding; JSON booleans cannot construct it."""
    authority: object
    records: dict
    scope: dict
    excluded_actor_refs: frozenset[str]
    live_config_hash: str
    record_hashes: dict
    custody_recheck: object = None

    def recheck(self, spec: dict) -> None:
        from voicelab.evaluation.artifacts import digest
        if spec.get('mode') != 'live' or digest(spec.get('live_config')) != self.live_config_hash:
            raise AdmissionRefused('live_config_changed_after_admission')
        if digest(spec.get('k_control_config')) != self.scope['k_control_config_hash']:
            raise AdmissionRefused('licensed_control_changed_after_cohort_freeze')
        if not utc(self.scope['window']['starts_at']) <= datetime.now(timezone.utc) < utc(self.scope['window']['expires_at']):
            raise AdmissionRefused('live_cohort_window_expired')
        if digest(spec.get('cohort')) != self.scope['cohort_hash'] or spec.get('fixture_catalog_hash') != self.scope['catalog_hash']:
            raise AdmissionRefused('frozen_cohort_configuration_changed')
        import hashlib
        if hashlib.sha256(str(Path(spec.get('live_budget_owner', {}).get('root', '')).resolve()).encode()).hexdigest() != self.scope['live_owner_root_hash']:
            raise AdmissionRefused('live_owner_root_changed_after_admission')
        if self.custody_recheck is None or self.custody_recheck() is not True:
            raise AdmissionRefused('canonical_custody_freeze_unverified')
        _require_shared_records(self.authority, self.records, self.record_hashes, self.scope,
                                self.excluded_actor_refs, ('cohort_authorization', 'fixture_rights', 'live_authorization'))


def prepare_cohort(spec: dict, supplied_plan: dict, bundle: dict, artifact_root: str | Path,
                   expected_freeze_hash: str, *, authority=None,
                   excluded_actor_refs: frozenset[str] = frozenset()) -> tuple[dict, dict, dict, dict, AdmissionGrant | None]:
    # Reuse canonical readers/models/plan/freeze/gates, never install a local verifier.
    from voicelab.evaluation.artifacts import digest
    from voicelab.evaluation.custody import PublicCustodyManifest
    from voicelab.evaluation.exposure import PublicReadAccess, require_output_root
    from voicelab.evaluation.planning import validate_trial_plan
    from voicelab.evaluation.gates import freeze_matches
    from voicelab.evaluation.assembly import prepare_inputs
    public = PublicCustodyManifest.model_validate(bundle['public_manifest']).model_dump(mode='json')
    if public['split'] != 'dev':
        raise AdmissionRefused('heldout_closed_before_plan_fixture_or_label_access')
    access = spec.get('_public_access')
    if not isinstance(access, PublicReadAccess):
        if authority is None:
            raise AdmissionRefused('cohort_external_verifier_absent')
        metadata = bundle['public_access']
        access = PublicReadAccess(authority, metadata['records'], metadata['hashes'], metadata['scope'],
                                  excluded_actor_refs=excluded_actor_refs)
    reader = access.reader(artifact_root, bundle['artifacts'])
    for key in ('run_id', 'code_hash', 'build_hash', 'config_hash', 'fixture_manifest_hash', 'trial_plan_hash'):
        if access.scope.get(key) != public[key]:
            raise AdmissionRefused('public_access_scope_differs_from_public_custody_pins')
    require_output_root(reader, spec['_evidence_root'])
    reader, manifest, frozen_plan, gates = prepare_inputs(bundle, artifact_root,
        expected_freeze_hash=expected_freeze_hash, public_access=access)
    validate_trial_plan(frozen_plan)
    requested = spec.get('_requested_plan_path')
    if requested is not None and Path(requested).resolve() != (reader.root / bundle['artifacts']['plan']['path']).resolve():
        raise AdmissionRefused('requested_plan_path_differs_from_authenticated_inventory')
    if supplied_plan is not None and supplied_plan != frozen_plan:
        raise AdmissionRefused('supplied_plan_differs_from_frozen_plan')
    for pair in frozen_plan['pairs']:
        selected = [task for task in frozen_plan['tasks'] if task['pair_id'] == pair['pair_id']]
        if (len(selected) != 3 or {task['arm'] for task in selected} != {'V', 'C', 'K'}
                or any(task['control'] != (task['arm'] == 'K') for task in selected)):
            raise AdmissionRefused('frozen_pair_control_membership_invalid')
        expected = ['V', 'C', 'K'] if pair['order'] == 'AB' else ['C', 'V', 'K']
        if [task['arm'] for task in selected] != expected:
            raise AdmissionRefused('frozen_AB_BA_execution_order_changed')
    if manifest['split'] != 'dev':
        raise AdmissionRefused('heldout_closed_before_fixture_exposure')
    freeze = bundle['public_freeze']
    if not freeze_matches(manifest, freeze, expected_freeze_hash):
        raise AdmissionRefused('canonical_build_fixture_freeze_unverified')
    expected_scope = {**scope_for(spec, manifest, frozen_plan), 'run_manifest_hash': reader.verified['manifest']}
    scope = access.scope
    if any(scope.get(key) != value for key, value in expected_scope.items()):
        raise AdmissionRefused('public_read_scope_differs_from_frozen_cohort_scope')
    authority = access.authority
    records = dict(access.records)
    hashes = dict(access.hashes)
    def common_gate_check():
        access.check(artifact_root, bundle['artifacts'])
        return _require_shared_records(authority, records, hashes, scope, excluded_actor_refs,
                                       ('cohort_authorization', 'fixture_rights'))
    common_gate_check()
    grant = None
    if spec['mode'] == 'live':
        if authority is None:
            raise AdmissionRefused('live_external_verifier_absent')
        from voicelab.evaluation.custody import import_custody
        def custody_recheck():
            receipt, _ = import_custody(bundle, artifact_root, expected_freeze_hash=expected_freeze_hash,
                expected_release_hash=spec.get('custody_release_hash', ''), authority=authority,
                excluded_actor_refs=excluded_actor_refs)
            return receipt['freeze_authority_verified'] is True
        records['live_authorization'] = bundle['public_live_authorization']
        hashes['live_authorization'] = spec.get('gate_record_hashes', {}).get('live_authorization', '')
        grant = AdmissionGrant(authority, json.loads(json.dumps(records)), json.loads(json.dumps(scope)),
                               excluded_actor_refs, digest(spec.get('live_config')),
                               hashes, custody_recheck)
        grant.recheck(spec)  # dev reads already used PublicReadAccess; live open remains closed
    if not gates['freeze_valid']:
        raise AdmissionRefused('canonical_gate_did_not_admit_frozen_inputs')
    fixtures = {row['fixture_id']: row for row in reader.json('fixtures', 'fixtures')['fixtures']}
    return manifest, frozen_plan, gates, {'scope': scope, 'fixtures': fixtures,
        'reader': reader, 'public_access': access, 'common_gate_check': common_gate_check}, grant


def admit_live_arm(spec: dict, arm: str) -> dict:
    if arm == 'K':
        from decimal import Decimal
        from voicelab.adapters.licensed import validate_k_config
        verifier = spec.get('_k_approval_verifier')
        if not spec.get('k_control_config') or not callable(verifier):
            raise AdmissionRefused('live_K_configuration_or_external_approval_verifier_absent')
        config = validate_k_config({**spec['k_control_config'], 'approval_verifier': verifier})
        auth = spec['live_config']['manifests']['authorization']
        if (config['run_id'] != spec['run_id']
                or config['manifests']['authorization']['owner_lease_reference'] != auth['owner_lease_reference']
                or Decimal(config['budget_usd']) != Decimal(str(auth['budget_usd']))):
            raise AdmissionRefused('licensed_control_run_budget_or_owner_differs_from_serialized_cohort')
        return config
    from voicelab.adapters.live import validate_live_config
    verifier = spec.get('_attempt_bound_verifier')
    if not callable(verifier):
        raise AdmissionRefused('real_attempt_bound_verifier_unavailable')
    config = validate_live_config({**spec['live_config'], 'attempt_bound_verifier': verifier}, arm)
    if config['run_id'] != spec['run_id']:
        raise AdmissionRefused('live_provider_run_identity_mismatch')
    return config
