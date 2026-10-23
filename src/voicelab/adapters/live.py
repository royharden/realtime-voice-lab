"""Bounded live source behind explicit manifests; unexecuted and uncertified."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
import re
import inspect
from datetime import datetime, timezone
from decimal import Decimal

from .providers import parse_provider_object, parse_realtime, parse_transcription, pcm_chunk, serialize_command
from .sessions import match_command
from .transport import ProviderTransport
from voicelab.core.task import SLOTS
from voicelab.contracts import ProviderIdentityTag, ReservationTerms
from voicelab.accounting.ledger import BudgetRefused, money
from pydantic import TypeAdapter


def task_answer(outcome: dict) -> str:
    """Only human task information is spoken; no challenge/journal identifiers."""
    labels = {slot['id']: slot['label'] for slot in SLOTS}
    if not outcome.get('ok'):
        if outcome.get('code') == 'explicit_confirmation_required':
            return 'Your appointment is not confirmed. Explicit confirmation evidence is required.'
        return 'I could not complete that request. Please ask for hours, available slots, a selection, confirmation, or status.'
    if outcome.get('hours'):
        return 'We are open ' + outcome['hours'] + '.'
    if outcome.get('slots'):
        return 'The available appointments are ' + ' and '.join(labels[s['id']] for s in outcome['slots']) + '.'
    booking = outcome.get('booking')
    if booking:
        return 'Your appointment is confirmed for ' + labels[booking['slot_id']] + '.'
    if outcome.get('slot_id'):
        return 'You selected ' + labels[outcome['slot_id']] + '. Please explicitly confirm this appointment.'
    return 'No appointment is booked. Ask for available slots to begin.'


def validate_live_config(config: dict, arm: str) -> dict:
    """Pure no-I/O structural admission. References require operator verification.

    No model/voice/price is supplied by this program. Caller pins all of them.
    Expiry is checked separately immediately before every network operation.
    """
    if config.get('mode') != 'live' or arm not in {'V', 'C'}:
        raise ValueError('live_gate_closed')
    manifests = config.get('manifests', {})
    for name in ('authorization', 'capabilities', 'pricing', 'rights', 'evaluation'):
        if not isinstance(manifests.get(name), dict) or not manifests[name].get('reference'):
            raise ValueError('live_gate_missing_' + name)
    auth, caps, price, rights, evaluation = (manifests[k] for k in
        ('authorization', 'capabilities', 'pricing', 'rights', 'evaluation'))
    budget = Decimal(str(auth.get('budget_usd', 'NaN')))
    if auth.get('approved') is not True or auth.get('scope') != 'M6-live' or not budget.is_finite() or budget <= 0:
        raise ValueError('live_budget_authorization_required')
    money(budget)
    expiry = datetime.fromisoformat(auth.get('expires_at', '').replace('Z', '+00:00'))
    if expiry.tzinfo is None:
        raise ValueError('authorization expiry must include timezone')
    if datetime.now(timezone.utc) >= expiry:
        raise ValueError('live authorization expired')
    if (caps.get('verified') is not True or caps.get('manual_endpointing') is not True
            or caps.get('sample_rate') != 24000 or (arm == 'V' and caps.get('native_cancel_truncate') is not True)):
        raise ValueError('live_capability_gate_closed')
    if rights.get('approved') is not True or rights.get('voice_output_redistribution') is not True:
        raise ValueError('live_rights_gate_closed')
    for field in ('custody_reference', 'reviewer_reference', 'offline_gate_reference', 'calibration_gate_reference'):
        if not evaluation.get(field):
            raise ValueError('live_evaluation_gate_closed')
    if price.get('currency') != 'USD' or not price.get('version'):
        raise ValueError('live_price_gate_closed')
    if caps.get('attempt_bounds_verified') is not True or not callable(config.get('attempt_bound_verifier')):
        raise ValueError('live_attempt_bound_verifier_required')
    if auth.get('budget_owner_scope') != 'single_owner_run' or not isinstance(auth.get('owner_lease_reference'), str) or not 1 <= len(auth['owner_lease_reference']) <= 200:
        raise ValueError('live_budget_owner_lease_reference_required')
    components = ['native'] if arm == 'V' else ['stt', 'tts']
    models = config.get('models', {})
    try:
        if not isinstance(models, dict) or set(models) - {'native', 'stt', 'tts'}:
            raise ValueError
        for model in models.values():
            TypeAdapter(ProviderIdentityTag).validate_python(model)
        if config.get('voice') is not None:
            TypeAdapter(ProviderIdentityTag).validate_python(config['voice'])
    except (ValueError, TypeError):
        raise ValueError('configured_provider_identity_invalid') from None
    reservation_terms = {}
    for component in components:
        if not models.get(component) or caps.get('supported_models', {}).get(component) != models[component]:
            raise ValueError('verified model identity required')
        if component != 'tts' and not caps.get('returned_models', {}).get(component):
            raise ValueError('verified returned model identity required')
        if component != 'tts':
            try:
                TypeAdapter(ProviderIdentityTag).validate_python(caps['returned_models'][component])
            except ValueError:
                raise ValueError('expected_returned_identity_invalid') from None
        tariff = price.get('components', {}).get(component, {})
        bound = Decimal(str(tariff.get('bound_usd', 'NaN')))
        if not bound.is_finite() or bound <= 0 or not tariff.get('bound_basis') or not tariff.get('rates'):
            raise ValueError('enforceable priced attempt bound required')
        money(bound)
        for rate in tariff['rates'].values():
            value = Decimal(str(rate))
            if not value.is_finite() or value < 0:
                raise ValueError('invalid usage tariff')
        try:
            reservation_terms[component] = ReservationTerms(mode='live', component=component,
                model=models[component], currency='USD', price_version=price['version'],
                pricing_reference=price['reference'], capability_reference=caps['reference'],
                enforcement_reference=tariff['enforcement_reference'], bound_usd=format(bound, 'f'),
                basis=tariff['bound_basis'], rates={path: str(rate) for path, rate in tariff['rates'].items()},
                usage_bounds=tariff['usage_bounds'], usage_units=tariff['usage_units']).model_dump(mode='json')
        except (ValueError, KeyError, TypeError):
            raise ValueError('known_dimensioned_enforceable_price_terms_required') from None
    if arm == 'C' and (not config.get('voice') or caps.get('verified_voice') != config['voice']):
        raise ValueError('verified voice identity required')
    component = 'native' if arm == 'V' else 'stt'
    update = config.get('session_update', {}).get(component)
    if not isinstance(update, dict) or update.get('type') != 'session.update':
        raise ValueError('pinned manual-endpoint session update required')
    session = update.get('session', {})
    audio_input = session.get('audio', {}).get('input', {})
    if audio_input.get('turn_detection', 'missing') is not None or audio_input.get('format') != {'type': 'audio/pcm', 'rate': 24000}:
        raise ValueError('matched manual endpoint and PCM24k required')
    try:
        update_raw = json.dumps(update, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
        if len(update_raw) > 131072:
            raise ValueError
    except (ValueError, TypeError, UnicodeError):
        raise ValueError('provider_session_update_bound') from None
    if caps.get('session_update_sha256', {}).get(component) != hashlib.sha256(update_raw).hexdigest():
        raise ValueError('session update differs from verified capability manifest')
    if caps.get('connection_metering') != 'no_charge_verified':
        raise ValueError('connection billing needs a separately reviewed reservation design')
    if arm == 'V' and caps.get('response_metadata_echo') is not True:
        raise ValueError('verified response metadata fencing required')
    run_id = config.get('run_id') or evaluation.get('run_id') or auth.get('run_id')
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,96}', run_id):
        raise ValueError('pinned live run identity required')
    copied = {key: json.loads(json.dumps(config[key])) for key in ('manifests', 'models', 'session_update')}
    return {**config, **copied, 'run_id': run_id, 'budget_usd': format(budget, 'f'),
            'reservation_terms': reservation_terms,
            'evidence_label': 'authorized live source — runtime unverified; independent acceptance pending'}


class LiveArmSession:
    def __init__(self, arm, *, task_authority, trial_namespace, ledger, config):
        self.config = validate_live_config(config, arm)
        if ledger.budget != Decimal(self.config['budget_usd']):
            raise ValueError('ledger budget differs from authorization')
        self.arm, self.authority, self.namespace, self.ledger = arm, task_authority, trial_namespace, ledger
        self.transport = config.get('transport') or ProviderTransport()
        self.transport_source = 'provider_network' if type(self.transport) is ProviderTransport else 'injected_unverified'
        self.ws = None
        self.closed = False
        self.queue = asyncio.Queue(maxsize=64)
        self.connection_epoch = config.get('connection_epoch', 0)
        self.response_epoch = config.get('response_epoch', 0)
        self.attempts = {}
        self.all_attempts = []
        self.bindings = {}
        self.counter = 0
        self.sequence = 0
        self.input_samples = 0
        self.response_item = {}
        self.input_artifacts = {}
        self.input_pcm = {}
        self.observed_returned_models = {}
        self.attempt_components = {}
        self.active_turn = None

    def _guard(self):
        expires = datetime.fromisoformat(self.config['manifests']['authorization']['expires_at'].replace('Z', '+00:00'))
        if self.closed or datetime.now(timezone.utc) >= expires:
            raise ValueError('live authorization expired or session closed')

    def _credential(self, component):
        self._guard()
        provider = self.config.get('credential_provider')
        if not callable(provider):
            raise ValueError('server-only injected credential provider required')
        try:
            value = provider(component)
        except Exception:
            raise ValueError('credential_unavailable') from None
        if not isinstance(value, str) or not 1 <= len(value) <= 8192 or any(not 33 <= ord(c) <= 126 for c in value):
            raise ValueError('credential unavailable')
        return value

    async def open(self, config=None):
        if config and any(k not in {'mode'} for k in config):
            raise ValueError('live manifest cannot change after admission')
        self._guard()
        component = 'native' if self.arm == 'V' else 'stt'
        self.ws = await self.transport.connect(component, self.config['models'][component], self._credential(component))
        try:
            self._guard()
            await self.ws.send(json.dumps(self.config['session_update'][component]))
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=max(.01, deadline - time.monotonic()))
                event = parse_provider_object(raw)
                if event.get('type') == 'error':
                    raise ValueError('provider session negotiation refused')
                if event.get('type') == 'session.updated':
                    session = event['session']
                    returned = session.get('model') if component == 'native' else session.get('audio', {}).get('input', {}).get('transcription', {}).get('model')
                    if returned != self.config['manifests']['capabilities']['returned_models'][component]:
                        raise ValueError('returned model identity differs from verified cohort')
                    if self.transport_source == 'provider_network':
                        self.observed_returned_models[component] = returned
                    return
            raise ValueError('provider session negotiation timeout')
        except BaseException:
            await self.ws.close()
            self.ws = None
            raise

    def bind_turn(self, turn_id, logical_turn_id):
        if len(self.bindings) >= 8 and turn_id not in self.bindings:
            raise ValueError('turn limit')
        self.bindings[turn_id] = logical_turn_id

    def _reserve(self, turn, component, logical=None, *, force_new=False):
        self._guard()
        key = (turn, component)
        if key in self.attempts and not force_new:
            return self.attempts[key]
        self.counter += 1
        attempt = 'at-' + hashlib.sha256(f'{self.namespace}:{turn}:{component}:{self.counter}'.encode()).hexdigest()[:32]
        price = self.config['manifests']['pricing']
        tariff = price['components'][component]
        self.ledger.reserve(attempt, amount=Decimal(str(tariff['bound_usd'])),
            logical_turn_id=logical or self.bindings.get(turn, turn), arm=self.arm, component=component,
            price_version=price['version'], basis=tariff['bound_basis'],
            terms=self.config['reservation_terms'][component], trial_id=self.namespace)
        self.attempts[key] = attempt
        self.all_attempts.append(attempt)
        self.attempt_components[attempt] = component
        return attempt

    def _authorize(self, attempt, component, turn, *, outgoing_message_bytes=None, outgoing_text_bytes=None):
        self._guard()
        verifier = self.config.get('attempt_bound_verifier')
        context = {'attempt_id': attempt, 'trial_namespace': self.namespace,
            'transport_turn_id': turn, 'logical_turn_id': self.bindings.get(turn, turn),
            'component': component, 'response_epoch': self.response_epoch,
            'connection_epoch': self.connection_epoch,
            'owner_id': self.ledger.owner_id,
            'budget_owner_scope': self.config['manifests']['authorization']['budget_owner_scope'],
            'terms': json.loads(json.dumps(self.config['reservation_terms'][component])),
            'captured_turn_samples': sum(len(frame) // 2 for frame in self.input_pcm.get(turn, ())),
            'session_input_samples': self.input_samples, 'max_turn_input_samples': 360000,
            'max_response_samples': 240000, 'max_session_seconds': 120,
            'outgoing_message_bytes': outgoing_message_bytes, 'outgoing_text_bytes': outgoing_text_bytes,
            'owner_lease_reference': self.config['manifests']['authorization']['owner_lease_reference']}
        try:
            verification = verifier(context) if callable(verifier) else False
        except Exception:
            raise BudgetRefused('attempt_bound_enforcement_refused') from None
        if inspect.isawaitable(verification):
            if inspect.iscoroutine(verification):
                verification.close()
            raise BudgetRefused('synchronous_attempt_bound_verifier_required')
        if verification is not True:
            raise BudgetRefused('attempt_bound_enforcement_refused')
        self.ledger.authorize_dispatch(attempt)
        self.ledger.ensure_dispatch_allowed(attempt)

    async def _billable_send(self, attempt, component, turn, payload):
        if not isinstance(payload, str) or len(payload.encode('utf-8')) > 131072:
            raise ValueError('provider_protocol_error')
        self._authorize(attempt, component, turn, outgoing_message_bytes=len(payload.encode('utf-8')))
        try:
            await self.ws.send(payload)
            self.ledger.record_sent(attempt, transport_source=self.transport_source)
        except BaseException as exc:
            self.ledger.mark_unknown(attempt, reason='canceled' if isinstance(exc, asyncio.CancelledError) else 'dispatch_outcome_unknown')
            raise

    async def append_audio(self, frame):
        self._guard()
        if self.ws is None:
            await self.open({'mode': 'live'})
        turn = frame['turn_id']
        artifact = frame.get('input_artifact_hash')
        if not isinstance(artifact, str) or not re.fullmatch(r'[a-f0-9]{64}', artifact):
            raise ValueError('trusted captured input artifact reference required')
        references = self.input_artifacts.setdefault(turn, [])
        if len(references) >= 8192:
            raise ValueError('input artifact frame bound')
        references.append(artifact)
        component = 'native' if self.arm == 'V' else 'stt'
        raw = base64.b64decode(frame['pcm16'], validate=True)
        pcm_chunk(raw, sample_rate=frame['sample_rate'])
        if hashlib.sha256(raw).hexdigest() != artifact or frame.get('input_artifact_persisted') is not False:
            raise ValueError('expected trusted ephemeral input digest')
        self.input_pcm.setdefault(turn, []).append(raw)
        self.input_samples += len(raw) // 2
        if self.input_samples > 24000 * 120:
            raise ValueError('live input duration bound')
        attempt = self._reserve(turn, component)
        await self._billable_send(attempt, component, turn, serialize_command('append', audio=frame['pcm16']))

    async def _emit(self, event, turn, epoch):
        if epoch != self.response_epoch or self.closed:
            return
        self.sequence += 1
        event.update(turn_id=turn, response_epoch=epoch, connection_epoch=self.connection_epoch, sequence=self.sequence)
        self.queue.put_nowait(event)

    def _settle(self, turn, component, usage):
        attempt = self.attempts[(turn, component)]
        self._settle_attempt(attempt, component, usage)

    def _settle_attempt(self, attempt, component, usage):
        if self.attempt_components.get(attempt) != component:
            self.ledger.mark_unknown(attempt, reason='usage_component_mismatch')
            return
        if self.transport_source != 'provider_network':
            self.ledger.mark_unknown(attempt, reason='injected_transport_usage_unverified')
            return
        self.ledger.settle_usage(attempt, usage=usage, source='provider_terminal_usage')

    async def commit_turn(self, turn_id, *, text=None, logical_turn_id=None):
        if text is not None:
            raise ValueError('debug text refused in live mode')
        self._guard()
        logical = logical_turn_id or self.bindings.get(turn_id, turn_id)
        self.bind_turn(turn_id, logical)
        self.active_turn = turn_id
        component = 'native' if self.arm == 'V' else 'stt'
        self._reserve(turn_id, component, logical)
        epoch = self.response_epoch
        try:
            attempt = self.attempts[(turn_id, component)]
            await self._billable_send(attempt, component, turn_id, serialize_command('commit'))
            metadata = {'voicelab_request': self.attempts[(turn_id, component)], 'voicelab_epoch': str(epoch)}
            if self.arm == 'V':
                await self._billable_send(attempt, component, turn_id, json.dumps({'type': 'response.create', 'response': {'metadata': metadata}}))
            deadline = time.monotonic() + 10
            emitted_audio = False
            pending_tools = []
            tool_rounds = 0
            active_response = None
            input_item = None
            while time.monotonic() < deadline:
                self._guard()
                raw = await asyncio.wait_for(self.ws.recv(), timeout=max(.01, deadline - time.monotonic()))
                # Control events are explicitly ignored; unknown data events fail closed.
                decoded_wire = parse_provider_object(raw)
                kind = decoded_wire.get('type')
                if kind == 'input_audio_buffer.committed':
                    input_item = decoded_wire['item_id']
                    continue
                if kind == 'response.created':
                    response = decoded_wire['response']
                    if response.get('metadata') == metadata:
                        active_response = response['id']
                    continue
                if kind == 'response.done':
                    response = decoded_wire['response']
                    if response.get('metadata') != metadata or response.get('id') != active_response:
                        late_attempt = response.get('metadata', {}).get('voicelab_request')
                        if late_attempt in self.all_attempts:
                            self._settle_attempt(late_attempt, 'native', response.get('usage'))
                        continue
                if kind in {'response.output_audio.delta', 'response.function_call_arguments.done'}:
                    if not active_response or decoded_wire.get('response_id') != active_response:
                        continue
                if kind.startswith('conversation.item.input_audio_transcription.'):
                    if not input_item or decoded_wire.get('item_id') != input_item:
                        continue
                if kind in {'session.created', 'session.updated',
                            'response.output_item.added', 'response.content_part.added',
                            'response.output_audio.done', 'response.output_item.done', 'response.content_part.done',
                            'response.output_audio_transcript.delta', 'response.output_audio_transcript.done',
                            'response.function_call_arguments.delta', 'rate_limits.updated'}:
                    continue
                event = parse_realtime(raw) if self.arm == 'V' else parse_transcription(raw)
                if event['type'] == 'error':
                    raise ValueError('provider_error')
                if event['type'] == 'transcript.final':
                    await self._emit({'type': 'transcript', 'text': event['text']}, turn_id, epoch)
                    if self.arm == 'C':
                        self._settle(turn_id, 'stt', None)  # No invented STT usage from transcript.
                        await self._cascade(turn_id, logical, event['text'], epoch)
                        return
                elif event['type'] == 'tool.call':
                    outcome = self._task(logical, turn_id, event['name'], event['arguments'], epoch)
                    await self._emit({'type': 'task', 'outcome': outcome}, turn_id, epoch)
                    pending_tools.append((event['operation_id'], outcome))
                    if len(pending_tools) > 8:
                        raise ValueError('tool proposal bound')
                elif event['type'] == 'audio.delta':
                    decoded = decoded_wire
                    self.response_item[event['response_id']] = decoded['item_id']
                    emitted_audio = True
                    await self._emit({'type': 'audio', **{k: v for k, v in event.items() if k != 'type'}}, turn_id, epoch)
                elif event['type'] == 'response.done':
                    self._settle(turn_id, 'native', event.get('usage'))
                    if pending_tools and emitted_audio:
                        raise ValueError('mixed audio/tool response requires verified history handling')
                    if event['status'] == 'completed' and pending_tools and not emitted_audio:
                        tool_rounds += 1
                        if tool_rounds > 8:
                            raise ValueError('tool round bound')
                        self._reserve(turn_id, 'native', logical, force_new=True)
                        for operation, result in pending_tools:
                            await self.submit_tool_result(operation, result)
                        pending_tools.clear()
                        self._guard()
                        metadata = {'voicelab_request': self.attempts[(turn_id, component)], 'voicelab_epoch': str(epoch)}
                        active_response = None
                        await self._billable_send(self.attempts[(turn_id, component)], component, turn_id,
                            json.dumps({'type': 'response.create', 'response': {'metadata': metadata}}))
                        continue
                    if event['status'] != 'completed' or not emitted_audio:
                        raise ValueError('provider_empty_or_unsuccessful_response')
                    await self._emit({'type': 'event', 'kind': 'response_done', 'response_id': event['response_id']}, turn_id, epoch)
                    return
            raise ValueError('provider_timeout')
        except BaseException:
            for attempt in self.all_attempts:
                self.ledger.mark_unknown(attempt, reason='dispatch_outcome_unknown')
            raise
        finally:
            self.input_pcm.pop(turn_id, None)
            self.input_artifacts.pop(turn_id, None)

    def _task(self, logical, transport_turn, command, arguments, epoch):
        evidence = self.config.get('confirmation_evidence')
        if command == 'confirm_slot':
            snapshot = self.authority.snapshot(self.namespace)
            if any(key in arguments and arguments[key] != snapshot[key]
                   for key in ('slot_id', 'challenge', 'state_version')):
                return {'ok': False, 'code': 'stale_confirmation_proposal'}
            arguments = {key: snapshot[key] for key in ('slot_id', 'challenge', 'state_version')}
        context = {'trial_namespace': self.namespace, 'logical_turn_id': logical,
            'transport_turn_id': transport_turn, 'response_epoch': epoch, 'command': command,
            'arguments': arguments, 'slot_id': arguments.get('slot_id'),
            'challenge': arguments.get('challenge'), 'state_version': arguments.get('state_version'),
            'input_artifact_hashes': tuple(self.input_artifacts.get(transport_turn, ())),
            'input_pcm16': b''.join(self.input_pcm.get(transport_turn, ())), 'sample_rate': 24000,
            'input_artifact_persisted': False, 'evidence_kind': 'ephemeral captured input digest/PCM',
            'input_evidence_reference': self.config.get('input_evidence_references', {}).get(transport_turn)}
        verification = evidence(context) if context['input_pcm16'] and callable(evidence) else False
        if inspect.isawaitable(verification):
            if inspect.iscoroutine(verification):
                verification.close()
            raise ValueError('confirmation verifier must be synchronous and return literal bool')
        confirmed = verification is True
        if command == 'confirm_slot' and not confirmed:
            return {'ok': False, 'code': 'explicit_confirmation_required'}
        return self.authority.execute(self.namespace, command, arguments, logical_turn_id=logical,
            response_epoch=epoch, explicit_confirmation=confirmed)

    async def _cascade(self, turn, logical, transcript, epoch):
        controller_start = time.monotonic_ns()
        command, arguments, lexical_confirmation = match_command(transcript)
        if command == 'confirm_slot':
            snapshot = self.authority.snapshot(self.namespace)
            arguments = {k: snapshot[k] for k in ('slot_id', 'challenge', 'state_version')}
        outcome = self._task(logical, turn, command, arguments, epoch) if command else {
            'ok': False, 'code': 'clarification', 'message': 'Please clarify your request.'}
        await self._emit({'type': 'event', 'kind': 'controller_span', 'start_ns': controller_start,
            'end_ns': time.monotonic_ns(), 'clock_domain': 'server_monotonic_ns',
            'translation': 'identity_noop'}, turn, epoch)
        await self._emit({'type': 'task', 'outcome': outcome}, turn, epoch)
        # A complete deterministic answer is batched before streaming TTS.
        answer = task_answer(outcome)
        if len(answer) > 2000:
            raise ValueError('TTS input bound')
        attempt = self._reserve(turn, 'tts', logical)
        self._authorize(attempt, 'tts', turn, outgoing_text_bytes=len(answer.encode('utf-8')))
        response_id = 'r-' + hashlib.sha256(f'{turn}:{self.counter}'.encode()).hexdigest()[:32]
        count = 0
        await self._emit({'type': 'event', 'kind': 'tts_request', 'timestamp_ns': time.monotonic_ns(),
                         'clock_domain': 'server_monotonic_ns'}, turn, epoch)
        async for chunk in self.transport.speech(self.config['models']['tts'], self.config['voice'], answer, self._credential('tts')):
            if epoch != self.response_epoch:
                raise ValueError('stale response')
            audio = pcm_chunk(chunk)
            count += 1
            if count == 1:
                self.ledger.record_sent(attempt, boundary='first_tts_pcm_received', transport_source=self.transport_source)
                await self._emit({'type': 'event', 'kind': 'tts_first_pcm', 'timestamp_ns': time.monotonic_ns(),
                                 'clock_domain': 'server_monotonic_ns'}, turn, epoch)
            await self._emit({'type': 'audio', 'response_id': response_id, 'pcm16': audio['pcm16'], 'sample_rate': 24000}, turn, epoch)
        self._settle(turn, 'tts', None)  # Raw PCM HTTP body exposes no authoritative token usage.
        if not count:
            raise ValueError('empty TTS response')
        await self._emit({'type': 'event', 'kind': 'response_done', 'response_id': response_id}, turn, epoch)

    async def submit_tool_result(self, operation_id, result):
        self._guard()
        turn = self.active_turn
        if turn is None or (turn, 'native') not in self.attempts:
            raise ValueError('tool_output_requires_reserved_attempt')
        public = {'ok': result.get('ok', False), 'message': task_answer(result)}
        await self._billable_send(self.attempts[(turn, 'native')], 'native', turn,
            json.dumps({'type': 'conversation.item.create', 'item': {
                'type': 'function_call_output', 'call_id': operation_id, 'output': json.dumps(public)}}))

    async def cancel(self, response_id, response_epoch, rendered_cutoff):
        if response_epoch <= self.response_epoch:
            raise ValueError('stale cancel epoch')
        self.response_epoch = response_epoch
        self.input_pcm.clear()
        self.input_artifacts.clear()
        if self.ws and self.arm == 'V':
            self._guard()
            await self.ws.send(serialize_command('cancel', response_id=response_id) if response_id else
                               json.dumps({'type': 'response.cancel'}))
            item = self.response_item.get(response_id)
            if not item:
                raise ValueError('native truncation identity unknown; unrecovered')
            await self.ws.send(serialize_command('truncate', item_id=item, rendered_cutoff=rendered_cutoff))
        if self.ws and self.arm == 'V':
            await self.ws.send(json.dumps({'type': 'input_audio_buffer.clear'}))
            deadline = time.monotonic() + 2
            for _ in range(100):
                raw = await asyncio.wait_for(self.ws.recv(), timeout=max(.01, deadline - time.monotonic()))
                received = parse_provider_object(raw)
                if received.get('type') == 'response.done':
                    response = received.get('response', {})
                    attempt = response.get('metadata', {}).get('voicelab_request')
                    if attempt in self.all_attempts:
                        self._settle_attempt(attempt, 'native', response.get('usage'))
                if received.get('type') == 'input_audio_buffer.cleared':
                    break
            else:
                raise ValueError('input clear acknowledgement unknown; unrecovered')
        elif self.ws:
            await self.ws.close()
            self.ws = None  # C restarts STT before next input; no stale transcript adoption.
        while not self.queue.empty():
            self.queue.get_nowait()
        for attempt in self.all_attempts:
            self.ledger.mark_unknown(attempt, reason='canceled')

    async def resume(self, connection_epoch, rendered_ack):
        # Ambiguous provider conversation state is never silently reconstructed.
        raise ValueError('recovery_unknown')

    async def events(self):
        while True:
            event = await self.queue.get()
            if event is None:
                return
            yield event

    async def close(self):
        self.closed = True
        self.input_pcm.clear()
        self.input_artifacts.clear()
        for attempt in self.all_attempts:
            self.ledger.mark_unknown(attempt, reason='dispatch_outcome_unknown')
        if self.ws:
            await self.ws.close()
        while not self.queue.empty():
            self.queue.get_nowait()
        self.queue.put_nowait(None)
