"""Pre/post render-graph calibration source. Physical/acoustic behavior stays unknown."""
from __future__ import annotations

import asyncio
import math

from .browser import BrowserDriver
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


async def calibrate_page(page, sink, identity: str, phase: str) -> dict:
    from voicelab.reporting.diagnostics import calibration_report
    rows, raw_anchors, controls_by_repeat, failures = [], [], [], []
    cursor = DiagnosticCursor()
    cursor.cursor = (await app_snapshot(page))['trace_cursor']
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
        'cancel_control': cancellation, 'summary': analysis, 'failures': failures,
        'full_gate_complete': full, 'full_gate_pass': full, 'required_repeats': 30,
        'acoustic_uncertainty': 'unknown; output timestamp estimate and render graph only',
        'product_acceptance': False}


async def run_calibration(spec: dict, nonce: str, sink, phase: str) -> dict:
    identity = f'calibration-{phase}'
    task = {'task_id': identity, 'arm': 'K'}
    async with BrowserDriver(spec, nonce, sink) as driver:
        page, _ = await driver.page(task)
        await page.locator('#start').click()
        await wait_state(page, ['listening'])
        result = await calibrate_page(page, sink, identity, phase)
        await page.locator('#end').click()
        cleanup = await wait_cleanup(page)
        reference = sink.artifact(f'calibration-{phase}.json', result, 'calibration_observations')
        cleanup_valid = cleanup.get('observation_statuses', {}).get('cleanup') == 'observed_released'
        return {'references': {identity: reference}, 'summary': result,
                'snapshot': cleanup, 'incomplete': [] if result['full_gate_pass'] and cleanup_valid else [identity],
                'evidence_label': 'single pre/post phase; pair matching and independent acceptance not established'}
