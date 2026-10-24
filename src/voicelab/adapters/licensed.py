"""Conditional fixed licensed K playback. No recognizer, model, provider or intent inference."""
from __future__ import annotations

import asyncio
import base64
from decimal import Decimal
import hashlib
import inspect
from pathlib import Path
import time

from voicelab.contracts import LicensedControlConfig, ReservationTerms
from voicelab.harness.catalog import _canonical, _read, _relative, _root, _wav
from voicelab.security import safe_artifact_path


LABEL = 'licensed fixed PCM control; descriptive receipt/render only; no speech understanding'
STATES = {'information', 'slots_offered', 'awaiting_confirmation', 'booked', 'complete'}
BASIS = 'No provider generation or provider egress; license, study and local compute costs excluded/unknown'


class LicensedControlRefused(ValueError):
    """Only this module's fixed refusal codes enter telemetry."""

    def __init__(self, code):
        allowed = {'licensed_control_authorization_expired', 'licensed_control_synchronous_approval_required',
            'licensed_control_external_approval_refused', 'licensed_control_not_open',
            'licensed_control_intent_unavailable', 'licensed_control_state_unmapped',
            'licensed_control_tool_intent_unavailable'}
        self.code = code if code in allowed else 'licensed_control_refused'
        super().__init__(self.code)


def validate_k_config(raw: dict | None) -> dict:
    """Validate declarations only. No filesystem reads, approvals or provider probes."""
    if not isinstance(raw, dict):
        raise ValueError('licensed_control_configuration_required')
    verifier = raw.get('approval_verifier')
    if not callable(verifier) or inspect.iscoroutinefunction(verifier):
        raise ValueError('licensed_control_external_approval_verifier_required')
    spec = LicensedControlConfig.model_validate({k: v for k, v in raw.items() if k != 'approval_verifier'})
    value = spec.model_dump(mode='json')
    ids = {asset.asset_id for asset in spec.assets}
    if len(ids) != len(spec.assets) or set(spec.state_assets) != STATES or not set(spec.state_assets.values()) <= ids:
        raise ValueError('licensed_control_closed_state_mapping_required')
    if spec.expires_at_unix <= time.time():
        raise ValueError('licensed_control_authorization_expired')
    root = Path(spec.asset_root)
    if (not root.is_absolute() or '://' in spec.asset_root or root.drive.startswith('\\')
            or spec.asset_root.startswith(('//', '\\\\'))):
        raise ValueError('licensed_control_local_absolute_root_required')
    for asset in spec.assets:
        _relative(asset.path)
        if (Decimal(asset.duration_seconds) <= 0 or
                abs(Decimal(asset.duration_seconds) - Decimal(asset.frame_count) / 24000) > Decimal(1) / 24000):
            raise ValueError('licensed_control_duration_mismatch')
    catalog = {'assets': value['assets'], 'state_assets': value['state_assets']}
    if hashlib.sha256(_canonical(catalog)).hexdigest() != spec.catalog_sha256:
        raise ValueError('licensed_control_catalog_hash_mismatch')
    config_hash = hashlib.sha256(_canonical(value)).hexdigest()
    metadata = {'kind': 'licensed_fixed_pcm', 'config_hash': config_hash,
        'catalog_hash': spec.catalog_sha256,
        'asset_hashes': {asset.asset_id: asset.pcm_sha256 for asset in spec.assets},
        'authorization_reference': spec.authorization_reference,
        'capability_reference': spec.capability_reference,
        'approval_observation': 'configuration_only_not_verified',
        'provider_generation_cost_usd': '0', 'provider_cost_scope': 'no_provider_component_only',
        'other_costs': 'unknown', 'task_applicability': 'not_applicable',
        'spoken_correctness': 'unknown', 'model_stages': 'not_applicable',
        'translation': 'identity_noop_no_duration'}
    manifests = {'authorization': {'reference': spec.authorization_reference,
        'budget_owner_scope': 'single_owner_run', 'owner_lease_reference': spec.owner_lease_reference},
        'capabilities': {'reference': spec.capability_reference, 'control_config_hash': config_hash,
            'control_catalog_hash': spec.catalog_sha256},
        'rights': {'assets': [{key: asset[key] for key in ('asset_id', 'license_reference',
            'output_rights_reference', 'approval_reference')} for asset in value['assets']]},
        'pricing': {'scope': 'no_provider_component_only', 'version': 'local-control-zero-v1'}}
    return {'mode': 'live', 'licensed_control': metadata, 'control_spec': value,
        'approval_verifier': verifier, 'manifests': manifests, 'models': {},
        'run_id': spec.run_id, 'budget_usd': spec.budget_usd, 'evidence_label': LABEL}


