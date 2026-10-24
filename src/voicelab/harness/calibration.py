"""Pre/post render-graph calibration source. Physical/acoustic behavior stays unknown."""
from __future__ import annotations

import asyncio
import math
import time
import re

from .browser import BrowserDriver, server_evidence, collect_relay_trace, collect_ledger_snapshot, own_diagnostic
from .journey import wait_state
from .observations import DiagnosticCursor, app_snapshot, wait_cleanup


async def probe(page, options: dict) -> dict:
    before = await page.evaluate('()=>window.__voiceLabDriver.graphStamp()')
    request = await page.evaluate('(options)=>window.__voiceLabDiagnostics.diagnosticProbe(options)', options)
    await page.wait_for_function('''(id)=>{
      const state=window.__voiceLabDiagnostics.snapshot();
      return state.state!=='calibrating' && state.last_probe?.probe_id===id &&
        state.last_probe.events.some(event=>['probe_done','probe_canceled'].includes(event.kind));
    }''', arg=request['probe_id'], timeout=3500)
    observed = (await app_snapshot(page))['last_probe']
    after = await page.evaluate('()=>window.__voiceLabDriver.graphStamp()')
    if (request.get('graph_id') != before.get('graph_id') or after.get('graph_id') != request.get('graph_id')
            or observed.get('sample_id') != request.get('sample_id')
            or any(event.get('probe_id') != request['probe_id'] or event.get('graph_id') != request['graph_id']
                   for event in observed.get('events', []))):
        raise ValueError('probe/graph/sample anchor identity mismatch')
    return {'request': request, 'result': observed, 'before': before, 'after': after,
            'options': options, 'boundary': 'worklet_render_graph', 'physical_audio': 'unknown'}


def mapped_ms(frame: int, rate: int, stamp: dict) -> float | None:
    output = stamp.get('output_timestamp', {})
    context, performance = output.get('contextTime'), output.get('performanceTime')
    if not all(isinstance(v, (int, float)) and math.isfinite(v) and v >= 0
               for v in (frame, rate, context, performance)) or rate <= 0 or performance == 0:
        return None
    # getOutputTimestamp already estimates device output; never add outputLatency again.
    return performance + (frame / rate - context) * 1000


def gap_row(raw: dict, index: int) -> tuple[dict, dict]:
    request, result = raw['request'], raw['result']
    events = result.get('events', [])
    started = next((e for e in events if e.get('kind') == 'probe_started'), {})
    ended = next((e for e in events if e.get('kind') == 'probe_done'), {})
    pulses = {e.get('pulse'): e for e in events if e.get('kind') == 'probe_pulse'}
    rate, quantum = request.get('sample_rate'), started.get('quantum')
    # The declared producer supplies its exact pulse length; absent is not guessed.
    pulse_frames = request.get('pulse_frames')
    gap_frames = started.get('gap_frames')
    complete = (all(type(v) is int and v > 0 for v in (rate, quantum, pulse_frames, gap_frames))
                and all(type(e.get('frame')) is int for e in (started, ended, pulses.get(0, {}), pulses.get(1, {}))))
    row = {'repeat_index': index, 'scheduled_gap_ms': raw['options']['gap_ms'],
           'observed_gap_ms': None, 'observed_quantum_ms': quantum / rate * 1000 if rate and quantum else None,
           'cutoff_error_samples': None, 'quantum_samples': quantum,
           'mapping_monotonic': False, 'clock_domain': 'browser_performance_ms',
           'sample_anchor_clock_domain': 'audio_context_samples', 'observation': 'unknown',
           'cutoff_kind': 'scheduled_signal_end; separate cancellation control required'}
    controls = {'sample_anchor_valid': False, 'past_schedule_clamped': None,
                'injected_offset_rejected': False}
    if not complete:
        return row, controls
    onset0, onset1 = pulses[0]['frame'], pulses[1]['frame']
    first = mapped_ms(onset0, rate, raw['after'])
    second = mapped_ms(onset1, rate, raw['after'])
    before_stamp = raw['before']['output_timestamp']
    after_stamp = raw['after']['output_timestamp']
    monotonic = (first is not None and second is not None and second > first
                 and all(isinstance(stamp.get('performanceTime'), (int, float))
                         and math.isfinite(stamp['performanceTime']) and stamp['performanceTime'] > 0
                         for stamp in (before_stamp, after_stamp))
                 and after_stamp['contextTime'] > before_stamp['contextTime']
                 and after_stamp['performanceTime'] > before_stamp['performanceTime'])
    expected_end = started['frame'] + pulse_frames * 2 + gap_frames
    row.update(observed_gap_ms=(second - first - pulse_frames / rate * 1000) if monotonic else None,
        cutoff_error_samples=ended['frame'] - expected_end, mapping_monotonic=monotonic,
        observation='observed' if monotonic else 'unknown')
    expected_first = mapped_ms(started['frame'], rate, raw['after'])
    tolerance = max(20, quantum / rate * 2000)
    anchor = first is not None and expected_first is not None and abs(first - expected_first) <= tolerance
    controls['sample_anchor_valid'] = anchor
    # Actual anchors must match, while a deliberately wrong +100ms mapping must fail.
    controls['injected_offset_rejected'] = anchor and abs(first + 100 - expected_first) > tolerance
    if raw['options'].get('offset_ms', 0) < -100:
        before_frame = math.floor(raw['before']['context_time'] * rate)
        controls['past_schedule_clamped'] = (request['requested_frame'] < before_frame
            and started['frame'] >= before_frame and onset0 >= started['frame'])
    return row, controls


