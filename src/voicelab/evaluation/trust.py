"""Operator-supplied external authority boundary. No local authority is provided."""
from __future__ import annotations

from typing import Literal, Protocol, get_args

AuthorityPurpose = Literal['manual_decision', 'custody_freeze', 'custody_release',
    'cohort_authorization', 'live_authorization', 'fixture_rights', 'calibration_evidence']
PURPOSES = frozenset(get_args(AuthorityPurpose))


class ExternalAuthority(Protocol):
    def verify(self, purpose: AuthorityPurpose, record: dict, scope: dict) -> bool:
        """Authenticate exact issuance, appointment, nonauthor lineage and bound scope.

        A roster/name/hash/JSON boolean alone is insufficient. Implementations must
        resolve real actor identity and implementation/parent/child/fork exclusions.
        Custody release verification also owns independent single-use redemption.
        No input artifact may dynamically load an implementation of this protocol.
        """
        ...


def externally_authorized(authority: ExternalAuthority | None, purpose: AuthorityPurpose,
                          record: dict, scope: dict, excluded_actor_refs: frozenset[str]) -> bool:
    if purpose not in PURPOSES:
        raise ValueError('unsupported external authority purpose')
    actor = record.get('actor_ref') if purpose == 'manual_decision' else record.get('issuer_actor_ref')
    if not actor or actor in excluded_actor_refs or not record.get('appointment_ref') or authority is None:
        return False
    return authority.verify(purpose, record, {**scope, 'excluded_actor_refs': sorted(excluded_actor_refs),
        'coding_lineage_may_not_supply_W7_W8': purpose in {'manual_decision', 'custody_freeze', 'custody_release'}}) is True


def verify_gate_record(purpose: AuthorityPurpose, record: dict, scope: dict, *, expected_record_hash: str,
                       authority: ExternalAuthority | None = None,
                       excluded_actor_refs: frozenset[str] = frozenset()) -> bool:
    """Shared cohort/live/rights/calibration proof boundary, not a caller-flag gate."""
    from .artifacts import digest
    if purpose not in {'cohort_authorization', 'live_authorization', 'fixture_rights', 'calibration_evidence'}:
        raise ValueError('use canonical manual/custody workflow for this purpose')
    common = ('run_id', 'code_hash', 'build_hash', 'config_hash', 'fixture_manifest_hash', 'trial_plan_hash')
    if not all(scope.get(key) for key in common):
        return False
    if purpose == 'live_authorization' and not all(scope.get(key) for key in ('pricing_manifest_hash', 'authorization_reference')):
        return False
    if purpose == 'calibration_evidence' and not scope.get('calibration_hash'):
        return False
    if (record.get('schema_version') != 1 or record.get('purpose') != purpose or not record.get('reference')
            or record.get('bindings') != scope or digest(record) != expected_record_hash):
        return False
    return externally_authorized(authority, purpose, record,
        {**scope, 'expected_record_hash': expected_record_hash}, excluded_actor_refs)
