"""Bounded development journeys through real V/C/K browser and relay paths."""
from __future__ import annotations

import asyncio
import json

from .browser import BrowserDriver, snapshot, trace_export, server_evidence, collect_relay_trace, collect_ledger_snapshot
from .activation import bind_task_fixture
from .faults import MockControls, effect_observation


FAULTS = {
    'permission_denied': 'permission_denied; no admitted server session',
    'device_lost': 'device_lost; explicit retry/end only',
    'device_muted': 'device_muted or device_lost; explicit retry/end only',
    'silence_timeout': 'silence_timeout; no fabricated successful turn',
    'start_end_race': 'closed; late permission acquisition released',
    'cancel_before_commit': 'no booking; incremented render fence',
    'cancel_after_commit': 'booking preserved; incremented render fence',
    'fresh_tool_duplicate': 'one booking; original operation and duplicate attempt retained',
    'lost_rendered_ack': 'no old audio replay; repeat_requested or recovery_unknown',
    'queue_overrun': 'queue_overflow or queue_overrun; terminal and bounded cleanup',
    'malformed_protocol': 'protocol_error; explicit terminal',
    'duplicate_sequence': 'protocol_error; duplicate frame refused',
    'service_restart': 'service_restart journal outcome; original booking preserved',
}


def tasks_from(spec: dict, plan: dict | None) -> list[dict]:
    if plan is not None:
        # D5 owns plan validity/balance. Use that validator rather than a second schedule truth.
        from voicelab.evaluation.planning import validate_trial_plan
        validate_trial_plan(plan)
        if plan['run_id'] != spec['run_id']:
            raise ValueError('plan/run identity mismatch')
        tasks = plan['tasks']
    else:
        tasks = [{'task_id': f'dev-{arm}', 'trial_id': f'dev-{arm}', 'arm': arm,
                  'cohort': 'diagnostic', 'family': 'development-fixed-booking',
                  'speaker': None, 'logical_turn_ids': [f'dev-{arm}-t{i}' for i in range(1, 6)],
                  'fault_schedule': []} for arm in ('V', 'C', 'K')]
        if spec.get('include_required_faults') is True:
            for arm in ('V', 'C', 'K'):
                for kind in FAULTS:
                    position = 4 if kind in {'cancel_after_commit', 'fresh_tool_duplicate', 'lost_rendered_ack', 'service_restart'} else 1
                    name = f'dev-{arm}-{kind}'
                    tasks.append({'task_id': name, 'trial_id': name, 'arm': arm,
                        'cohort': 'diagnostic', 'family': 'development-fixed-booking', 'speaker': None,
                        'logical_turn_ids': [f'{name}-t{i}' for i in range(1, 6)],
                        'fault_schedule': [{'kind': kind, 'turn_position': position}]})
    if not 1 <= len(tasks) <= 144 or len({t['task_id'] for t in tasks}) != len(tasks):
        raise ValueError('unique bounded task schedule required')
    for task in tasks:
        if task['arm'] not in {'V', 'C', 'K'} or not 1 <= len(task['logical_turn_ids']) <= 8:
            raise ValueError('arm or turn schedule bound')
        seen_faults = set()
        for fault in task['fault_schedule']:
            if fault['kind'] not in FAULTS or not 1 <= fault['turn_position'] <= len(task['logical_turn_ids']):
                raise ValueError('unsupported or unbounded fault schedule')
            key = (fault['turn_position'], fault['kind'])
            if key in seen_faults:
                raise ValueError('duplicate scheduled action identity')
            seen_faults.add(key)
            if fault['kind'] in {'permission_denied', 'start_end_race'} and fault['turn_position'] != 1:
                raise ValueError('preconnection faults must be scheduled at the first position')
    return tasks


async def wait_state(page, states: list[str], timeout: int = 4000):
    await page.wait_for_function('(states)=>states.includes(document.querySelector("#state")?.textContent)',
                                 arg=states, timeout=timeout)