async def calibrate_page(page, sink, identity: str, phase: str, *, spec=None, nonce=None, task=None) -> dict:
    initial = await app_snapshot(page)
    if initial['state'] != 'awaiting_response':
        return await _calibrate_page(page, sink, identity, phase)
    result = {'phase': phase, 'rows': [], 'raw_anchors': [], 'controls': {},
              'failures': [], 'required_repeats': 30, 'capture_return_state': 'awaiting_response',
              'full_gate_complete': False, 'full_gate_pass': False, 'product_acceptance': False}
    session = (task or {}).get('_session_id')
    if spec is None or nonce is None or not isinstance(session, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,96}', session):
        result['failures'].append('paused_calibration_phase_protocol_unavailable')
        return result
    if (initial.get('active_probe') is not None or initial.get('queue_seconds', {}).get('playback') != 0
            or initial.get('worklet', {}).get('status') != 'observed'
            or initial.get('worklet', {}).get('queued_frames') != 0):
        result['failures'].append('paused_calibration_drained_graph_unobserved')
        return result
    phase_id = f'calibration-{phase}'
    active = False
    begin_attempted = False
    async def control(action):
        from voicelab.contracts import CalibrationReceipt
        raw = await asyncio.to_thread(own_diagnostic, spec, nonce,
            f'/sessions/{session}/calibration', body={'phase_id': phase_id, 'action': action})
        receipt = CalibrationReceipt.model_validate(raw).model_dump(mode='json')
        if (receipt['session_id'] != session or receipt['run_id'] != task.get('_server_run_id')
                or receipt['trial_id'] != task.get('_trial_namespace')
                or receipt['boot_id'] != task.get('_boot_id') or receipt['phase_id'] != phase_id
                or receipt['captured_turn_created'] is not False or receipt['task_deadlines_retained'] is not True
                or receipt['absolute_cap_ms'] != 30000 or receipt['phase_limit'] != 2
                or receipt['deadline_ns'] > receipt['session_deadline_ns']
                or not 0 < receipt['deadline_ns'] - receipt['started_ns'] <= 30_000_000_000):
            raise ValueError('calibration_phase_receipt_scope_or_deadline_mismatch')
        sink.emit('calibration_phase_receipt', task_id=identity, phase=phase, receipt=receipt)
        return receipt
    try:
        # D6 independently refuses undrained response/queue state at probe admission.
        # HTTP phase does not send capture input or change transport/response epochs.
        began = time.monotonic()
        begin_attempted = True
        receipt = await control('begin')
        active = receipt['status'] == 'active'
        if not active or receipt['duplicate']:
            raise ValueError('original_calibration_phase_already_ended_no_renewal')
        duration = (receipt['deadline_ns'] - receipt['started_ns']) / 1e9
        result = await asyncio.wait_for(_calibrate_page(page, sink, identity, phase),
            timeout=max(.001, duration - (time.monotonic() - began)))
        result['calibration_phase_receipt'] = receipt
    except Exception as exc:
        result['failures'].append('phase-' + type(exc).__name__)
        result['full_gate_complete'] = result['full_gate_pass'] = False
    finally:
        if begin_attempted:
            try:
                ended = await control('end')
                result['calibration_phase_end'] = ended
                if ended['status'] != 'ended':
                    raise ValueError('calibration_phase_end_unobserved')
            except Exception as exc:
                result['failures'].append('phase-end-' + type(exc).__name__)
                result['full_gate_complete'] = result['full_gate_pass'] = False
    return result


