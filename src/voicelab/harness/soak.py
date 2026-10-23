"""Bounded own-browser/relay soak source; observed effects are not action counters."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import time

from .browser import BrowserDriver, server_evidence, collect_relay_trace, collect_ledger_snapshot
from .calibration import calibrate_page
from .journey import complete_turn, wait_state
from .observations import DiagnosticCursor, app_snapshot, wait_cleanup, cleanup_count, resource_sample


PROFILES = {
    'full': {'max_seconds': 1800, 'max_turns': 200, 'concurrency': 3,
             'sessions_required': 40, 'required_interruptions': 40, 'required_reconnects': 10},
    'short': {'max_seconds': 120, 'max_turns': 50, 'concurrency': 2,
              'sessions_required': 10, 'required_interruptions': 4, 'required_reconnects': 2},
}


@dataclass
class Lane:
    page: object
    context: object
    cdp: object
    task: dict
    cursor: DiagnosticCursor
    sample_rate: int | None = None


async def probe_state(page):
    return await page.evaluate('()=>window.__voiceLabDriver.snapshot()')


async def start_session(lane: Lane, identity: str, sink):
    # Mutate the observer's stable task object only after verified previous closure.
    lane.task.clear()
    lane.task.update(task_id=identity, trial_id=identity, arm='K')
    sink.emit('session_start_attempted', task_id=identity, arm='K')
    await lane.page.locator('#start').click()
    await wait_state(lane.page, ['listening'], timeout=9000)
    if not lane.task.get('_session_id'):
        raise ValueError('ready session identity was not observed')
    lane.sample_rate = (await lane.page.evaluate('()=>window.__voiceLabDriver.graphStamp()'))['sample_rate']
    lane.cursor.sample_rate = lane.sample_rate
    sink.emit('task_admission', task_id=identity, arm='K', admission='admitted',
              session_id=lane.task['_session_id'], server_trial_namespace=lane.task['_trial_namespace'])


async def end_session(lane: Lane, spec: dict, nonce: str, sink, references: dict) -> tuple[list[dict], dict]:
    await lane.page.locator('#end').click()
    cleanup = await wait_cleanup(lane.page)
    await lane.cursor.drain(lane.page, sink, lane.task['task_id'])
    sink.emit('session_cleanup_observed', task_id=lane.task['task_id'], snapshot=cleanup,
              observation='observed' if cleanup_count(cleanup) == 0 else 'unknown')
    evidence = await server_evidence(spec, nonce, lane.task)
    raw = collect_relay_trace(spec, evidence)
    name = lane.task['task_id']
    references[name + '-trace'] = sink.bytes_artifact(name + '.jsonl', raw, 'trace')
    references[name + '-server'] = sink.artifact(name + '.json', evidence, 'server_evidence')
    ledger_ref = sink.authoritative_artifact(collect_ledger_snapshot(spec, evidence), 'ledger_snapshot')
    references[name + '-ledger'] = ledger_ref
    sink.emit('ledger_snapshot_reference', task_id=name, session_id=lane.task['_session_id'],
              server_run_id=lane.task.get('_server_run_id'), scope='run_snapshot', references={'ledger': ledger_ref})
    return [json.loads(line) for line in raw.decode().splitlines() if line.strip()], cleanup


async def fourth_refusal(driver: BrowserDriver, lanes: list[Lane], sink) -> bool:
    from .browser import own_diagnostic
    task = {'task_id': 'soak-fourth-refusal', 'arm': 'K'}
    page, context = await driver.page(task)
    try:
        sink.emit('admission_probe_attempted', task_id=task['task_id'], contexts=4)
        await page.locator('#start').click()
        await wait_state(page, ['failed', 'disconnected', 'permission_denied'], timeout=9000)
        observed = await probe_state(page)
        server = await asyncio.to_thread(own_diagnostic, driver.spec, driver.nonce)
        closed = observed.get('lastClose') or {}
        refused = (task.get('_session_id') is None and closed.get('code') == 1008
                   and closed.get('reason') == 'admission refused'
                   and server.get('admitted_sessions') == 3
                   and all(lane.task.get('_session_id') for lane in lanes))
        sink.emit('admission_probe_observed', task_id=task['task_id'],
                  refusal_observation='observed' if refused else 'unknown', snapshot=observed,
                  server_observations=server, fourth_session_refused=refused)
        await page.locator('#end').click()
        await wait_cleanup(page)
        return refused
    finally:
        await context.close()
        driver.contexts.remove(context)


async def run_soak(spec: dict, nonce: str, sink, profile: str) -> dict:
    if not spec.get('_fixture') or len(spec['_fixture']['turns']) != 5:
        raise ValueError('soak needs the explicitly labeled five-turn development fixture')
    policy = PROFILES[profile]
    references, incomplete, samples, calibrations = {}, [], [], []
    summary = {'profile': profile, **policy, 'contexts': policy['concurrency'],
        'attempted_turns': 0, 'aggregate_turns': 0, 'observed_turns': 0,
        'interruption_attempts': 0, 'interruptions': 0,
        'reconnect_attempts': 0, 'reconnects': 0, 'sessions_started': 0, 'sessions_ended': 0,
        'fourth_session_refused': False, 'coverage_actions_are_attempts': False,
        'sample_interval_seconds': 2, 'turn_spacing_seconds': 1,
        'full_gate_pass': False, 'product_acceptance': False}
    references['profile'] = sink.artifact('soak-profile.json', {'schema_version': 1,
        'profile': profile, **policy, 'sample_interval_seconds': 2, 'turn_spacing_seconds': 1,
        'required_repeats': 30, 'audio_sha256': spec['_fixture']['audio']['sha256'],
        'run_id': spec['run_id'], 'manifest_hash': spec['_manifest_hash'],
        'evidence_label': 'short profile cannot replace full soak' if profile == 'short' else 'full bounded development profile',
        'product_acceptance': False}, 'soak_profile')
    session_ids = [f'soak-{profile}-s{n:03d}' for n in range(policy['sessions_required'])]
    for identity in session_ids:
        sink.emit('task_scheduled', task_id=identity, arm='K', cohort='diagnostic',
                  logical_turn_ids=[f'{identity}-t{i}' for i in range(1, 6)])
    lanes: list[Lane] = []
    stop = asyncio.Event()
    starts, terminals, ended_sessions = set(), set(), set()
    async with BrowserDriver(spec, nonce, sink) as driver:
        for index in range(policy['concurrency']):
            task = {'task_id': session_ids[index], 'arm': 'K'}
            page, context = await driver.page(task)
            cdp = await context.new_cdp_session(page)
            await cdp.send('Performance.enable')
            lanes.append(Lane(page, context, cdp, task, DiagnosticCursor()))
        await asyncio.gather(*(start_session(lane, session_ids[index], sink) for index, lane in enumerate(lanes)))
        summary['sessions_started'] += len(lanes)
        if profile == 'full':
            summary['fourth_session_refused'] = await fourth_refusal(driver, lanes, sink)
            if not summary['fourth_session_refused']:
                incomplete.append('fourth-admission-not-proven-refused')
        else:
            # A two-context short run never silently substitutes for the fourth/full gate.
            summary['fourth_session_refused'] = False
        pre = await asyncio.gather(*(calibrate_page(lane.page, sink, f'{profile}-c{index}-pre', 'pre')
                                    for index, lane in enumerate(lanes)))
        for index, result in enumerate(pre):
            calibrations.append(result)
            references[f'c{index}-pre'] = sink.artifact(f'calibration-c{index}-pre.json', result, 'calibration_observations')
            if not result['full_gate_pass']:
                incomplete.append(f'c{index}-pre-calibration')
        started = time.monotonic()
        deadline = started + policy['max_seconds']

        async def sample_loop():
            tick = started
            while not stop.is_set():
                row = await resource_sample([lane.page for lane in lanes], [lane.cdp for lane in lanes],
                    spec, nonce, after_warmup=summary['observed_turns'] >= 20,
                    sample_rates=[lane.sample_rate for lane in lanes])
                samples.append(row)
                sink.emit('resource_sample', resources=row)
                for lane in lanes:
                    await lane.cursor.drain(lane.page, sink, lane.task['task_id'])
                tick += 2
                try:
                    await asyncio.wait_for(stop.wait(), timeout=max(.01, tick - time.monotonic()))
                except asyncio.TimeoutError:
                    pass

        async def worker(index: int, lane: Lane):
            indexes = list(range(index, policy['sessions_required'], policy['concurrency']))
            for offset, session_index in enumerate(indexes):
                identity = session_ids[session_index]
                interrupt_epoch = reconnect_connection = None
                if offset:
                    await start_session(lane, identity, sink)
                    summary['sessions_started'] += 1
                try:
                    for position, words in enumerate(spec['_fixture']['turns'], 1):
                        if time.monotonic() >= deadline:
                            raise asyncio.TimeoutError('soak time cap')
                        tick = time.monotonic()
                        logical_id = f'{identity}-t{position}'
                        lane.task['_logical_turn_id'] = logical_id
                        sink.emit('turn_start_attempted', task_id=identity, logical_turn_id=logical_id, arm='K')
                        summary['attempted_turns'] += 1
                        cancel = position == 4 and session_index < policy['required_interruptions']
                        before = await probe_state(lane.page)
                        if cancel:
                            summary['interruption_attempts'] += 1
                            sink.emit('action_attempted', task_id=identity, logical_turn_id=logical_id,
                                      action_id=identity + '-cancel', fault='cancel_after_commit')
                        await complete_turn(lane.page, words, cancel_after_commit=cancel)
                        after = await probe_state(lane.page)
                        if cancel and after['fenceCounter'] > before['fenceCounter'] and after['cancelAcks'] > before['cancelAcks']:
                            interrupt_epoch = after['lastFence']['epoch']
                        if position == 3 and (session_index + 1) % 4 == 0:
                            summary['reconnect_attempts'] += 1
                            before_resume = await probe_state(lane.page)
                            sink.emit('action_attempted', task_id=identity, logical_turn_id=logical_id,
                                      action_id=identity + '-reconnect', fault='transport_loss')
                            await lane.page.locator('#disconnect').click()
                            await wait_state(lane.page, ['disconnected'])
                            await lane.page.locator('#retry').click()
                            await wait_state(lane.page, ['listening'], timeout=4500)
                            after_resume = await probe_state(lane.page)
                            if after_resume['resumeCount'] > before_resume['resumeCount']:
                                reconnect_connection = (after_resume.get('lastFence') or {}).get('connection')
                        await lane.cursor.drain(lane.page, sink, identity)
                        await asyncio.sleep(max(0, tick + 1 - time.monotonic()))
                    if offset == len(indexes) - 1:
                        # Final graph is observed separately; do not claim it is the same graph as pre.
                        if (await app_snapshot(lane.page))['state'] != 'listening':
                            await lane.page.locator('#retry').click()
                            await wait_state(lane.page, ['listening'])
                        result = await calibrate_page(lane.page, sink, f'{profile}-c{index}-post', 'post')
                        calibrations.append(result)
                        references[f'c{index}-post'] = sink.artifact(f'calibration-c{index}-post.json', result, 'calibration_observations')
                        if not result['full_gate_pass']:
                            incomplete.append(f'c{index}-post-calibration')
                finally:
                    try:
                        trace, cleanup = await end_session(lane, spec, nonce, sink, references)
                        summary['sessions_ended'] += 1
                        ended_sessions.add(identity)
                        for event in trace:
                            key = (event['session_id'], event.get('logical_turn_id'))
                            if event['kind'] == 'turn_started' and key[1] is not None:
                                starts.add(key)
                            if event['kind'] == 'turn_ended' and event.get('terminal_outcome') is not None:
                                terminals.add(key)
                                if event['terminal_outcome'] not in {'success', 'expected_interruption'}:
                                    incomplete.append(identity + '-unexpected-turn-outcome')
                        summary['observed_turns'] = summary['aggregate_turns'] = len(starts)
                        if interrupt_epoch is not None and any(e['kind'] == 'cancel_dispatch' and e['response_epoch'] == interrupt_epoch for e in trace):
                            summary['interruptions'] += 1
                        if reconnect_connection is not None and any(e['kind'] == 'reconnect' and e['connection_epoch'] == reconnect_connection for e in trace):
                            summary['reconnects'] += 1
                        if cleanup_count(cleanup) != 0 or cleanup['observation_statuses'].get('cleanup') != 'observed_released':
                            incomplete.append(identity + '-cleanup')
                    except Exception as exc:
                        incomplete.append(identity + '-terminal-' + type(exc).__name__)

        sampling = asyncio.create_task(sample_loop())
        workers = [asyncio.create_task(worker(index, lane)) for index, lane in enumerate(lanes)]
        try:
            await asyncio.wait_for(asyncio.gather(*workers), timeout=policy['max_seconds'])
            summary['stop_reason'] = 'scheduled_turn_cap'
        except Exception as exc:
            summary['stop_reason'] = 'time_cap_or_worker_failure'
            incomplete.append(type(exc).__name__)
        finally:
            for task in workers:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            stop.set()
            try:
                await sampling
            except Exception as exc:
                incomplete.append('sampler-' + type(exc).__name__)
            for lane in lanes:
                await lane.page.locator('#end').click()
                await wait_cleanup(lane.page)
                await lane.cursor.drain(lane.page, sink, lane.task['task_id'])
            samples.append(await resource_sample([lane.page for lane in lanes], [lane.cdp for lane in lanes],
                spec, nonce, after_warmup=True, sample_rates=[lane.sample_rate for lane in lanes]))
        summary['elapsed_seconds'] = time.monotonic() - started
        unended = set(session_ids) - ended_sessions
        for identity in sorted(unended):
            sink.emit('scheduled_session_incomplete', observation='unknown', task_id=identity)
        if unended or starts - terminals or len(starts) != policy['max_turns']:
            incomplete.append('scheduled-start-terminal-denominators-incomplete')
        gaps = [b['monotonic_seconds'] - a['monotonic_seconds'] for a, b in zip(samples, samples[1:])]
        summary['max_sample_interval_seconds'] = max(gaps) if gaps else None
        summary['cadence_complete'] = bool(gaps) and max(gaps) <= 4
        if not summary['cadence_complete'] or any(lane.cursor.lost for lane in lanes):
            incomplete.append('resource-or-diagnostic-cadence-unknown')
        summary['event_queue_maxima'] = [lane.cursor.max_queue_seconds for lane in lanes]
        summary['queue_observation_counts'] = [lane.cursor.queue_events for lane in lanes]
        if any(lane.cursor.queue_events == 0 or lane.cursor.queue_unknown or lane.cursor.failure_observed
               or lane.cursor.max_queue_seconds > 2 for lane in lanes):
            incomplete.append('event-level-queue-bound-unproven-or-exceeded')
        boots = {row['server_observations'].get('boot_id') for row in samples}
        if None in boots or len(boots) != 1:
            incomplete.append('own-server-boot-observation-incomplete-or-changed')
        required_metrics = ('rss_bytes', 'browser_heap_bytes', 'owned_handles', 'queue_seconds',
                            'worklet_queue_seconds', 'sockets', 'files', 'async_tasks', 'event_loop_lag_ms')
        if not samples or any(any(row.get(key) is None for key in required_metrics) for row in samples):
            incomplete.append('required-resource-metric-unknown')
        if samples and (samples[-1].get('owned_handles') != 0
                or samples[-1]['server_observations'].get('admitted_sessions') != 0):
            incomplete.append('post-End-owned-resource-release-not-proven')
        if any(row.get('worklet_queue_seconds') is not None and row['worklet_queue_seconds'] > 2 for row in samples):
            incomplete.append('observed-worklet-queue-exceeds-two-seconds')
        coverage = (len(starts) == policy['max_turns'] and not starts - terminals
            and summary['interruptions'] >= policy['required_interruptions']
            and summary['reconnects'] >= policy['required_reconnects']
            and len(ended_sessions) == policy['sessions_required'])
        summary['coverage_complete'] = coverage
        if not coverage:
            incomplete.append('observed-effects-below-profile-coverage')
        if not all(c['full_gate_pass'] for c in calibrations) or len(calibrations) != policy['concurrency'] * 2:
            incomplete.append('pre-post-calibration-incomplete')
        if profile == 'full':
            from voicelab.reporting.diagnostics import soak_report
            report = soak_report(samples, summary)
            summary['resource_gate_pass'] = report['resource_gate_pass']
            summary['full_gate_pass'] = report['passed'] and not incomplete
            references['resource-analysis'] = sink.artifact('soak-resource-analysis.json', report, 'soak_analysis')
            if not summary['full_gate_pass']:
                incomplete.append('full-resource-or-coverage-gate-incomplete')
        else:
            summary['full_gate_pass'] = False
            summary['resource_gate_pass'] = None  # full three-context analyzer cannot certify short growth
            if any(row.get('browser_heap_bytes') is None or row.get('owned_handles') is None for row in samples):
                incomplete.append('short-resource-observations-unknown')
        reference = sink.artifact('soak-observations.json', {'profile': profile, 'samples': samples,
            'summary': summary, 'calibrations': calibrations}, 'soak_observations')
        references['soak'] = reference
        return {'references': references, 'summary': summary, 'incomplete': sorted(set(incomplete)),
                'evidence_label': 'short diagnostic profile; never full-soak acceptance' if profile == 'short' else 'full bounded diagnostic source; independent acceptance remains separate'}