class LicensedKSession:
    def __init__(self, *, task_authority, trial_namespace, ledger, config):
        if ledger.budget != Decimal(config['budget_usd']):
            raise ValueError('licensed_control_ledger_budget_mismatch')
        self.authority, self.namespace, self.ledger = task_authority, trial_namespace, ledger
        self.config = config
        self.connection_epoch = config.get('connection_epoch', 0)
        self.response_epoch = config.get('response_epoch', 0)
        self.sequence = 0
        self.closed = False
        self.opened = False
        self.queue = asyncio.Queue(maxsize=4)
        self.assets = {}
        self.bindings = {}
        self.input_samples = {}
        self.counter = 0
        self.response_id = None
        self.sent_samples = 0
        self.rendered_samples = 0
        self.attempts = set()
        self.observed_control = None

    async def open(self, config=None):
        spec = LicensedControlConfig.model_validate(self.config['control_spec'])
        if self.closed or spec.expires_at_unix <= time.time():
            raise LicensedControlRefused('licensed_control_authorization_expired')
        # External verifier must authenticate genuine approval/rights; references alone do not.
        context = {'config_hash': self.config['licensed_control']['config_hash'],
            'catalog_hash': spec.catalog_sha256, 'run_id': spec.run_id,
            'run_manifest_hash': self.config['run_manifest_hash'],
            'session_id': self.config['session_id'], 'trial_id': self.namespace,
            'authorization_reference': spec.authorization_reference,
            'capability_reference': spec.capability_reference,
            'owner_lease_reference': spec.owner_lease_reference, 'budget_owner_id': self.ledger.owner_id,
            'budget_usd': spec.budget_usd, 'expires_at_unix': spec.expires_at_unix,
            'assets': [{k: v for k, v in asset.model_dump(mode='json').items() if k != 'path'}
                for asset in spec.assets]}
        result = self.config['approval_verifier'](context)
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise LicensedControlRefused('licensed_control_synchronous_approval_required')
        if result is not True:
            raise LicensedControlRefused('licensed_control_external_approval_refused')
        root = _root(Path(spec.asset_root))
        loaded = {}
        for asset in spec.assets:
            # Common path gate also rejects hard links and ancestor escapes.
            path = safe_artifact_path(root, asset.path)
            if path.stat().st_nlink != 1:
                raise ValueError('licensed_control_asset_link_refused')
            raw, _ = _read(root, {'path': asset.path, 'sha256': asset.sha256}, 384044, 'licensed_output')
            declared = {k: v for k, v in asset.model_dump(mode='json').items()
                if k in {'path', 'sha256', 'pcm_sha256', 'format', 'channels', 'sample_rate', 'frame_count', 'duration_seconds'}}
            _wav(raw, declared)
            # Reviewed parser requires exactly fmt(16), data; no other WAV chunks.
            loaded[asset.asset_id] = raw[44:]
        self.assets = loaded
        self.opened = True
        self.observed_control = {**self.config['licensed_control'],
            'approval_observation': 'external_verifier_accepted',
            'asset_observation': 'local_wav_and_pcm_hashes_verified_at_open',
            'independent_verification': 'unknown'}

    def bind_turn(self, turn_id, logical_turn_id):
        if len(self.bindings) >= 8 and turn_id not in self.bindings:
            raise ValueError('turn_limit')
        self.bindings[turn_id] = logical_turn_id

    async def append_audio(self, frame):
        if not self.opened or self.closed:
            raise LicensedControlRefused('licensed_control_not_open')
        raw = base64.b64decode(frame['pcm16'], validate=True)
        if frame['sample_rate'] != 24000 or not raw or len(raw) % 2 or len(raw) > 9600:
            raise ValueError('unsupported_audio')
        turn = frame['turn_id']
        self.input_samples[turn] = self.input_samples.get(turn, 0) + len(raw) // 2
        if self.input_samples[turn] > 360000:
            raise ValueError('input_audio_overflow')
        # No interpretation, input persistence or synthetic transcript.

    async def _emit(self, event, turn, epoch, connection):
        if self.closed or epoch != self.response_epoch or connection != self.connection_epoch:
            raise asyncio.CancelledError
        self.sequence += 1
        await self.queue.put({**event, 'turn_id': turn, 'sequence': self.sequence,
            'response_epoch': epoch, 'connection_epoch': connection})

    async def commit_turn(self, turn_id, *, text=None, logical_turn_id=None):
        if text is not None or not self.opened or self.closed:
            raise LicensedControlRefused('licensed_control_intent_unavailable')
        if self.config['control_spec']['expires_at_unix'] <= time.time():
            raise LicensedControlRefused('licensed_control_authorization_expired')
        logical = logical_turn_id or self.bindings.get(turn_id, turn_id)
        epoch, connection = self.response_epoch, self.connection_epoch
        # State is read from actual authority. K never submits guessed commands or confirms.
        task = self.authority.snapshot(self.namespace)
        asset_id = self.config['control_spec']['state_assets'].get(task['state'])
        if asset_id not in self.assets:
            raise LicensedControlRefused('licensed_control_state_unmapped')
        self.counter += 1
        attempt = 'at-' + hashlib.sha256(f'{self.config["session_id"]}:{turn_id}:{self.counter}'.encode()).hexdigest()[:32]
        response = 'r-' + hashlib.sha256(attempt.encode()).hexdigest()[:32]
        terms = ReservationTerms(mode='local_control', component='canned', model='licensed-fixed-pcm',
            currency='USD', price_version='local-control-zero-v1', pricing_reference='no-provider-generation',
            capability_reference=self.config['control_spec']['capability_reference'],
            enforcement_reference=self.config['licensed_control']['config_hash'], bound_usd='0', basis=BASIS,
            rates={'provider_requests': '0'}, usage_bounds={'provider_requests': '0'},
            usage_units={'provider_requests': 'requests'})
        self.ledger.reserve(attempt, amount=Decimal(0), logical_turn_id=logical, arm='K', component='canned',
            price_version=terms.price_version, basis=BASIS, terms=terms, trial_id=self.namespace,
            session_id=self.config['session_id'], transport_turn_id=turn_id,
            connection_epoch=connection, response_epoch=epoch)
        self.attempts.add(attempt)
        try:
            self.ledger.authorize_dispatch(attempt)
            self.ledger.ensure_dispatch_allowed(attempt)
            # Zero is a construction fact for this no-provider component, not a study bill.
            self.ledger.settle(attempt, amount=Decimal(0), usage={'provider_requests': '0'}, source='local_no_provider_component')
            self.response_id, self.sent_samples, self.rendered_samples = response, 0, 0
            await self._emit({'type': 'task', 'outcome': {**task, 'ok': None, 'effect_committed': False,
                'control_only': True, 'task_applicability': 'not_applicable', 'spoken_correctness': 'unknown',
                'code': 'licensed_fixed_control_no_intent_inference', 'asset_id': asset_id,
                'catalog_hash': self.config['licensed_control']['catalog_hash']}}, turn_id, epoch, connection)
            raw = self.assets[asset_id]
            for offset in range(0, len(raw), 4800):
                chunk = raw[offset:offset + 4800]
                # Render ACK backpressure; no silent replay or invented heard boundary.
                while self.sent_samples + len(chunk) // 2 - self.rendered_samples > 24000:
                    if self.closed or epoch != self.response_epoch or connection != self.connection_epoch:
                        raise asyncio.CancelledError
                    await asyncio.sleep(0.01)
                self.sent_samples += len(chunk) // 2
                await self._emit({'type': 'audio', 'response_id': response,
                    'pcm16': base64.b64encode(chunk).decode('ascii'), 'sample_rate': 24000,
                    'asset_id': asset_id, 'evidence_label': LABEL}, turn_id, epoch, connection)
            await self._emit({'type': 'event', 'kind': 'response_done', 'response_id': response,
                'control_only': True, 'model_stages': 'not_applicable',
                'translation': 'identity_noop_no_duration'}, turn_id, epoch, connection)
        except BaseException:
            self.ledger.mark_unknown(attempt, reason='local_control_cancelled_or_failed')
            raise

    def acknowledge_rendered(self, response_id, sample):
        if response_id == self.response_id and self.rendered_samples <= sample <= self.sent_samples:
            self.rendered_samples = sample

    async def submit_tool_result(self, operation_id, result):
        raise LicensedControlRefused('licensed_control_tool_intent_unavailable')

    def _drain(self):
        while not self.queue.empty():
            self.queue.get_nowait()

    async def cancel(self, response_id, response_epoch, rendered_cutoff):
        if response_epoch <= self.response_epoch:
            raise ValueError('stale_cancel_epoch')
        self.response_epoch = response_epoch
        self.response_id = None
        self._drain()

    async def resume(self, connection_epoch, rendered_ack):
        if connection_epoch <= self.connection_epoch:
            raise ValueError('stale_connection_epoch')
        self.connection_epoch = connection_epoch
        self._drain()
        # No replay from a partial/unknown heard boundary. Relay requests a fresh turn.

    async def events(self):
        while True:
            event = await self.queue.get()
            if event is None:
                return
            yield event

    async def close(self):
        self.closed = True
        self.assets.clear()
        self._drain()
        self.queue.put_nowait(None)
