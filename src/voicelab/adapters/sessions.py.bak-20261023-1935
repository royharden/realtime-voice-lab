from __future__ import annotations

import asyncio
import base64
import math
import re
import struct
import time
import hashlib
from decimal import Decimal

from .wire import parse_wire, serialize_wire
from .providers import mock_audio, mock_transcript, parse_realtime, serialize_command
import json


def match_command(text: str) -> tuple[str | None, dict, bool]:
    """Deliberately narrow lexical baseline; misses remain visible failures."""
    words = ' '.join(re.findall(r"[a-z0-9:]+", text.lower()))
    if words in {'hours', 'what are your hours', 'when are you open'}:
        return 'get_hours', {}, False
    if words in {'slots', 'list slots', 'what appointments are available', 'availability'}:
        return 'list_slots', {}, False
    selected = re.fullmatch(r'(?:select|choose|book) (?:slot )?([12])', words)
    if selected:
        return 'select_slot', {'slot_id': 'slot-a' if selected.group(1) == '1' else 'slot-b'}, False
    if words in {'yes', 'yes confirm', 'confirm', 'i confirm'}:
        if '?' in text:
            return None, {}, False
        return 'confirm_slot', {}, True
    if words in {'status', 'read back', 'what is my booking'}:
        return 'get_status', {}, False
    return None, {}, False