async def complete_turn(page, text: str, *, cancel_after_commit: bool = False) -> dict:
    # No fixed sleep is treated as completion. Wait for an actual response-done trace.
    state = await page.locator('#state').inner_text()
    if state in {'playing', 'awaiting_response'}:
        await page.locator('#retry').click()
        await wait_state(page, ['listening'])
    before = await page.evaluate('()=>window.__voiceLabDriver.snapshot().responseDone')
    cancel_before = await page.evaluate('()=>window.__voiceLabDriver.snapshot().cancelTriggered')
    if cancel_after_commit:
        await page.evaluate('()=>window.__voiceLabDriver.configure({cancelAfterCommit:true})')
    await page.locator('#text').fill(text)
    await page.locator('#stimulus').evaluate('(form)=>form.requestSubmit()')
    # UI state completion may precede final rendering; capture both trace and snapshot.
    await page.wait_for_function("""(prior) => {
      const probe=window.__voiceLabDriver.snapshot();
      const state=document.querySelector('#state')?.textContent;
      return (prior.cancel?probe.cancelTriggered>prior.cancelBefore:probe.responseDone>prior.before)
        || ['failed','closed','device_lost','device_muted','silence_timeout'].includes(state);
    }""", arg={'before': before, 'cancelBefore': cancel_before, 'cancel': cancel_after_commit}, timeout=10000)
    await page.wait_for_timeout(150)  # render grace only; never used as a verdict
    return await snapshot(page)


def observed_outcome(kind: str, state: dict, trace: dict, baseline: dict | None = None) -> tuple[str, str]:
    """Conservative driver checks; task/audio correctness belongs to D5 + independent oracle."""
    events = trace.get('events', [])
    kinds = {event.get('kind') for event in events}
    error = state.get('error') or ''
    if kind == 'permission_denied' and state['state'] == 'permission_denied':
        return 'observed', 'permission_denied'
    if kind in {'device_lost', 'device_muted'} and state['state'] in {'device_lost', 'device_muted'}:
        return 'observed', state['state']
    if kind == 'silence_timeout' and state['state'] == 'silence_timeout':
        return 'observed', 'silence_timeout'
    if kind == 'start_end_race' and state['state'] == 'closed':
        handles = state.get('driver', {})
        if handles.get('liveTracks') == 0 and handles.get('openSockets') == 0:
            return 'observed', 'closed_capture_and_socket_released'
    if kind in {'cancel_before_commit', 'cancel_after_commit'}:
        try:
            task = json.loads(state.get('task') or '{}')
        except ValueError:
            task = {}
        booking = task.get('booking') or task.get('task', {}).get('booking')
        task_observed = 'booking' in task or 'booking' in task.get('task', {})
        prior = (baseline or {}).get('driver', {})
        current = state.get('driver', {})
        prior_fence = prior.get('lastFence') or {}
        current_fence = current.get('lastFence') or {}
        fresh = (isinstance(prior_fence.get('epoch'), int)
                 and current.get('fenceCounter', 0) > prior.get('fenceCounter', 0)
                 and current_fence.get('epoch', -1) > prior_fence['epoch'])
        if task_observed and fresh and (bool(booking) == (kind == 'cancel_after_commit')):
            return 'observed', 'fence_and_booking_state_observed'
    if kind in {'malformed_protocol', 'duplicate_sequence'} and 'protocol_error' in error:
        return 'observed', 'protocol_error'
    if kind == 'queue_overrun' and any(code in error for code in ('queue_overflow', 'queue_overrun')):
        return 'observed', 'queue_overrun'
    if kind == 'lost_rendered_ack' and 'relay_event' in kinds:
        if any(e.get('detail', {}).get('kind') == 'repeat_requested' for e in events):
            return 'observed', 'repeat_requested_no_replay_requires_server_audit'
    return 'unknown', 'required_observation_absent; canonical trace/journal audit required'


