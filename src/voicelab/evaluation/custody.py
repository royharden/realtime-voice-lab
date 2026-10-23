"""Import externally issued metadata; never create custody seals or appointments."""
from __future__ import annotations

from pathlib import Path
import json
from typing import Literal

from voicelab.contracts import Hash, Identifier, Model

from .artifacts import canonical_bytes, digest
from .gates import freeze_matches, release_matches
from .exposure import ExposureJournal, PrivateExposureUse, root_binding
from .trust import ExternalAuthority, externally_authorized


class PublicCustodyManifest(Model):
    """Public pins/config only: no assignments, wording, speakers, outcomes or audio."""
    run_id: Identifier
    mode: Literal['mock', 'replay', 'live']
    split: Literal['dev', 'heldout']
    code_hash: Hash
    build_hash: Hash
    config_hash: Hash
    schema_hash: Hash
    fixture_manifest_hash: Hash
    trial_plan_hash: Hash
    fixture_seal: str | None
    thresholds: dict[str, object]
    endpoint: dict[str, object]
    onset: dict[str, object]
    manifest_sha256: Hash


def import_custody(bundle: dict, root: str | Path, *, expected_freeze_hash: str,
                   expected_release_hash: str, authority: ExternalAuthority | None = None,
                   excluded_actor_refs: frozenset[str] = frozenset(),
                   journal: ExposureJournal | None = None) -> tuple[dict, PrivateExposureUse]:
    """Import public DTOs without opening protected artifacts or consuming a use.

    Enter the returned PrivateExposureUse to durably account intent and request
    actual independent redemption. Pass that same active object to prepare_inputs;
    the existing verify_release seam acknowledges it without redeeming again.
    Serialized import/accounting receipts never authorize reads or resume a use.
    """
    # These DTOs are explicitly public import metadata, supplied separately from
    # protected files. Import never opens plan/fixture/manifest/audio artifacts.
    manifest = PublicCustodyManifest.model_validate(bundle['public_manifest']).model_dump(mode='json')
    references = bundle['artifacts']
    if (references['manifest']['sha256'] != manifest['manifest_sha256']
            or references['fixtures']['sha256'] != manifest['fixture_manifest_hash']):
        raise ValueError('custody fixture manifest hash mismatch')
    freeze, release = (json.loads(canonical_bytes(bundle[key])) for key in ('public_freeze', 'public_release'))
    if digest(freeze) != expected_freeze_hash or digest(release) != expected_release_hash:
        raise ValueError('externally supplied custody digest pin mismatch')
    if not freeze_matches(manifest, freeze, expected_freeze_hash) or not release_matches(manifest, release, expected_freeze_hash):
        raise ValueError('custody freeze/release scope does not match exact manifest')
    scope = {'code_hash': manifest['code_hash'], 'build_hash': manifest['build_hash'],
             'config_hash': manifest['config_hash'], 'schema_hash': manifest['schema_hash'],
             'trial_plan_hash': manifest['trial_plan_hash'], 'manifest_sha256': manifest['manifest_sha256'],
             'fixture_manifest_hash': manifest['fixture_manifest_hash'], 'run_id': manifest['run_id'],
             'freeze_hash': expected_freeze_hash, 'release_hash': expected_release_hash,
             'owned_root_binding': release.get('owned_root_binding'),
             'journal_root_binding': journal.binding if journal is not None else None,
             'artifact_scope_hash': digest({name: ref for name, ref in references.items() if name not in {'freeze', 'release'}})}
    freeze_verified = externally_authorized(authority, 'custody_freeze', freeze, scope, excluded_actor_refs)
    if freeze_verified and root_binding(root) != scope['owned_root_binding']:
        raise ValueError('imported custody record does not bind actual owned root')
    # Do not consume a single-use release merely by importing/viewing metadata.
    result = {'schema_version': 1, 'kind': 'external_custody_import', 'scope': scope,
              'source_hashes': {'freeze': expected_freeze_hash, 'release': expected_release_hash},
              'split_audit': None,
              'freeze_authority_verified': freeze_verified,
              'release_status': 'unconsumed; existing gate must authenticate actual single use',
              'independent_release_verified': False, 'product_acceptance': False,
              'limitations': ['private split metadata not opened; split audit follows actual redemption']
                            + ([] if freeze_verified else ['real nonauthor freeze issuer/appointment not externally verified'])}

    def recheck_freeze() -> bool:
        if not freeze_verified or not freeze_matches(manifest, freeze, expected_freeze_hash):
            return False
        return externally_authorized(authority, 'custody_freeze', freeze, scope, excluded_actor_refs)

    use = PrivateExposureUse(root, references, scope, release, authority=authority, journal=journal,
                             freeze_recheck=recheck_freeze, excluded_actor_refs=excluded_actor_refs)
    return result, use
