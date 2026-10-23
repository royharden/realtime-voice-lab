"""Operator-supplied external authority boundary. No local authority is provided."""
from __future__ import annotations

from typing import Protocol


class ExternalAuthority(Protocol):
    def verify(self, purpose: str, record: dict, scope: dict) -> bool:
        """Authenticate exact issuance, appointment, nonauthor lineage and bound scope.

        A roster/name/hash/JSON boolean alone is insufficient. Implementations must
        resolve real actor identity and implementation/parent/child/fork exclusions.
        Custody release verification also owns independent single-use redemption.
        No input artifact may dynamically load an implementation of this protocol.
        """
        ...


def externally_authorized(authority: ExternalAuthority | None, purpose: str,
                          record: dict, scope: dict, excluded_actor_refs: frozenset[str]) -> bool:
    actor = record.get('issuer_actor_ref') if purpose.startswith('custody_') else record.get('actor_ref')
    if not actor or actor in excluded_actor_refs or not record.get('appointment_ref') or authority is None:
        return False
    return authority.verify(purpose, record, {**scope, 'excluded_actor_refs': sorted(excluded_actor_refs),
                                            'coding_lineage_may_not_supply_W7_W8': True}) is True
