"""Explicit-manifest metadata-only expiry proposals. No deletion API exists."""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
from typing import Literal

from pydantic import Field, field_validator

from voicelab.contracts import Hash, Identifier, Model

from .artifacts import digest, owned_file_metadata
from .exposure import root_binding, utc
from .trust import ExternalAuthority, verify_gate_record


class RetentionArtifact(Model):
    artifact_id: Identifier
    path: str = Field(min_length=1, max_length=500)
    sha256: Hash
    kind: Literal['audio', 'trace', 'ledger', 'oracle', 'fixture', 'journal', 'metadata']
    role: Literal['input', 'output', 'confirmation', 'metadata']
    approved_synthetic: bool
    policy: Literal['operator_custody_expiry']
    approval_reference: str = Field(min_length=1, max_length=500)
    input_rights_reference: str | None
    output_rights_reference: str | None
    manifest_reference: str = Field(min_length=1, max_length=500)
    custody_reference: str = Field(min_length=1, max_length=500)
    retention_until_utc: str

    @field_validator('path')
    @classmethod
    def owned_relative(cls, value):
        path = Path(value)
        if path.is_absolute() or path.drive or '..' in path.parts or not path.parts or ':' in value:
            raise ValueError('explicit relative owned retention path required')
        return value

    @field_validator('retention_until_utc')
    @classmethod
    def explicit_expiry(cls, value):
        utc(value)
        return value


class RetentionInventory(Model):
    schema_version: Literal[1]
    kind: Literal['retention_inspection_inventory']
    inventory_id: Identifier
    scope: dict[str, object]
    owned_root_binding: dict[str, object]
    ownership_reference: str = Field(min_length=1, max_length=500)
    artifacts: list[RetentionArtifact] = Field(max_length=1000)


def inspect_retention(inventory: dict, root: str | Path, *, as_of: str,
                      authority: ExternalAuthority | None = None, authorization_record: dict | None = None,
                      expected_record_hash: str | None = None,
                      excluded_actor_refs: frozenset[str] = frozenset()) -> dict:
    """Only listed paths; never read artifact bytes, delete, chmod, reset or sweep.

    The requested cutoff is planning metadata, not proof of actual current expiry.
    Actual external ownership/rights/custody bindings are needed even for stat.
    Unknown authority defaults to a refusal plan with no private filesystem access.
    """
    parsed = RetentionInventory.model_validate(inventory).model_dump(mode='json')
    cutoff = utc(as_of)
    entries = parsed['artifacts']
    if len({row['artifact_id'] for row in entries}) != len(entries) or len({row['path'] for row in entries}) != len(entries):
        raise ValueError('duplicate retention artifact identities/paths')
    scope = {**parsed['scope'], 'operation': 'retention_metadata_inspection', 'inventory_hash': digest(parsed),
             'owned_root_binding': parsed['owned_root_binding'], 'ownership_reference': parsed['ownership_reference']}
    def current_authority():
        try:
            window = scope.get('window', {})
            now = datetime.now(timezone.utc)
            return (bool(authorization_record and expected_record_hash)
                and utc(window['starts_at']) <= now < utc(window['expires_at'])
                and verify_gate_record('fixture_rights', authorization_record, scope,
                    expected_record_hash=expected_record_hash or '', authority=authority,
                    excluded_actor_refs=excluded_actor_refs))
        except Exception:
            # An unavailable verifier or unknown expiry never permits a stat.
            return False
    verified = current_authority()
    root_verified = False
    if verified:
        try:
            root_verified = root_binding(root) == parsed['owned_root_binding']
        except (OSError, ValueError):
            root_verified = False
    proposals = []
    for entry in sorted(entries, key=lambda row: row['artifact_id']):
        item = {'artifact_id': entry['artifact_id'], 'path': entry['path'], 'expected_sha256': entry['sha256'],
                'retention_until_utc': entry['retention_until_utc'], 'policy': entry['policy'],
                'approval_reference': entry['approval_reference'], 'custody_reference': entry['custody_reference'],
                'input_rights_reference': entry['input_rights_reference'], 'output_rights_reference': entry['output_rights_reference'],
                'manifest_reference': entry['manifest_reference'], 'content_hash_verified': False,
                'deletion_authorized': False, 'proposed_action': 'refuse', 'reason': None,
                'observed_file_identity': None}
        rights = entry['output_rights_reference'] if entry['role'] == 'output' else entry['input_rights_reference']
        if not verified:
            item['reason'] = 'real external ownership/rights/custody authority missing; no private stat/read performed'
        elif not root_verified:
            item['reason'] = 'owned root identity missing/changed; no artifact access'
        elif not isinstance(rights, str) or not rights:
            item['reason'] = 'role-specific rights evidence missing'
        elif entry['kind'] == 'audio' and entry['approved_synthetic'] is not True:
            item['reason'] = 'manual/unapproved audio not inspectable under synthetic policy'
        elif entry['kind'] == 'journal':
            item.update(proposed_action='preserve_accounting_original', reason='custody/effect/accounting originals are not expiry cleanup targets')
        else:
            try:
                if not current_authority():
                    raise ValueError('retention inspection authority expired or unavailable')
                owned_root = Path(root).resolve(strict=True)
                _, before = owned_file_metadata(owned_root, entry['path'], maximum=(1 << 63) - 1)
                _, after = owned_file_metadata(owned_root, entry['path'], maximum=(1 << 63) - 1)
                if before != after:
                    raise ValueError('retention target identity changed during metadata inspection')
                item['observed_file_identity'] = list(before)
                if cutoff >= utc(entry['retention_until_utc']):
                    item.update(proposed_action='review_expired_owned_artifact',
                                reason='expired as of requested cutoff; exact bytes/ownership/authority must be rechecked before any separately authorized action')
                else:
                    item.update(proposed_action='retain_until_declared_expiry', reason='not expired as of requested cutoff')
            except (OSError, ValueError):
                item['reason'] = 'target missing, symlink/reparse, outside root or racing; no content read'
        proposals.append(item)
    if verified and not current_authority():
        verified, root_verified = False, False
        for item in proposals:
            item.update(proposed_action='refuse', reason='authority expired/unknown during inspection')
    if root_verified:
        try:
            root_verified = root_binding(root) == parsed['owned_root_binding']
        except (OSError, ValueError):
            root_verified = False
        if not root_verified:
            for item in proposals:
                item.update(proposed_action='refuse', reason='owned root changed during inspection')
    return {'schema_version': 1, 'kind': 'retention_inspection_plan', 'inventory_hash': digest(parsed),
            'inventory_id': parsed['inventory_id'], 'as_of': as_of, 'dry_run': True, 'metadata_only': True,
            'authority_verified': bool(verified), 'owned_root_verified': root_verified,
            'proposals': proposals, 'execution_supported': False, 'deletion_authorized': False,
            'artifact_bytes_read': False, 'fresh_set_asserted': False, 'product_acceptance': False,
            'limitations': ['Expected hashes are declarations; this planner does not read or certify file bytes',
                            'No delete/execute/reset/renew/reissue workflow exists; review proposals only',
                            'Exposure intent/result originals and external redemption authority are unchanged']}
