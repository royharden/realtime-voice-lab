"""Independent synthetic orchestration controls; no browser/server/device is launched.

These exercise the production run_soak scheduler against explicit observed test doubles.
Passing them is test suitability evidence, never an executed full soak or W7/W8 verdict.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from voicelab.harness import soak


class Sink:
    def __init__(self):
        self.events = []
        self.artifacts = {}

    def emit(self, kind, **value):
        self.events.append({'kind': kind, **deepcopy(value)})

    def artifact(self, name, value, kind):
        self.artifacts[name] = deepcopy(value)
        return {'path': name, 'kind': kind}


def released():
    return {'owned_handles': {'socket': 0, 'graph': 0},
            'observation_statuses': {'owned_handles': 'observed_api_state',
                                     'cleanup': 'observed_released'}}


def configure(monkeypatch, fault=None):
    """Boundary doubles emit starts/terminals only when complete_turn is actually called."""
    sink = Sink()
    state = SimpleNamespace(samples=0, contexts=[], ended=[], turns=0)

    class Page:
        def __init__(self):
            self.trace = []
            self.snapshot = {'fenceCounter': 0, 'cancelAcks': 0, 'resumeCount': 0,
                             'lastFence': {'epoch': 0, 'connection': 0}}

        def locator(self, selector):
            async def click():
                if selector == '#retry':
                    self.snapshot['resumeCount'] += 1
                    self.snapshot['lastFence']['connection'] += 1
                    if fault != 'reconnect_trace':
                        self.trace.append({'kind': 'reconnect', 'session_id': self.task['task_id'], 'connection_epoch':
                                           self.snapshot['lastFence']['connection']})
            return SimpleNamespace(click=click)

    class Context:
        async def new_cdp_session(self, page):
            async def send(*args):
                return {}
            return SimpleNamespace(send=send)

    class Driver:
        def __init__(self, *args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def page(self, task):
            page = Page()
            state.contexts.append(page)
            return page, Context()

    class Cursor:
        def __init__(self):
            self.lost = int(fault == 'lost_diagnostics')
            self.max_queue_seconds = 2.01 if fault == 'event_queue' else .01
            self.queue_events = 0 if fault == 'missing_queue' else 10
            self.queue_unknown = fault == 'unknown_queue'
            self.failure_observed = fault == 'failed_state'

        async def drain(self, *args):
            return []

    async def start(lane, identity, _sink):
        lane.task.clear()
        lane.task.update(task_id=identity, _session_id=identity)
        lane.page.trace.clear()
        lane.page.task = lane.task
        lane.sample_rate = 48000

    async def complete(page, words, *, cancel_after_commit):
        if fault == 'worker_timeout' and state.turns >= 3:
            raise asyncio.TimeoutError('controlled worker time cap')
        identity = page.task['task_id']
        turn = page.task['_logical_turn_id']
        page.trace.append({'kind': 'turn_started', 'session_id': identity,
                           'logical_turn_id': turn})
        if fault != 'missing_terminal':
            page.trace.append({'kind': 'turn_ended', 'session_id': identity,
                               'logical_turn_id': turn, 'terminal_outcome':
                               'failed' if fault == 'failed_terminal' else 'success'})
        if cancel_after_commit:
            page.snapshot['fenceCounter'] += 1
            page.snapshot['cancelAcks'] += 1
            page.snapshot['lastFence']['epoch'] += 1
            if fault != 'cancel_trace':
                page.trace.append({'kind': 'cancel_dispatch', 'session_id': identity, 'response_epoch':
                                   page.snapshot['lastFence']['epoch']})
        state.turns += 1
        await asyncio.sleep(0)

    async def end(lane, *args):
        state.ended.append(lane.task['task_id'])
        cleanup = released()
        if fault == 'cleanup_unknown':
            cleanup['observation_statuses']['cleanup'] = 'unknown'
        return deepcopy(lane.page.trace), cleanup

    async def sample(*args, **kwargs):
        state.samples += 1
        row = {'monotonic_seconds': state.samples * (5 if fault == 'cadence' else 2),
               'after_warmup': kwargs['after_warmup'], 'rss_bytes': 100000,
               'browser_heap_bytes': 10000, 'owned_handles': 0, 'queue_seconds': 0,
               'worklet_queue_seconds': 3 if fault == 'worklet_queue' else 0,
               'sockets': 0, 'files': 0, 'async_tasks': 1, 'event_loop_lag_ms': 0,
               'browser_heaps_by_context': {'c0': 10000, 'c1': 10000, 'c2': 10000},
               'server_observations': {'boot_id': 'boot-' + str(state.samples)
                                       if fault == 'changed_boot' else 'one-boot',
                                       'admitted_sessions': 1 if fault == 'server_leak' else 0}}
        if fault == 'unknown_metric':
            row['rss_bytes'] = None
        return row

    async def probe(page):
        return deepcopy(page.snapshot)

    async def calibrate(*args, **kwargs):
        return {'full_gate_pass': fault != 'calibration'}

    async def yes(*args, **kwargs):
        return fault != 'fourth_admitted'

    async def snapshot(*args):
        return {'state': 'listening'}

    async def cleanup(*args):
        return released()

    # Only the sampler stop.wait uses this fast controlled timeout. Worker wait_for
    # retains asyncio's real cancellation behavior; no global event loop patches.
    async def wait_for(awaitable, timeout):
        if getattr(awaitable, '__name__', '') == 'wait':
            awaitable.close()
            await asyncio.sleep(0)
            raise asyncio.TimeoutError
        return await asyncio.wait_for(awaitable, timeout)

    proxy = SimpleNamespace(Event=asyncio.Event, gather=asyncio.gather,
                            create_task=asyncio.create_task, TimeoutError=asyncio.TimeoutError,
                            wait_for=wait_for, sleep=lambda seconds: asyncio.sleep(0))
    for name, value in {'BrowserDriver': Driver, 'DiagnosticCursor': Cursor,
                        'start_session': start, 'end_session': end, 'complete_turn': complete,
                        'probe_state': probe, 'calibrate_page': calibrate, 'resource_sample': sample,
                        'fourth_refusal': yes, 'app_snapshot': snapshot, 'wait_cleanup': cleanup,
                        'wait_state': yes, 'asyncio': proxy}.items():
        monkeypatch.setattr(soak, name, value)
    return sink, state


def run(monkeypatch, fault=None, profile='full'):
    sink, state = configure(monkeypatch, fault)
    spec = {'run_id': 'synthetic-control', '_manifest_hash': '0' * 64,
            '_fixture': {'turns': ['one', 'two', 'three', 'four', 'five'],
                         'audio': {'sha256': '1' * 64}}}
    return asyncio.run(soak.run_soak(spec, 'unused-test-nonce', sink, profile)), sink, state


def test_full_positive_control_counts_executed_effects_and_keeps_acceptance_separate(monkeypatch):
    """what_bug_this_catches: a vacuous always-incomplete oracle or attempted-effect counts."""
    result, sink, state = run(monkeypatch)
    summary = result['summary']
    assert result['incomplete'] == []
    assert (summary['aggregate_turns'], summary['interruptions'], summary['reconnects']) == (200, 40, 10)
    assert summary['full_gate_pass'] is True and summary['product_acceptance'] is False
    assert state.turns == 200 and len(state.ended) == len(set(state.ended)) == 40
    assert len(state.contexts) == 3
    assert len([e for e in sink.events if e['kind'] == 'task_scheduled']) == 40
    assert summary['fourth_session_refused'] is True


@pytest.mark.parametrize('fault, pointer', [
    ('missing_terminal', 'scheduled-start-terminal-denominators-incomplete'),
    ('failed_terminal', 'unexpected-turn-outcome'),
    ('cancel_trace', 'observed-effects-below-profile-coverage'),
    ('reconnect_trace', 'observed-effects-below-profile-coverage'),
    ('cleanup_unknown', '-cleanup'),
    ('cadence', 'resource-or-diagnostic-cadence-unknown'),
    ('lost_diagnostics', 'resource-or-diagnostic-cadence-unknown'),
    ('unknown_metric', 'required-resource-metric-unknown'),
    ('changed_boot', 'own-server-boot-observation-incomplete-or-changed'),
    ('server_leak', 'post-End-owned-resource-release-not-proven'),
    ('event_queue', 'event-level-queue-bound-unproven-or-exceeded'),
    ('missing_queue', 'event-level-queue-bound-unproven-or-exceeded'),
    ('unknown_queue', 'event-level-queue-bound-unproven-or-exceeded'),
    ('failed_state', 'event-level-queue-bound-unproven-or-exceeded'),
    ('worklet_queue', 'observed-worklet-queue-exceeds-two-seconds'),
    ('calibration', 'pre-post-calibration-incomplete'),
    ('fourth_admitted', 'fourth-admission-not-proven-refused'),
])
def test_full_missing_or_contradictory_observations_cannot_pass(monkeypatch, fault, pointer):
    """what_bug_this_catches: a claimed full pass despite a missing required observation."""
    result, _, state = run(monkeypatch, fault)
    assert state.turns == 200  # attempted workload alone is insufficient
    assert result['summary']['full_gate_pass'] is False
    assert any(pointer in reason for reason in result['incomplete'])


def test_short_diagnostic_never_substitutes_for_full(monkeypatch):
    """what_bug_this_catches: promoting the short two-context profile to a full soak."""
    result, sink, state = run(monkeypatch, profile='short')
    summary = result['summary']
    assert result['incomplete'] == []
    assert summary['aggregate_turns'] == state.turns == 50
    assert summary['contexts'] == len(state.contexts) == 2
    assert summary['full_gate_pass'] is False and summary['fourth_session_refused'] is False
    assert summary['resource_gate_pass'] is None
    assert 'never full-soak acceptance' in result['evidence_label']
    assert sink.artifacts['soak-profile.json']['profile'] == 'short'


def test_worker_time_cap_preserves_unexecuted_session_denominators(monkeypatch):
    """what_bug_this_catches: dropping scheduled-but-unstarted work after a worker abort."""
    result, sink, state = run(monkeypatch, 'worker_timeout')
    assert result['summary']['full_gate_pass'] is False
    assert result['summary']['stop_reason'] == 'time_cap_or_worker_failure'
    assert state.turns < 200
    scheduled = {e['task_id'] for e in sink.events if e['kind'] == 'task_scheduled'}
    incomplete = {e['task_id'] for e in sink.events if e['kind'] == 'scheduled_session_incomplete'}
    assert len(scheduled) == 40
    assert incomplete == scheduled - set(state.ended)
    assert incomplete


@pytest.mark.parametrize('change, expected', [
    ({}, True), ({'code': 1000}, False), ({'reason': 'closed'}, False),
    ({'admitted_sessions': 2}, False), ({'admitted_sessions': 4}, False),
    ({'fourth_session': 'unexpected-admission'}, False), ({'missing_lane': True}, False),
])
def test_fourth_refusal_requires_actual_close_and_three_live_admissions(monkeypatch, change, expected):
    """what_bug_this_catches: treating any fourth-window failure as capacity refusal."""
    from voicelab.harness import browser

    class Context:
        closed = False

        async def close(self):
            self.closed = True

    context = Context()
    driver = SimpleNamespace(spec={}, nonce='test-only', contexts=[context])
    sink = Sink()
    lanes = [SimpleNamespace(task={'_session_id': str(i)}) for i in range(3)]
    if change.get('missing_lane'):
        lanes[0].task.clear()

    async def no_op(*args, **kwargs):
        pass

    async def page(task):
        if change.get('fourth_session'):
            task['_session_id'] = change['fourth_session']
        return SimpleNamespace(locator=lambda selector: SimpleNamespace(click=no_op)), context

    async def probe(*args):
        return {'lastClose': {'code': change.get('code', 1008),
                              'reason': change.get('reason', 'admission refused')}}

    driver.page = page
    monkeypatch.setattr(soak, 'wait_state', no_op)
    monkeypatch.setattr(soak, 'wait_cleanup', no_op)
    monkeypatch.setattr(soak, 'probe_state', probe)
    monkeypatch.setattr(browser, 'own_diagnostic', lambda *args: {
        'admitted_sessions': change.get('admitted_sessions', 3)})
    assert asyncio.run(soak.fourth_refusal(driver, lanes, sink)) is expected
    assert context.closed and driver.contexts == []
    observation = sink.events[-1]
    assert observation['fourth_session_refused'] is expected
    assert observation['refusal_observation'] == ('observed' if expected else 'unknown')
