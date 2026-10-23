"""Import externally issued metadata; never create custody seals or appointments."""
from __future__ import annotations

from pathlib import Path
from collections.abc import Callable

from voicelab.contracts import RunManifest

from .artifacts import ArtifactReader, digest
from .gates import audit_fixture_split, freeze_matches, release_matches
from .trust import ExternalAuthority, externally_authorized


def import_custody(bundle: dict, root: str | Path, *, expected_freeze_hash: str,
                   expected_release_hash: str, authority: ExternalAuthority | None = None,
                   excluded_actor_refs: frozenset[str] = frozenset()) -> tuple[dict, Callable[[dict], bool]]:
    """Read pinned manifest/fixture metadata/freeze/release only; never expose audio.

    Returned callable supplies the existing gates.verify_release seam. It performs
    real authority authentication/redemption only when gates admit an exact use.
    A serialized structural-import receipt cannot be used as that callable.
    """
    reader = ArtifactReader(root, bundle['artifacts'])
    manifest = RunManifest.model_validate(reader.json('manifest', 'manifest')).model_dump(mode='json')
    if reader.references['fixtures']['sha256'] != manifest['fixture_manifest_hash']:
        raise ValueError('custody fixture manifest hash mismatch')
    freeze = reader.json('freeze', 'freeze')
    release = reader.json('release', 'release')
    if digest(freeze) != expected_freeze_hash or digest(release) != expected_release_hash:
        raise ValueError('externally supplied custody digest pin mismatch')
    if not freeze_matches(manifest, freeze, expected_freeze_hash) or not release_matches(manifest, release, expected_freeze_hash):
        raise ValueError('custody freeze/release scope does not match exact manifest')
    scope = {'code_hash': manifest['code_hash'], 'build_hash': manifest['build_hash'],
             'fixture_manifest_hash': manifest['fixture_manifest_hash'], 'run_id': manifest['run_id'],
             'freeze_hash': expected_freeze_hash, 'release_hash': expected_release_hash}
    freeze_verified = externally_authorized(authority, 'custody_freeze', freeze, scope, excluded_actor_refs)
    split = None
    if freeze_verified:
        fixtures = reader.json('fixtures', 'fixtures')
        split = audit_fixture_split(fixtures)
        if not split['dimensions_complete'] or split['disjoint'] is not True:
            raise ValueError('custody import has missing/overlapping family/speaker/interruption dimensions')
    # Do not consume a single-use release merely by importing/viewing metadata.
    result = {'schema_version': 1, 'kind': 'external_custody_import', 'scope': scope,
              'source_hashes': reader.verified, 'split_audit': split,
              'freeze_authority_verified': freeze_verified,
              'release_status': 'unconsumed; existing gate must authenticate actual single use',
              'independent_release_verified': False, 'product_acceptance': False,
              'limitations': [] if freeze_verified else ['real nonauthor freeze issuer/appointment not externally verified; private split metadata was not opened']}

    def verify_release(candidate: dict) -> bool:
        if not freeze_verified or split is None or digest(candidate) != expected_release_hash:
            return False
        if not externally_authorized(authority, 'custody_freeze', freeze, scope, excluded_actor_refs):
            return False
        return externally_authorized(authority, 'custody_release', candidate, scope, excluded_actor_refs)

    return result, verify_release
