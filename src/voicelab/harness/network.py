"""Frozen application-message delay/drop source, never a packet-loss measurement."""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time


def validate_network(value: dict, profile: str, seed: int) -> dict:
    if (not isinstance(value, dict) or value.get('profile') != profile
            or type(value.get('seed')) is not int or value['seed'] != seed):
        raise ValueError('network profile/seed differs from frozen cohort')
    if set(value) != {'profile', 'seed', 'kind', 'upload_delay_ms', 'download_delay_ms', 'audio_drop_fraction'}:
        raise ValueError('explicit closed network configuration required')
    if value['kind'] not in {'loopback', 'application_message_delay_drop'}:
        raise ValueError('unsupported network profile')
    for key in ('upload_delay_ms', 'download_delay_ms'):
        if type(value[key]) is not int or not 0 <= value[key] <= 1000:
            raise ValueError('bounded explicit message delay required')
    drop = value['audio_drop_fraction']
    if type(drop) not in {int, float} or not 0 <= drop <= .02:
        raise ValueError('bounded explicit eligible audio-message drop fraction required')
    if value['kind'] == 'loopback' and any(value[k] for k in ('upload_delay_ms', 'download_delay_ms', 'audio_drop_fraction')):
        raise ValueError('loopback profile cannot conceal injected delay/drop')
    return dict(value)


class MessageNetwork:
    def __init__(self, config: dict, pair_identity: str, sink, task: dict):
        self.config, self.sink, self.task = config, sink, task
        seed = int(hashlib.sha256(f'{config["seed"]}:{pair_identity}'.encode()).hexdigest(), 16)
        self.random = {'upload': random.Random(seed), 'download': random.Random(seed ^ 0x5A5A)}
        self.locks = {'upload': asyncio.Lock(), 'download': asyncio.Lock()}
        self.pending = {'upload': 0, 'download': 0}
        self.sequence = {'upload': 0, 'download': 0}

    async def forward(self, direction: str, raw, target, close):
        target_time = time.monotonic() + self.config[direction + '_delay_ms'] / 1000
        self.pending[direction] += 1
        try:
            if self.pending[direction] > 64:
                self.sink.emit('network_queue_overrun', task_id=self.task['task_id'],
                               direction=direction, outcome='queue_overrun')
                await close(code=1009, reason='bounded driver network queue exceeded')
                return
            if len(raw) > 300000:
                await close(code=1009, reason='bounded driver frame exceeded')
                return
            try:
                frame = json.loads(raw)
                eligible = isinstance(frame, dict) and frame.get('type') == 'audio'
            except (ValueError, TypeError):
                eligible = False
            # Control, admission, authority and terminal frames are never silently dropped.
            dropped = eligible and self.random[direction].random() < self.config['audio_drop_fraction']
            number = self.sequence[direction]
            self.sequence[direction] += 1
            async with self.locks[direction]:
                await asyncio.sleep(max(0, target_time - time.monotonic()))
                if not dropped:
                    await target.send(raw)
            if dropped:
                self.sink.emit('network_audio_message_dropped', task_id=self.task['task_id'],
                    direction=direction, sequence=number, network_profile=self.config['profile'],
                    observation='observed', scope='application_message_only')
        finally:
            self.pending[direction] -= 1
