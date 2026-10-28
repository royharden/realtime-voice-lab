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

    async def _emit(self, event):
        if self.closed:
            return
        self.sequence += 1
        event.update(sequence=self.sequence, connection_epoch=self.connection_epoch,
                     response_epoch=self.response_epoch)
        self.queue.put_nowait(event)

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
        for component in components:
            component_id = attempt_id + '-' + component
            self.ledger.reserve(component_id, amount=Decimal('0'), logical_turn_id=logical_turn_id,
                                arm=self.arm, component=component, price_version='mock-zero-v1',
                                basis='No provider egress; diagnostic synthetic mock only')
            reserved.append(component_id)
        response_id = 'r-' + hashlib.sha256(f'{turn_id}:{self.attempt}'.encode()).hexdigest()[:32]
        try:
            if self.arm in {'V', 'C'}:
                serialize_command('commit')
            transcript = mock_transcript(self.arm, self.text, turn_id)
            await self._emit({'type': 'transcript', 'turn_id': turn_id, 'text': transcript['text'],
                              'evidence_label': 'mock text stimulus; no speech recognition'})
            controller_start = time.monotonic_ns()
            command, arguments, explicit = match_command(transcript['text'])
            if self.arm == 'V' and command:
                proposal = parse_realtime(json.dumps({'type': 'response.function_call_arguments.done',
                    'call_id': f'call-{response_id}', 'name': command, 'arguments': json.dumps(arguments)}))
                command, arguments = proposal['name'], proposal['arguments']
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
                outcome = self.authority.execute(self.namespace, command, arguments,
                    logical_turn_id=logical_turn_id, response_epoch=self.response_epoch, explicit_confirmation=explicit)
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
            audio = mock_audio(self.arm, tone, response_id)
            await self._emit({'type': 'audio', 'turn_id': turn_id, 'response_id': response_id,
                'sample_rate': 24000, 'pcm16': audio['pcm16'], 'evidence_label': 'diagnostic tone; not speech'})
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
                self.ledger.mark_unknown(component_id, reason='mock attempt aborted before terminal settlement')
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
        while not self.queue.empty():
            self.queue.get_nowait()
        ack = parse_wire(serialize_wire({'wire_version': 1, 'type': 'cancel.ack'}))
        await self._emit({'type': 'event', 'kind': ack['type'], 'response_id': response_id,
                          'rendered_cutoff': rendered_cutoff, 'history_method': 'mock_no_provider_history'})

    async def resume(self, connection_epoch, rendered_ack):
        if connection_epoch <= self.connection_epoch:
            raise ValueError('stale connection epoch')
        self.connection_epoch = connection_epoch
        while not self.queue.empty():
            self.queue.get_nowait()
        self.last_outcome = self.authority.snapshot(self.namespace)
        await self._emit({'type': 'event', 'kind': 'resume.ack', 'rendered_ack': rendered_ack,
                          'audio_replayed': False, 'reason': 'mock resume has no replay custody'})

    async def events(self):
        while True:
            event = await self.queue.get()
            if event is None:
                return
            yield event

    async def close(self):
        self.closed = True
        while not self.queue.empty():
            self.queue.get_nowait()
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