async def _calibrate_page(page, sink, identity: str, phase: str) -> dict:
    from voicelab.reporting.diagnostics import calibration_report
    rows, raw_anchors, controls_by_repeat, failures = [], [], [], []
    cursor = DiagnosticCursor()
    initial = await app_snapshot(page)
    paused = initial['state'] == 'awaiting_response'
    cursor.cursor = initial['trace_cursor']
    sink.emit('calibration_phase_started', task_id=identity, phase=phase, cursor=cursor.cursor)
    for index in range(30):
        options = {'kind': 'offset' if index == 1 else 'gap', 'gap_ms': 100,
                   'offset_ms': -250 if index == 0 else 100 if index == 1 else 0}
        sink.emit('calibration_probe_attempted', task_id=identity, phase=phase, repeat_index=index,
                  probe_kind=options['kind'], options=options)
        try:
            raw = await probe(page, options)
            row, control = gap_row(raw, index)
            rows.append(row)
            raw_anchors.append(raw)
            controls_by_repeat.append(control)
            sink.emit('calibration_probe_observed', task_id=identity, phase=phase,
                      repeat_index=index, raw_anchors=raw, row=row, controls=control)
            await cursor.drain(page, sink, identity)
        except Exception as exc:
            failures.append(f'repeat-{index}-{type(exc).__name__}')
            rows.append({'repeat_index': index, 'observation': 'unknown'})
            controls_by_repeat.append({})
            sink.emit('calibration_probe_observed', observation='unknown', task_id=identity,
                      phase=phase, repeat_index=index, error_type=type(exc).__name__)
            break  # do not silently repair a failed graph or restart capture
    cancellation = {}
    try:
        sink.emit('calibration_cancel_attempted', task_id=identity, phase=phase)
        rate = (await page.evaluate('()=>window.__voiceLabDriver.graphStamp()'))['sample_rate']
        cancellation = await probe(page, {'kind': 'cancel', 'gap_ms': 100, 'offset_ms': 0,
                                          'cancel_after_frames': math.ceil(rate * .02)})
        events = cancellation['result']['events']
        canceled = next((e for e in events if e.get('kind') == 'probe_canceled'), {})
        started = next((e for e in events if e.get('kind') == 'probe_started'), {})
        expected = cancellation['request'].get('intended_cutoff_frame')
        # Main-thread timers alone do not establish a requested sample cutoff.
        cancel_valid = (type(expected) is int and type(canceled.get('frame')) is int
                        and canceled.get('frame') == expected
                        and isinstance(canceled.get('last_frame'), int)
                        and canceled.get('cancel_origin') == 'scheduled_worklet'
                        and 0 < canceled['frame'] - canceled['last_frame'] <= started.get('quantum', 0))
        before = await page.evaluate('()=>window.__voiceLabDriver.snapshot()')
        if not paused:
            await page.locator('#interrupt').click()
            await wait_state(page, ['listening'])
            await page.wait_for_function('''(prior)=>{
              const now=window.__voiceLabDriver.snapshot();
              return now.fenceCounter>prior.fenceCounter && now.cancelAcks>prior.cancelAcks;
            }''', arg=before, timeout=2000)
        await page.evaluate('()=>window.__voiceLabDriver.staleControl()')
        await page.wait_for_function('(prior)=>window.__voiceLabDriver.snapshot().staleDiscards>prior',
                                     arg=before.get('staleDiscards', 0), timeout=2000)
        after = await page.evaluate('()=>window.__voiceLabDriver.snapshot()')
        if paused:
            sample_fence = next((e for e in events if e.get('kind') == 'probe_fence_applied'), {})
            fence_valid = cancel_valid and sample_fence.get('frame') == expected
            if (await app_snapshot(page))['state'] != 'awaiting_response':
                raise ValueError('post calibration must preserve paused capture without a new task turn')
        else:
            fence_valid = (after['fenceCounter'] > before['fenceCounter']
                           and after['lastFence']['epoch'] > (before.get('lastFence') or {}).get('epoch', -1))
    except Exception as exc:
        cancel_valid = fence_valid = False
        after = {}
        failures.append('cancel-' + type(exc).__name__)
    controls = {
        'app_worklet_anchor_valid': len(controls_by_repeat) == 30 and all(c.get('sample_anchor_valid') is True for c in controls_by_repeat),
        'injected_offset_rejected': len(controls_by_repeat) == 30 and all(c.get('injected_offset_rejected') is True for c in controls_by_repeat),
        'past_scheduling_anchor_valid': any(c.get('past_schedule_clamped') is True for c in controls_by_repeat),
        'fence_ack_observed': fence_valid,
        'app_fence_cutoff_within_quantum': cancel_valid,
        'stale_epoch_frame_refused': bool(after) and after.get('staleDiscards', 0) > 0,
    }
    analysis = calibration_report(rows)
    full = len(rows) == 30 and analysis['internal_gate_pass'] and all(controls.values()) and not failures and cursor.lost == 0
    return {'phase': phase, 'rows': rows, 'raw_anchors': raw_anchors, 'controls': controls,
        'capture_return_state': 'awaiting_response' if paused else 'listening',
        'fence_scope': 'fresh scheduled probe sample fence; retained session epoch unchanged' if paused else 'fresh session epoch fence and ACK',
        'cancel_control': cancellation, 'summary': analysis, 'failures': failures,
        'full_gate_complete': full, 'full_gate_pass': full, 'required_repeats': 30,
        'acoustic_uncertainty': 'unknown; output timestamp estimate and render graph only',
        'product_acceptance': False}


