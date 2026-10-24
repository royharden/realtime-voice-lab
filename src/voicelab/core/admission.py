"""Configuration fingerprints and genuinely observed identities; no provider I/O."""
from __future__ import annotations

import hashlib
import json

from voicelab.contracts import AdmissionEvidence


def admission_evidence(*, mode: str, run_id: str, run_manifest_hash: str | None,
                       owner_id: str, config: dict | None = None,
                       observed_models: dict[str, str] | None = None,
                       observed_control: dict | None = None) -> dict:
    config, observed_models = config or {}, observed_models or {}
    manifests = config.get('manifests', {})
    fingerprints = {}
    for kind in ('capabilities', 'pricing', 'authorization', 'rights'):
        manifest = manifests.get(kind)
        if manifest is None:
            fingerprints[kind] = None
            continue
        try:
            raw = json.dumps(manifest, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
            if len(raw) > 131072:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            raise ValueError('manifest_fingerprint_invalid') from None
        fingerprints[kind] = hashlib.sha256(raw).hexdigest()
    auth, caps = manifests.get('authorization', {}), manifests.get('capabilities', {})
    requested = {component: model for component, model in config.get('models', {}).items()
                 if component in {'native', 'stt', 'tts'}}
    actual = {component: ({'value': observed_models[component], 'observation': 'observed',
                         'source': 'provider_session_updated'} if component in observed_models else
                        {'value': None, 'observation': 'unknown', 'source': 'unknown'})
              for component in requested}
    lease = auth.get('owner_lease_reference')
    try:
        return AdmissionEvidence(mode=mode, run_id=run_id, run_manifest_hash=run_manifest_hash,
            manifest_hashes=fingerprints, requested_models=requested, requested_voice=config.get('voice'),
            licensed_control=observed_control or config.get('licensed_control'),
            expected_returned_models={key: value for key, value in caps.get('returned_models', {}).items() if key in requested},
            actually_returned_models=actual, budget_usd=config.get('budget_usd', '0'), budget_owner={
                'scope': auth.get('budget_owner_scope', 'unknown') if mode == 'live' else 'mock_local_run',
                'owner_id': owner_id, 'lease_reference': lease,
                'lease_observation': 'operator_attestation' if lease else 'unknown'}).model_dump(mode='json')
    except ValueError:
        raise ValueError('admission_configuration_invalid') from None
