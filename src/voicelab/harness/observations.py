"""Bounded diagnostic cursors and own-resource samples, never an evidence oracle."""
from __future__ import annotations

import asyncio
import time

from .browser import own_diagnostic


async def app_snapshot(page) -> dict:
    value = await page.evaluate('()=>window.__voiceLabDiagnostics?.snapshot()??null')
    if not isinstance(value, dict) or value.get('schema_version') != 1:
        raise ValueError('D6 diagnostic snapshot v1 unavailable')
    return value


class DiagnosticCursor:
    def __init__(self):
        self.cursor = 0
        self.lost = 0
        self.lock = asyncio.Lock()
        self.sample_rate: int | None = None
        self.max_queue_seconds = 0.0
        self.queue_events = 0
        self.queue_unknown = False
        self.failure_observed = False

    async def drain(self, page, sink, identity: str) -> list[dict]:
        async with self.lock:
            return await self._drain(page, sink, identity)

    async def _drain(self, page, sink, identity: str) -> list[dict]:
        events = []
        for _ in range(16):
            batch = await page.evaluate('(after)=>window.__voiceLabDiagnostics.drain(after,1000)', self.cursor)
            if batch.get('schema_version') != 1 or batch.get('cursor', -1) < self.cursor:
                raise ValueError('D6 diagnostic cursor integrity')
            self.lost += batch.get('dropped_before_cursor', 0)
            sink.emit('browser_diagnostic_batch', task_id=identity, snapshot=batch,
                      observation='unknown' if batch.get('dropped_before_cursor') else 'observed')
            events.extend(batch['events'])
            for event in batch['events']:
                detail = event.get('detail', {})
                if event.get('kind') == 'state' and detail.get('state', detail.get('next')) in {'failed', 'recovery_unknown', 'unsupported_audio'}:
                    self.failure_observed = True
                for key in ('queued_frames', 'queuedFrames', 'droppedFrames'):
                    if key in detail:
                        rate = detail.get('context_sample_rate', self.sample_rate)
                        value = detail[key]
                        if not isinstance(rate, (int, float)) or rate <= 0 or not isinstance(value, (int, float)) or value < 0:
                            self.queue_unknown = True
                        else:
                            self.queue_events += 1
                            self.max_queue_seconds = max(self.max_queue_seconds, value / rate)
            before, self.cursor = self.cursor, batch['cursor']
            if self.cursor >= batch['latest_seq']:
                return events
            if self.cursor == before:
                raise ValueError('D6 diagnostic cursor failed to advance')
        raise ValueError('D6 bounded drain exceeded; coverage unknown')


def cleanup_count(snapshot: dict) -> int | None:
    handles = snapshot.get('owned_handles', {})
    if (snapshot.get('observation_statuses', {}).get('owned_handles') != 'observed_api_state'
            or not handles or not all(type(v) is int and v >= 0 for v in handles.values())):
        return None
    return sum(handles.values())


async def wait_cleanup(page, timeout: float = 4) -> dict:
    deadline = time.monotonic() + timeout
    last = {}
    while time.monotonic() < deadline:
        last = await app_snapshot(page)
        if (cleanup_count(last) == 0 and last.get('observation_statuses', {}).get('cleanup') == 'observed_released'):
            return last
        await asyncio.sleep(.05)
    return last  # caller must retain nonzero/unknown; never coerce to released


async def resource_sample(pages, cdps, spec: dict, nonce: str, *, after_warmup: bool,
                          sample_rates: list[int | None] | None = None) -> dict:
    started = time.monotonic()
    heaps, snapshots, unknown = {}, [], []
    for index, (page, cdp) in enumerate(zip(pages, cdps)):
        snapshot = {}
        try:
            snapshot = await app_snapshot(page)
        except Exception as exc:
            unknown.append(type(exc).__name__)
        snapshots.append(snapshot)
        try:
            # Release only closed observer references before heap sampling, not app resources.
            await page.evaluate('()=>window.__voiceLabDriver.snapshot()')
            metrics = await cdp.send('Performance.getMetrics')
            values = {item['name']: item['value'] for item in metrics['metrics']}
            heaps[f'c{index}'] = values.get('JSHeapUsedSize')
        except Exception as exc:
            heaps[f'c{index}'] = None
            unknown.append(type(exc).__name__)
    try:
        server = await asyncio.to_thread(own_diagnostic, spec, nonce)
        if server.get('scope') != 'own_server_process_only' or not server.get('boot_id'):
            raise ValueError('own server metrics scope/boot identity absent')
    except Exception as exc:
        server = {'observation': 'unknown', 'metrics': {}, 'scope': 'own_server_process_only'}
        unknown.append(type(exc).__name__)
    def observed(name):
        row = server.get('metrics', {}).get(name, {})
        return row.get('value') if row.get('observation') == 'observed' else None
    handles = [cleanup_count(snapshot) for snapshot in snapshots]
    queues, worklet_queues = [], []
    for index, snapshot in enumerate(snapshots):
        q = snapshot.get('queue_seconds', {})
        states = snapshot.get('observation_statuses', {})
        if (states.get('playback_queue') not in {'estimated', 'observed_empty'}
                or states.get('upload_queue') not in {'estimated', 'observed_empty'}
                or any(q.get(key) is None for key in ('playback', 'upload'))):
            queues.append(None)
        else:
            queues.append(max(q['playback'], q['upload']))
        rate = sample_rates[index] if sample_rates and index < len(sample_rates) else None
        worklet = snapshot.get('worklet', {})
        if cleanup_count(snapshot) == 0 and states.get('cleanup') == 'observed_released':
            worklet_queues.append(0)
        elif (worklet.get('status') == 'observed' and isinstance(worklet.get('queued_frames'), (int, float))
                and rate is not None and rate > 0):
            worklet_queues.append(worklet['queued_frames'] / rate)
        else:
            worklet_queues.append(None)
    return {'monotonic_seconds': started, 'collection_duration_seconds': time.monotonic() - started,
        'after_warmup': after_warmup, 'browser_heaps_by_context': heaps,
        'browser_heap_bytes': max(heaps.values()) if heaps and all(v is not None for v in heaps.values()) else None,
        'owned_handles': sum(handles) if handles and all(v is not None for v in handles) else None,
        'queue_seconds': max(queues) if queues and all(v is not None for v in queues) else None,
        'worklet_queue_seconds': max(worklet_queues) if worklet_queues and all(v is not None for v in worklet_queues) else None,
        'rss_bytes': observed('rss_bytes'), 'sockets': observed('open_sockets'),
        'files': observed('open_files'), 'async_tasks': observed('asyncio_tasks'),
        'event_loop_lag_ms': observed('event_loop_lag_ms'), 'server_observations': server,
        'snapshots': snapshots, 'unknown': unknown,
        'queue_observation': 'estimated bounded buffer samples; worklet event maxima also required'}