async def run_calibration(spec: dict, nonce: str, sink, phase: str) -> dict:
    identity = f'calibration-{phase}'
    task = {'task_id': identity, 'arm': 'K', 'role': 'calibration_control'}
    sink.emit('auxiliary_session_scheduled', task_id=identity, role='calibration_control', phase=phase)
    async with BrowserDriver(spec, nonce, sink) as driver:
        page, _ = await driver.page(task)
        await page.locator('#start').click()
        await wait_state(page, ['listening'])
        result = await calibrate_page(page, sink, identity, phase, spec=spec, nonce=nonce, task=task)
        await page.locator('#end').click()
        cleanup = await wait_cleanup(page)
        fixture_cleanup = None
        if spec.get('_fixture_stream') is True:
            fixture_cleanup = await page.evaluate('()=>window.__voiceLabFixtureSource.close()')
        reference = sink.artifact(f'calibration-{phase}.json', result, 'calibration_observations')
        references = {identity: reference}
        evidence_complete = False
        try:
            evidence = await server_evidence(spec, nonce, task)
            references[identity + '-server'] = sink.artifact(
                identity + '-server.json', evidence, 'server_evidence')
            references[identity + '-trace'] = sink.bytes_artifact(
                identity + '-trace.jsonl', collect_relay_trace(spec, evidence), 'trace')
            references[identity + '-ledger'] = sink.authoritative_artifact(
                collect_ledger_snapshot(spec, evidence), 'ledger_snapshot')
            evidence_complete = evidence['session'].get('terminal_outcome') is not None
        except Exception as exc:
            sink.emit('auxiliary_session_evidence_unknown', task_id=identity,
                      role='calibration_control', error_type=type(exc).__name__, observation='unknown')
        cleanup_valid = cleanup.get('observation_statuses', {}).get('cleanup') == 'observed_released'
        if fixture_cleanup is not None:
            cleanup_valid = cleanup_valid and fixture_cleanup.get('closed') is True and all(
                v == 0 for v in fixture_cleanup['owned_handles'].values())
        return {'references': references, 'summary': result,
                'snapshot': cleanup, 'fixture_source': fixture_cleanup,
                'incomplete': [] if result['full_gate_pass'] and cleanup_valid and evidence_complete else [identity],
                'evidence_label': 'single pre/post phase; pair matching and independent acceptance not established'}