class MockArmSession:
    def __init__(self, arm, *, task_authority, trial_namespace, ledger, config):
        self.arm, self.authority, self.namespace, self.ledger = arm, task_authority, trial_namespace, ledger
        self.config = dict(config or {})
        self.connection_epoch = 0
        self.response_epoch = 0
        self.sequence = 0
        self.closed = False
        self.queue = asyncio.Queue(maxsize=64)
        self.text = ''
        self.audio_samples = 0
        self.attempt = 0
        self.last_outcome = {}
        self.logical_proposals = {}
        self.faults = self.config.get('mock_fault_controller')
        self.delayed_tasks = set()
        self.delayed_failure = None
        self.counters = {'queued_events': 0, 'dequeued_events': 0, 'queued_pcm_frames': 0,
                         'discarded_events': 0, 'discarded_pcm_frames': 0,
                         'authority_outcomes': 0, 'duplicate_outcomes': 0, 'new_effects': 0}

    async def open(self, config=None):
        if config:
            self.config.update(config)
        if self.config.get('mode', 'mock') != 'mock':
            raise ValueError('live_gate_closed: capability, rights, budget and independent acceptance pending')
        self.connection_epoch = self.config.get('connection_epoch', 0)
        self.response_epoch = self.config.get('response_epoch', 0)

    def set_mock_text(self, text: str):
        if not isinstance(text, str) or len(text) > 2000:
            raise ValueError('mock stimulus bound')
        self.text = text

    async def append_audio(self, frame):
        if self.closed:
            raise ValueError('session closed')
        payload = frame if isinstance(frame, dict) else frame.model_dump()
        pcm = base64.b64decode(payload['pcm16'], validate=True)
        if payload.get('sample_rate') != 24000 or not pcm or len(pcm) % 2 or len(pcm) > 9600:
            raise ValueError('unsupported_audio')
        self.audio_samples += len(pcm) // 2
        # Serialization is exercised even in mock mode; nothing sends this frame.
        if self.arm in {'V', 'C'}:
            for offset in range(0, len(pcm), 9600):
                serialize_command('append', audio=base64.b64encode(pcm[offset:offset + 9600]).decode('ascii'))
        if self.audio_samples > 24000 * 120:
            raise ValueError('audio duration bound')

    def _envelope(self, event):
        self.sequence += 1
        return {**event, 'sequence': self.sequence, 'connection_epoch': self.connection_epoch,
                'response_epoch': self.response_epoch}

    async def _emit(self, event):
        if not self.closed:
            self._put(self._envelope(event))

    def _put(self, event):
        self.queue.put_nowait(event)
        self.counters['queued_events'] += 1
        if event.get('type') == 'audio':
            self.counters['queued_pcm_frames'] += 1

    def _discard_queue(self):
        while not self.queue.empty():
            event = self.queue.get_nowait()
            if event is not None:
                self.counters['discarded_events'] += 1
                if event.get('type') == 'audio':
                    self.counters['discarded_pcm_frames'] += 1

    def diagnostics(self):
        return {**self.counters, 'pending_events': self.queue.qsize(),
                'pending_delayed_tasks': len(self.delayed_tasks), 'closed': self.closed,
                'observation': 'observed', 'scope': 'owned_mock_adapter_only'}

    def _take(self, kind):
        return self.faults.take(kind) if self.faults else None

    def _proposal(self, command, arguments, response_id, suffix):
        identity = hashlib.sha256(f'{response_id}:{suffix}'.encode()).hexdigest()[:24]
        tool_id, transport_id = 'call-' + identity, 'pt-' + identity
        proposal = {'name': command, 'arguments': dict(arguments), 'operation_id': tool_id}
        if self.arm == 'V':
            proposal = parse_realtime(json.dumps({'type': 'response.function_call_arguments.done',
                'call_id': tool_id, 'name': command, 'arguments': json.dumps(arguments)}))
        # C/K also traverse the existing mock tool-call dialect rather than bypassing it.
        return parse_wire(serialize_wire({'wire_version': 1,
            **proposal, 'type': 'tool.call', 'transport_turn_id': transport_id}))

    async def _tool_observation(self, turn, proposal, outcome):
        self.counters['authority_outcomes'] += 1
        self.counters['duplicate_outcomes'] += int(bool(outcome.get('duplicate')))
        self.counters['new_effects'] += int(bool(outcome.get('effect_committed') and not outcome.get('duplicate')))
        await self._emit({'type': 'event', 'kind': 'mock_tool_attempt', 'turn_id': turn,
            'provider_tool_id': proposal['operation_id'],
            'provider_transport_id': proposal['transport_turn_id'],
            'logical_operation_id': outcome['logical_operation_id'],
            'duplicate': outcome.get('duplicate', False),
            'effect_committed': outcome.get('effect_committed', False)})

    async def _drop(self, turn, control, phase, committed):
        await self._emit({'type': 'event', 'kind': 'mock_transport_drop', 'turn_id': turn,
            'control_id': control['control_id'], 'phase': phase, 'effect_committed': committed})
        # The relay consumes the queued drop and cancels this owned task. No response replay.
        await asyncio.Future()

    async def inject_provider_error(self, control):
        parsed = parse_realtime(json.dumps({'type': 'error', 'error': {'code': 'provider_error'}}))
        await self._emit({'type': 'event', 'kind': 'mock_provider_error', 'error_code': parsed['code'],
            'control_id': control['control_id']})
        self.faults.applied(control['control_id'], parser='parse_realtime', injected_count=1)

    async def _audio(self, turn, response, tone):
        chunks = [tone[offset:offset + 960] for offset in range(0, len(tone), 960)]
        missing = self._take('missing_audio')
        if missing:
            # Exercise the same PCM parser, then suppress its output at the named seam.
            for raw in chunks:
                mock_audio(self.arm, raw, response)
            self.faults.applied(missing['control_id'], injected_count=0, dropped_pcm_frames=len(chunks))
            return
        overrun = self._take('queue_overrun')
        if overrun:
            chunks = [((tone * 3)[:9600])] * 12
        events = [self._envelope({'type': 'audio', 'turn_id': turn, 'response_id': response,
            'sample_rate': 24000, 'pcm16': mock_audio(self.arm, raw, response)['pcm16'],
            'evidence_label': 'diagnostic tone; not speech'}) for raw in chunks]
        reorder, duplicate, late = self._take('reordered_pcm'), self._take('duplicate_pcm'), self._take('late_pcm')
        if reorder:
            events[0], events[1] = events[1], events[0]
        if duplicate:
            events.insert(1, dict(events[0]))
        delayed = events.pop(-1) if late else None
        count = 0
        try:
            for event in events:
                self._put(event)
                count += 1
        finally:
            combined = [control['control_id'] for control in (overrun, reorder, duplicate) if control]
            for control in (overrun, reorder, duplicate):
                if control:
                    self.faults.applied(control['control_id'], injected_count=count,
                        injected_pcm_samples=sum(len(base64.b64decode(item['pcm16'])) // 2 for item in events[:count]),
                        count_scope='shared_transformed_pcm_batch_not_additive', combined_controls=combined,
                        expected_queue_outcome='unknown_until_relay_observation')
        if late:
            async def deliver_late():
                await asyncio.sleep(late['parameters']['delay_ms'] / 1000)
                if not self.closed:
                    # Preserve the original turn/epoch/sequence. Cancellation cannot relabel it.
                    self._put(delayed)
                    self.faults.applied(late['control_id'], injected_count=1,
                        original_response_epoch=delayed['response_epoch'], original_sequence=delayed['sequence'])
            task = asyncio.create_task(deliver_late())
            self.delayed_tasks.add(task)
            def completed(done):
                self.delayed_tasks.discard(done)
                if not done.cancelled() and done.exception() is not None:
                    self.delayed_failure = 'queue_overrun'
            task.add_done_callback(completed)

    async def commit_turn(self, turn_id: str, *, text: str | None = None, logical_turn_id: str | None = None):
        if self.closed:
            raise ValueError('session closed')
        if text is not None:
            self.set_mock_text(text)
        logical_turn_id = logical_turn_id or turn_id
        self.attempt += 1
        attempt_id = 'at-' + hashlib.sha256(f'{self.namespace}:{self.arm}:{turn_id}:{self.attempt}'.encode()).hexdigest()[:32]
        components = {'V': ['native'], 'C': ['stt', 'tts'], 'K': ['canned']}[self.arm]
        reserved = []
        response_id = 'r-' + hashlib.sha256(f'{turn_id}:{self.attempt}'.encode()).hexdigest()[:32]
        try:
            for component in components:
                component_id = attempt_id + '-' + component
                self.ledger.reserve(component_id, amount=Decimal('0'), logical_turn_id=logical_turn_id,
                                    arm=self.arm, component=component, price_version='mock-zero-v1',
                                    basis='No provider egress; diagnostic synthetic mock only', trial_id=self.namespace)
                reserved.append(component_id)
            for component_id in reserved:
                self.ledger.authorize_dispatch(component_id)
            if self.arm in {'V', 'C'}:
                serialize_command('commit')
            transcript = mock_transcript(self.arm, self.text, turn_id)
            missing = self._take('missing_transcript')
            if missing:
                self.faults.applied(missing['control_id'], injected_count=0, dropped_transcripts=1)
            else:
                await self._emit({'type': 'transcript', 'turn_id': turn_id, 'text': transcript['text'],
                                  'evidence_label': 'mock text stimulus; no speech recognition'})
            controller_start = time.monotonic_ns()
            command, arguments, explicit = match_command(transcript['text'])
            if command:
                if command == 'confirm_slot':
                    arguments = {key: self.last_outcome[key] for key in ('challenge', 'state_version', 'slot_id')
                                 if key in self.last_outcome}
                    saved = self.logical_proposals.get(logical_turn_id)
                    if explicit and saved and saved[0] == command:
                        arguments = dict(saved[1])
                if len(self.logical_proposals) >= 8 and logical_turn_id not in self.logical_proposals:
                    raise ValueError('logical turn bound exceeded')
                self.logical_proposals[logical_turn_id] = (command, dict(arguments))
                proposal = self._proposal(command, arguments, response_id, 'original')
                before = []
                def before_effect():
                    control = self._take('drop_before_commit')
                    if control:
                        before.append(control)
                        self.faults.applied(control['control_id'], injected_count=1, effect_committed=False)
                        return False
                    return True
                outcome = self.authority.execute(self.namespace, proposal['name'], proposal['arguments'],
                    logical_turn_id=logical_turn_id, response_epoch=self.response_epoch,
                    explicit_confirmation=explicit, before_effect=before_effect)
                await self._tool_observation(turn_id, proposal, outcome)
                if before:
                    await self._drop(turn_id, before[0], 'before_effect', False)
                after = self._take('drop_after_commit') if outcome.get('effect_committed') and not outcome.get('duplicate') else None
                if after:
                    self.faults.applied(after['control_id'], injected_count=1, effect_committed=True,
                        logical_operation_id=outcome['logical_operation_id'])
                    await self._drop(turn_id, after, 'after_effect', True)
                fresh = self._take('fresh_tool_duplicate')
                if fresh:
                    second = self._proposal(command, arguments, response_id, 'duplicate:' + fresh['control_id'])
                    repeated = self.authority.execute(self.namespace, second['name'], second['arguments'],
                        logical_turn_id=logical_turn_id, response_epoch=self.response_epoch, explicit_confirmation=explicit)
                    await self._tool_observation(turn_id, second, repeated)
                    await self._emit({'type': 'task', 'turn_id': turn_id, 'outcome': repeated})
                    self.faults.applied(fresh['control_id'], injected_count=2,
                        original_provider_tool_id=proposal['operation_id'], provider_tool_id=second['operation_id'],
                        original_provider_transport_id=proposal['transport_turn_id'], provider_transport_id=second['transport_turn_id'],
                        logical_operation_id=outcome['logical_operation_id'], duplicate=repeated.get('duplicate'),
                        new_effect_count=int(bool(outcome.get('effect_committed') and not outcome.get('duplicate'))))
                self.last_outcome = outcome
            else:
                outcome = {'status': 'clarification', 'message': 'Please ask hours, list slots, select slot 1 or 2, confirm, or status.',
                           'lexical_miss': True}
            await self._emit({'type': 'event', 'kind': 'controller_span', 'turn_id': turn_id,
                'start_ns': controller_start, 'end_ns': time.monotonic_ns(), 'clock_domain': 'server_monotonic_ns',
                'translation': 'identity_noop', 'mock_native_reasoning': self.arm == 'V'})
            await self._emit({'type': 'task', 'turn_id': turn_id, 'outcome': outcome})
            # 80 ms tone: diagnostic only; never adjudicated as correct spoken output.
            tone = b''.join(struct.pack('<h', int(1400 * math.sin(2 * math.pi * 440 * i / 24000))) for i in range(1920))
            await self._audio(turn_id, response_id, tone)
            if self.arm == 'V':
                parse_realtime(json.dumps({'type': 'response.done', 'response': {
                    'id': response_id, 'status': 'completed', 'usage': {'provider_requests': 0}}}))
            else:
                parse_wire(serialize_wire({'wire_version': 1, 'type': 'response.done'}))
            for component_id in reserved:
                self.ledger.settle(component_id, amount=Decimal('0'), usage={'provider_requests': 0}, source='keyless_mock')
            await self._emit({'type': 'event', 'kind': 'response_done', 'turn_id': turn_id,
                              'response_id': response_id, 'spoken_correctness': 'unknown'})
        except BaseException:
            for component_id in reserved:
                self.ledger.mark_unknown(component_id, reason='dispatch_outcome_unknown')
            raise
        finally:
            self.text = ''

    async def submit_tool_result(self, operation_id, result):
        await self._emit({'type': 'task', 'operation_id': operation_id, 'outcome': result})

    async def cancel(self, response_id, response_epoch, rendered_cutoff):
        if response_epoch <= self.response_epoch:
            raise ValueError('stale cancel epoch')
        self.response_epoch = response_epoch
        if self.arm == 'V' and response_id:
            serialize_command('cancel', response_id=response_id)
            if rendered_cutoff is not None:
                serialize_command('truncate', item_id=f'item-{response_id}', rendered_cutoff=rendered_cutoff)
        self._discard_queue()
        ack = parse_wire(serialize_wire({'wire_version': 1, 'type': 'cancel.ack'}))
        await self._emit({'type': 'event', 'kind': ack['type'], 'response_id': response_id,
                          'rendered_cutoff': rendered_cutoff, 'history_method': 'mock_no_provider_history'})

    async def resume(self, connection_epoch, rendered_ack):
        if connection_epoch <= self.connection_epoch:
            raise ValueError('stale connection epoch')
        self.connection_epoch = connection_epoch
        self._discard_queue()
        self.last_outcome = self.authority.snapshot(self.namespace)
        await self._emit({'type': 'event', 'kind': 'resume.ack', 'rendered_ack': rendered_ack,
                          'audio_replayed': False, 'reason': 'mock resume has no replay custody'})

    async def events(self):
        while True:
            if self.delayed_failure is not None:
                failure, self.delayed_failure = self.delayed_failure, None
                yield self._envelope({'type': 'event', 'kind': 'mock_provider_error', 'error_code': failure})
            event = await self.queue.get()
            if event is None:
                return
            self.counters['dequeued_events'] += 1
            yield event

    async def close(self):
        self.closed = True
        for task in list(self.delayed_tasks):
            task.cancel()
        if self.delayed_tasks:
            await asyncio.gather(*list(self.delayed_tasks), return_exceptions=True)
        self._discard_queue()
        self.queue.put_nowait(None)


def create_arm(arm: str, *, task_authority, trial_namespace: str, ledger, config):
    if arm not in {'V', 'C', 'K'}:
        raise ValueError('unsupported arm')
    if (config or {}).get('mode', 'mock') == 'live':
        from .live import LiveArmSession
        return LiveArmSession(arm, task_authority=task_authority, trial_namespace=trial_namespace,
                              ledger=ledger, config=config)
    if (config or {}).get('mode', 'mock') != 'mock':
        raise ValueError('unsupported mode')
    return MockArmSession(arm, task_authority=task_authority, trial_namespace=trial_namespace,
                          ledger=ledger, config=config)