async def perform_fault(page, kind: str, *, server_control=None):
    if kind in {'device_lost', 'device_muted', 'malformed_protocol', 'duplicate_sequence', 'queue_overrun'}:
        return await page.evaluate('(kind)=>window.__voiceLabDriver.fault(kind)', kind)
    if kind == 'cancel_before_commit':
        await page.locator('#interrupt').click()
        return {'kind': kind, 'action': 'interrupt_requested'}
    if kind == 'cancel_after_commit':
        return {'kind': kind, 'action': 'task_commit_event_interrupt',
                'probe': await page.evaluate('()=>window.__voiceLabDriver.snapshot()')}
    if kind == 'lost_rendered_ack':
        await page.locator('#disconnect').click()
        await wait_state(page, ['disconnected'])
        await page.locator('#retry').click()
        await wait_state(page, ['listening', 'failed'])
        return {'kind': kind, 'action': 'explicit_bounded_resume_requested'}
    if kind == 'silence_timeout':
        await wait_state(page, ['silence_timeout'], timeout=9500)
        return {'kind': kind, 'action': 'actual_silence_deadline_observed'}
    if kind in {'fresh_tool_duplicate', 'service_restart'}:
        if server_control is None:
            raise ValueError('named D4 mock-only fault control not configured')
        return await server_control(page, kind)
    if kind == 'start_end_race':
        await page.evaluate('()=>{document.querySelector("#start").click();document.querySelector("#end").click();}')
        await page.wait_for_timeout(300)
        return {'kind': kind, 'action': 'same_task_start_end_race'}
    if kind == 'permission_denied':
        return {'kind': kind, 'action': 'preconnection_permission_rejection'}
    raise ValueError('unsupported development fault')


async def run_journeys(spec: dict, nonce: str, sink, *, plan: dict | None = None,
                       server_control=None) -> dict:
    tasks = tasks_from(spec, plan)
    references, incomplete = {}, []
    for task in tasks:
        sink.emit('task_scheduled', task_id=task['task_id'], arm=task['arm'],
                  logical_turn_ids=task['logical_turn_ids'], cohort=task['cohort'],
                  pair_id=task.get('pair_id'), fault_schedule=task['fault_schedule'])
    controls = MockControls(spec, nonce, sink)
    for number, task in enumerate(tasks):
        page = context = driver = None
        entered = False
        admitted = False
        started: set[str] = set()
        terminal: set[str] = set()
        faults = task['fault_schedule']
        attempted_actions: set[str] = set()
        matched_terminal = False
        def record_attempt(position: int, logical_id: str, kind: str) -> str:
            action_id = f'{task["task_id"]}-{position}-{kind}'
            if action_id not in attempted_actions:
                sink.emit('action_attempted', task_id=task['task_id'], logical_turn_id=logical_id,
                    arm=task['arm'], action_id=action_id, fault=kind, expected=FAULTS[kind])
                attempted_actions.add(action_id)
            return action_id
        try:
            task_spec = bind_task_fixture(spec, task, planned=plan is not None)
            sink.emit('fixture_bound', task_id=task['task_id'], fixture_id=task.get('fixture_id'),
                audio_sha256=task_spec['_fixture']['audio']['sha256'],
                wording_family=task_spec['_fixture'].get('wording_family'),
                speaker=task_spec['_fixture'].get('speaker'), split='dev')
            driver = BrowserDriver(task_spec, nonce, sink)
            await driver.__aenter__()
            entered = True
            page, context = await driver.page(task)
            permission = any(f['kind'] == 'permission_denied' for f in faults)
            await page.evaluate('(options)=>window.__voiceLabDriver.configure(options)',
                                {'permissionDenied': permission, 'dropRenderedAck': False})
            race = any(f['kind'] == 'start_end_race' for f in faults)
            sink.emit('session_start_attempted', task_id=task['task_id'], arm=task['arm'])
            for fault in faults:
                if fault['kind'] in {'permission_denied', 'start_end_race'}:
                    record_attempt(fault['turn_position'], task['logical_turn_ids'][fault['turn_position'] - 1], fault['kind'])
            if race:
                await perform_fault(page, 'start_end_race')
            else:
                await page.locator('#start').click()
                await wait_state(page, ['listening', 'permission_denied', 'failed', 'disconnected'])
            first = await snapshot(page)
            admitted = first['state'] == 'listening'
            if not admitted and not (permission or race):
                incomplete.append(task['task_id'] + '-admission')
            sink.emit('task_admission', task_id=task['task_id'], arm=task['arm'],
                      admission='admitted' if admitted else 'unknown', observation='observed' if admitted else 'unknown',
                      browser_state=first['state'], source='browser_listening; correlate ready envelope')
            for position, logical_id in enumerate(task['logical_turn_ids'], 1):
                task['_logical_turn_id'] = logical_id
                turn_faults = [f['kind'] for f in faults if f['turn_position'] == position]
                for kind in turn_faults:
                    record_attempt(position, logical_id, kind)
                baseline = await snapshot(page) if turn_faults else None
                if 'fresh_tool_duplicate' in turn_faults:
                    await controls.arm(task, 'fresh_tool_duplicate')
                if 'lost_rendered_ack' in turn_faults:
                    await page.evaluate('()=>window.__voiceLabDriver.configure({dropRenderedAck:true})')
                if not admitted:
                    sink.emit('turn_not_started', observation='unknown', task_id=task['task_id'],
                              logical_turn_id=logical_id, reason='no_observed_admission')
                else:
                    sink.emit('turn_start_attempted', task_id=task['task_id'], logical_turn_id=logical_id,
                              arm=task['arm'], planned_turn_position=position,
                              source='driver_stimulus_attempt; server start independently required')
                    started.add(logical_id)
                    if 'silence_timeout' not in turn_faults:
                        words = task_spec['_fixture']['turns']
                        if position > len(words):
                            raise ValueError('fixture has no prespecified text for scheduled turn')
                        # Precommit cancellation must occur before the selected turn stimulus.
                        if 'cancel_before_commit' in turn_faults:
                            await perform_fault(page, 'cancel_before_commit')
                        else:
                            observed = await complete_turn(page, words[position - 1],
                                cancel_after_commit='cancel_after_commit' in turn_faults)
                            sink.emit('turn_response_observed', task_id=task['task_id'], logical_turn_id=logical_id,
                                      arm=task['arm'], snapshot=observed, spoken_correctness='unknown')
                for kind in turn_faults:
                    action_id = f'{task["task_id"]}-{position}-{kind}'
                    try:
                        proof = None
                        if kind not in {'cancel_before_commit', 'start_end_race'}:
                            async def control(_page, control_kind):
                                return await controls.observe(task, control_kind)
                            proof = await perform_fault(page, kind, server_control=server_control or control)
                        await page.wait_for_timeout(100)
                        observed = await snapshot(page)
                        trace = await trace_export(page)
                        if kind in {'fresh_tool_duplicate', 'service_restart'}:
                            status, outcome = effect_observation(kind, proof or {})
                        else:
                            status, outcome = observed_outcome(kind, observed, trace, baseline)
                        sink.emit('action_observed', observation=status, task_id=task['task_id'],
                                  logical_turn_id=logical_id, action_id=action_id, fault=kind,
                                  outcome=outcome, snapshot=observed, baseline=baseline,
                                  producer_proof=proof if kind in {'fresh_tool_duplicate', 'service_restart'} else None)
                        if status != 'observed':
                            incomplete.append(action_id)
                        elif observed['state'] not in {'listening', 'playing', 'awaiting_response'}:
                            matched_terminal = True
                    except Exception as exc:
                        incomplete.append(action_id)
                        sink.emit('action_observed', observation='unknown', task_id=task['task_id'],
                                  logical_turn_id=logical_id, action_id=action_id,
                                  fault=kind, outcome='fault_control_or_observation_unavailable',
                                  error_type=type(exc).__name__)
                if logical_id in started:
                    # Driver completion never substitutes for relay's terminal or audio adjudication.
                    sink.emit('turn_driver_ended', observation='unknown', task_id=task['task_id'],
                              logical_turn_id=logical_id, arm=task['arm'],
                              outcome='driver_step_ended; authoritative_evidence_pending')
                    terminal.add(logical_id)
                if page and await page.locator('#state').inner_text() not in {'listening', 'playing', 'awaiting_response'}:
                    if not matched_terminal:
                        incomplete.append(task['task_id'] + '-unexpected-terminal')
                    break
        except Exception as exc:
            incomplete.append(task['task_id'])
            sink.emit('task_driver_failure', observation='unknown', task_id=task['task_id'],
                      error_type=type(exc).__name__, reason='bounded_driver_step_failed')
        finally:
            if page:
                try:
                    await page.locator('#end').click()
                    await wait_state(page, ['closed'])
                    final = await snapshot(page)
                    trace = await trace_export(page)
                    references[f'browser-{number}'] = sink.artifact(f'browser-{number:03d}.json',
                        {'task_id': task['task_id'], 'arm': task['arm'], 'trace': trace,
                         'snapshot': final, 'trace_may_be_truncated': True}, 'browser_observations')
                    if task.get('_session_id'):
                        # End requests durable finalization; read the authenticated journal,
                        # not a fabricated browser outcome. Missing terminal remains unknown.
                        evidence = await server_evidence(spec, nonce, task)
                        references[f'server-{number}'] = sink.artifact(f'server-{number:03d}.json',
                            {'planned_task_id': task['task_id'], 'observed': evidence}, 'server_evidence')
                        if task.get('_ended_envelope'):
                            references[f'terminal-{number}'] = sink.artifact(f'terminal-{number:03d}.json',
                                task['_ended_envelope'], 'terminal_wire_evidence')
                        if spec.get('relay_artifact_root'):
                            try:
                                raw = collect_relay_trace(spec, evidence)
                                references[f'trace-{number}'] = sink.bytes_artifact(
                                    f'server-trace-{number:03d}.jsonl', raw, 'trace')
                                ledger = sink.authoritative_artifact(collect_ledger_snapshot(spec, evidence), 'ledger_snapshot')
                                references[f'ledger-{number}'] = ledger
                                sink.emit('ledger_snapshot_reference', task_id=task['task_id'],
                                    session_id=task['_session_id'], server_run_id=task.get('_server_run_id'),
                                    scope='run_snapshot', references={'ledger': ledger})
                            except (OSError, ValueError, KeyError):
                                incomplete.append(task['task_id'] + '-server-trace')
                                sink.emit('server_trace_unknown', observation='unknown',
                                    task_id=task['task_id'], reason='missing_terminal_hash_or_owned_trace')
                        else:
                            incomplete.append(task['task_id'] + '-server-trace')
                            sink.emit('server_trace_unknown', observation='unknown',
                                task_id=task['task_id'], reason='explicit_owned_relay_artifact_root_absent')
                except Exception as exc:
                    incomplete.append(task['task_id'] + '-cleanup')
                    sink.emit('cleanup_unknown', observation='unknown', task_id=task['task_id'],
                              error_type=type(exc).__name__)
            for logical_id in started - terminal:
                sink.emit('turn_terminal_missing', observation='unknown', task_id=task['task_id'],
                          logical_turn_id=logical_id, outcome='unknown_failure')
            for logical_id in set(task['logical_turn_ids']) - started:
                sink.emit('scheduled_turn_unattempted', observation='unknown', task_id=task['task_id'],
                          logical_turn_id=logical_id, reason='task_stopped_before_turn')
                if not matched_terminal:
                    incomplete.append(logical_id + '-unattempted')
            for fault in faults:
                action_id = f'{task["task_id"]}-{fault["turn_position"]}-{fault["kind"]}'
                if action_id not in attempted_actions:
                    incomplete.append(action_id + '-unattempted')
                    sink.emit('action_unattempted', observation='unknown', task_id=task['task_id'],
                        action_id=action_id, fault=fault['kind'], reason='earlier_task_termination')
            sink.emit('task_driver_ended', task_id=task['task_id'], admitted_observed=admitted,
                      started_actions=len(started), terminal_actions=len(terminal),
                      product_outcome='unknown_until_D5_assembly')
            if entered:
                await driver.__aexit__(None, None, None)
    return {'references': references, 'incomplete': incomplete, 'tasks': len(tasks),
            'product_acceptance': False, 'evidence_label': 'dev diagnostic only'}
