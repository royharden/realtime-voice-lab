"""Independent sampler controls for the declared D10 diagnostics-r2 interface.

All probe processes/streams below are explicit in-memory doubles. No OS metadata
reader, child process, listener, credential or real device is started by these tests.
Production HTTP aliases use an in-process ASGI client only.
"""
from __future__ import annotations

import asyncio
from collections import deque
import inspect
import json
import time

import pytest


NAMES = ('rss_bytes', 'open_sockets', 'open_files')


class Clock:
    now = 100.0

    def __call__(self):
        return self.now

    def ns(self):
        return int(self.now * 1_000_000_000)


def line(name, value, observed_ns, reason=None):
    return (json.dumps({'name': name, 'value': value, 'observed_ns': observed_ns,
                        'reason': reason}) + '\n').encode()


class Probe:
    """Subprocess-compatible double; no PID or OS operation is ever used."""
    def __init__(self, rows=(), *, blocked=False, ignore_terminate=False, unreapable=False):
        self.rows = deque(rows)
        self.blocked, self.ignore_terminate, self.unreapable = blocked, ignore_terminate, unreapable
        self.returncode = None
        self.stdout = self
        self.done = asyncio.Event()
        self.reader_pending = asyncio.Event()
        self.wait_calls = 0
        self.terminate_calls = self.kill_calls = 0

    async def readline(self):
        await asyncio.sleep(0)
        if self.rows:
            return self.rows.popleft()
        if self.blocked and self.returncode is None:
            self.reader_pending.set()
            try:
                await self.done.wait()
            finally:
                self.reader_pending.clear()
        else:
            self.returncode = 0
            self.done.set()
        return b''

    async def wait(self):
        self.wait_calls += 1
        if not self.blocked and not self.rows and not self.unreapable:
            self.returncode = 0
            self.done.set()
        await self.done.wait()
        return self.returncode

    def terminate(self):
        self.terminate_calls += 1
        if not self.ignore_terminate and not self.unreapable:
            self.returncode = -15
            self.done.set()

    def kill(self):
        self.kill_calls += 1
        if not self.unreapable:
            self.returncode = -9
            self.done.set()


def sampler(monkeypatch, factory, clock=None):
    from voicelab.server import metrics
    clock = clock or Clock()
    launched = []

    async def launch(pid):
        assert type(pid) is int and pid > 0
        probe = factory(clock, len(launched))
        launched.append(probe)
        return probe

    monkeypatch.setattr(metrics, '_launch_probe', launch)
    return metrics.OwnProcessSampler(clock=clock, clock_ns=clock.ns), launched, clock


def rows(clock, values=(1000, 2, 3)):
    return [line(name, value, clock.ns()) for name, value in zip(NAMES, values)]


def unknown(row):
    assert row['value'] is None and row['observation'] == 'unknown'
    assert isinstance(row.get('reason'), str) and row['reason'].strip()



# Responsiveness contract: a 10ms heartbeat may have at most 100ms scheduling
# gap, well below the declared 2s diagnostics cadence. Calibrate eight idle
# intervals; permit 4x measured jitter with a 60ms floor, but never increase the
# ceiling to hide an overloaded harness. A 150ms synchronous block must fail.
HEARTBEAT_INTERVAL = .01
MAX_HEARTBEAT_GAP = .1
SAMPLE_ELAPSED_LIMIT = 2.5


async def measure_responsiveness(operation, is_pending):
    calibration = []
    previous = time.monotonic()
    for _ in range(8):
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        now = time.monotonic()
        calibration.append(now - previous)
        previous = now
    budget = max(.06, 4 * max(calibration))
    assert budget <= MAX_HEARTBEAT_GAP, 'heartbeat calibration exceeds declared 100ms ceiling'
    armed = asyncio.Event()
    timestamps = []
    pending_ticks = 0
    task = None

    async def heartbeat():
        nonlocal pending_ticks
        timestamps.append(time.monotonic())
        armed.set()
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            timestamps.append(time.monotonic())
            if is_pending() and task is not None and not task.done():
                pending_ticks += 1

    ticker = asyncio.create_task(heartbeat())
    await armed.wait()  # independent ticker is armed before operation can block
    started = time.monotonic()
    try:
        task = asyncio.create_task(operation())
        result = await asyncio.wait_for(task, timeout=SAMPLE_ELAPSED_LIMIT)
        finished = time.monotonic()
    finally:
        ticker.cancel()
        try:
            await ticker
        except asyncio.CancelledError:
            pass
    # Include the final interval even if the operation completes before the next tick.
    timestamps.append(finished)
    report = {'budget': budget, 'max_gap': max(b - a for a, b in zip(timestamps, timestamps[1:])),
              'elapsed': finished - started, 'pending_ticks': pending_ticks}
    return result, report


def assert_responsive(report):
    assert report['max_gap'] <= report['budget'], f"heartbeat gap exceeded budget: {report}"
    assert report['elapsed'] <= SAMPLE_ELAPSED_LIMIT, f"collection elapsed bound exceeded: {report}"
    assert report['pending_ticks'] >= 3, f"no repeated heartbeat while reader pending: {report}"


def watch_close(monkeypatch, owner, entered=None):
    """Wrap, rather than replace, the actual sampler shutdown implementation."""
    actual_close = owner.aclose
    events = []

    async def close():
        events.append('entered')
        if entered is not None:
            entered.set()
        try:
            await actual_close()
        except BaseException:
            events.append('failed')
            raise
        events.append('completed')

    monkeypatch.setattr(owner, 'aclose', close)
    return events


def assert_sampler_shutdown(owner, events, launches):
    assert events == ['entered', 'completed'], f"sampler shutdown incomplete: {events}"
    count = len(launches)
    result = asyncio.run(owner.sample())  # actual closed sample must refuse relaunch
    assert len(launches) == count
    for row in result.values():
        unknown(row)
        assert row['reason'] == 'own_process_sampler_closed'


def test_heartbeat_oracle_accepts_async_pending_control_and_rejects_sync_block():
    """what_bug_this_catches: ticks>0 accepting a synchronous 150ms loop block."""
    async def run():
        pending = False

        async def control(blocking):
            nonlocal pending
            pending = True
            try:
                if blocking:
                    time.sleep(.15)  # bounded intentional broken control; no OS probe
                else:
                    await asyncio.sleep(.15)
            finally:
                pending = False
            return 'synthetic'

        positive, asynchronous = await measure_responsiveness(lambda: control(False), lambda: pending)
        assert positive == 'synthetic'
        assert_responsive(asynchronous)
        negative, synchronous = await measure_responsiveness(lambda: control(True), lambda: pending)
        assert negative == 'synthetic'
        assert synchronous['max_gap'] >= .15
        with pytest.raises(AssertionError, match='heartbeat gap exceeded budget'):
            assert_responsive(synchronous)
    asyncio.run(run())


def test_valid_sampler_positive_cache_expiry_and_no_process_accumulation(monkeypatch):
    """what_bug_this_catches: always-unknown output, stale cache reuse or concurrent launches."""
    async def run():
        owner, launched, clock = sampler(monkeypatch,
            lambda clock, index: Probe(rows(clock, (1000 + index, 2, 3))))
        first = await owner.sample()
        assert set(first) == set(NAMES)
        assert [first[name]['value'] for name in NAMES] == [1000, 2, 3]
        assert all(first[name]['observation'] == 'observed' for name in NAMES)
        assert await owner.sample() == first and len(launched) == 1
        clock.now += 2
        refreshed = await owner.sample()
        assert refreshed['rss_bytes']['value'] == 1001 and len(launched) == 2
        assert all(probe.returncode is not None for probe in launched)
        await owner.aclose()
        await owner.aclose()
        closed = await owner.sample()
        assert len(launched) == 2
        for row in closed.values():
            unknown(row)
    asyncio.run(run())


def test_concurrent_requests_share_one_flight(monkeypatch):
    """what_bug_this_catches: one helper launch per simultaneous diagnostics request."""
    async def run():
        owner, launched, _ = sampler(monkeypatch, lambda clock, index: Probe(rows(clock)))
        results = await asyncio.gather(*(owner.sample() for _ in range(20)))
        assert len(launched) == 1
        assert all(result == results[0] for result in results)
        assert results[0]['rss_bytes']['value'] == 1000
        await owner.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('bad', [True, -1, float('nan'), float('inf'), '1000'])
def test_invalid_numeric_worker_observation_stays_unknown(monkeypatch, bad):
    """what_bug_this_catches: bool/negative/nonfinite/string becoming an observed resource."""
    async def run():
        owner, _, _ = sampler(monkeypatch, lambda clock, index: Probe(rows(clock, (bad, 2, 3))))
        result = await owner.sample()
        unknown(result['rss_bytes'])
        assert result['open_sockets']['value'] == 2
        await owner.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('stamp', [None, True, -1, '100000000000', 1, 110_000_000_000])
def test_missing_malformed_stale_or_future_timestamp_stays_unknown(monkeypatch, stamp):
    """what_bug_this_catches: stale/future or malformed metadata being relabeled fresh."""
    async def run():
        owner, _, _ = sampler(monkeypatch, lambda clock, index: Probe([
            line('rss_bytes', 1000, stamp), *rows(clock)[1:]]))
        result = await owner.sample()
        unknown(result['rss_bytes'])
        await owner.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('reason', ['own_process_probe_unsupported', 'own_process_probe_failed'])
def test_unsupported_or_failed_probe_never_fabricates_zero(monkeypatch, reason):
    """what_bug_this_catches: a failed OS reader being converted into a successful zero count."""
    async def run():
        owner, _, _ = sampler(monkeypatch, lambda clock, index: Probe([
            *rows(clock)[:2], line('open_files', None, None, reason)]))
        result = await owner.sample()
        assert result['rss_bytes']['value'] == 1000
        unknown(result['open_files'])
        await owner.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('raw', [b'not json\n', b'[]\n', b'{}\n', b'x' * 10000 + b'\n'])
def test_malformed_worker_line_is_incomplete_and_bounded(monkeypatch, raw):
    """what_bug_this_catches: malformed/oversize worker output omitting required unknown rows."""
    async def run():
        owner, launched, _ = sampler(monkeypatch, lambda clock, index: Probe([raw]))
        result = await asyncio.wait_for(owner.sample(), timeout=2.5)
        assert set(result) == set(NAMES)
        for row in result.values():
            unknown(row)
        await owner.aclose()
        assert len(launched) == 1 and launched[0].returncode is not None
    asyncio.run(run())


def test_blocked_files_reader_keeps_prior_observations_and_loop_progress(monkeypatch):
    """what_bug_this_catches: event-loop blocking, loss of prior RSS or fake file-count zero."""
    async def run():
        owner, launched, _ = sampler(monkeypatch,
            lambda clock, index: Probe(rows(clock)[:2], blocked=True))
        result, responsiveness = await measure_responsiveness(owner.sample,
            lambda: bool(launched and launched[0].reader_pending.is_set()))
        assert_responsive(responsiveness)
        assert result['rss_bytes']['value'] == 1000 and result['open_sockets']['value'] == 2
        unknown(result['open_files'])
        assert launched[0].terminate_calls + launched[0].kill_calls > 0
        assert launched[0].returncode is not None
        await owner.aclose()
    asyncio.run(run())


def test_terminate_ignored_uses_kill_then_reaps(monkeypatch):
    """what_bug_this_catches: timed-out helpers surviving shutdown when terminate is ignored."""
    async def run():
        owner, launched, _ = sampler(monkeypatch,
            lambda clock, index: Probe([], blocked=True, ignore_terminate=True))
        result = await asyncio.wait_for(owner.sample(), timeout=2.5)
        assert launched[0].terminate_calls >= 1 and launched[0].kill_calls >= 1
        assert launched[0].returncode is not None
        for row in result.values():
            unknown(row)
        await owner.aclose()
    asyncio.run(run())


def test_unconfirmed_reaping_prevents_relaunch_and_retains_unknowns(monkeypatch):
    """what_bug_this_catches: hidden still-live workers accumulating after each timeout."""
    async def run():
        owner, launched, clock = sampler(monkeypatch,
            lambda clock, index: Probe([], blocked=True, unreapable=True))
        result = await asyncio.wait_for(owner.sample(), timeout=2.5)
        for row in result.values():
            unknown(row)
        for _ in range(4):
            clock.now += 2
            result = await asyncio.wait_for(owner.sample(), timeout=2.5)
            for row in result.values():
                unknown(row)
        assert len(launched) == 1
        with pytest.raises((RuntimeError, ValueError), match='own_process_probe_cleanup_unconfirmed'):
            await asyncio.wait_for(owner.aclose(), timeout=2.5)
        assert launched[0].returncode is None  # retained unknown, not fabricated cleanup
    asyncio.run(run())


def test_launch_failure_is_unknown_and_does_not_leak_exception_text(monkeypatch):
    """what_bug_this_catches: spawn failure becoming zero/observed or exposing exception content."""
    from voicelab.server import metrics
    async def fail(pid):
        raise OSError('SYNTHETIC-EXCEPTION-SENTINEL')
    monkeypatch.setattr(metrics, '_launch_probe', fail)
    async def run():
        owner = metrics.OwnProcessSampler()
        result = await owner.sample()
        for row in result.values():
            unknown(row)
        assert 'SYNTHETIC-EXCEPTION-SENTINEL' not in json.dumps(result)
        await owner.aclose()
    asyncio.run(run())


def test_cancel_during_spawn_then_shutdown_reaps_late_helper(monkeypatch):
    """what_bug_this_catches: cancellation orphaning a worker that returns after app shutdown."""
    from voicelab.server import metrics
    async def run():
        gate, entered = asyncio.Event(), asyncio.Event()
        probe = Probe([], blocked=True)
        launches = []
        async def launch(pid):
            launches.append(pid)
            entered.set()
            await gate.wait()
            return probe
        monkeypatch.setattr(metrics, '_launch_probe', launch)
        owner = metrics.OwnProcessSampler()
        request = asyncio.create_task(owner.sample())
        await asyncio.wait_for(entered.wait(), timeout=2.5)
        request.cancel()
        try:
            await request
        except asyncio.CancelledError:
            pass
        close = asyncio.create_task(owner.aclose())
        gate.set()
        await asyncio.wait_for(close, timeout=2.5)
        for _ in range(10):
            await asyncio.sleep(0)
        assert probe.returncode is not None
        assert len(launches) == 1
        for row in (await owner.sample()).values():
            unknown(row)
        assert len(launches) == 1
    asyncio.run(run())


def test_http_alias_security_cache_and_application_shutdown(monkeypatch, tmp_path):
    """what_bug_this_catches: bypassed HTTP protections or a sampler detached from app lifetime."""
    from fastapi.testclient import TestClient
    from voicelab.server import metrics
    from voicelab.server.app import Settings, create_app
    probes = []
    async def launch(pid):
        probe = Probe([line(name, value, time.monotonic_ns())
                       for name, value in zip(NAMES, (1000, 2, 3))])
        probes.append(probe)
        return probe
    monkeypatch.setattr(metrics, '_launch_probe', launch)
    origin = 'http://127.0.0.1:22341'
    nonce = 'synthetic-local-control-nonce-001'
    app = create_app(Settings(nonce=nonce, allowed_origin=origin, artifact_root=tmp_path / 'runtime'))
    headers = {'Origin': origin, 'X-Voicelab-Nonce': nonce}
    owner = app.state.process_metrics
    close_events = watch_close(monkeypatch, owner)
    with TestClient(app, base_url=origin) as client:
        assert client.get('/diagnostics').status_code == 403
        assert probes == []
        get = client.get('/diagnostics', headers=headers)
        post = client.post('/diagnostics', headers=headers, json={})
        assert get.status_code == post.status_code == 200
        for name in NAMES:
            assert get.json()['metrics'][name] == post.json()['metrics'][name]
        assert get.json()['scope'] == 'own_server_process_only'
        assert app.state.process_metrics is not None
        refused = client.post('/diagnostics', headers=headers, json={'unexpected': True})
        assert refused.status_code == 400
        assert refused.json()['error']['code'] == 'evidence_read_empty_object_required'
        assert len(probes) == 1
        assert close_events == []
    assert all(probe.returncode is not None for probe in probes)
    assert_sampler_shutdown(owner, close_events, probes)


@pytest.mark.parametrize('noop_witness', [False, True])
def test_shutdown_oracle_rejects_omitted_or_noop_sampler_close(monkeypatch, tmp_path, noop_witness):
    """what_bug_this_catches: naturally reaped probes hiding skipped/no-op lifespan cleanup."""
    from fastapi.testclient import TestClient
    from voicelab.server import metrics
    from voicelab.server.app import Settings, create_app
    probes = []

    async def launch(pid):
        probe = Probe([line(name, value, time.monotonic_ns())
                       for name, value in zip(NAMES, (1000, 2, 3))])
        probes.append(probe)
        return probe

    monkeypatch.setattr(metrics, '_launch_probe', launch)
    origin = 'http://127.0.0.1:22341'
    nonce = 'synthetic-local-control-nonce-001'
    app = create_app(Settings(nonce=nonce, allowed_origin=origin, artifact_root=tmp_path / 'runtime'))
    owner = app.state.process_metrics

    async def deliberately_noop_close():
        pass

    monkeypatch.setattr(owner, 'aclose', deliberately_noop_close)
    events = watch_close(monkeypatch, owner) if noop_witness else []
    with TestClient(app, base_url=origin) as client:
        response = client.get('/diagnostics', headers={'Origin': origin, 'X-Voicelab-Nonce': nonce})
        assert response.status_code == 200
        assert response.json()['metrics']['rss_bytes']['value'] == 1000
    assert probes and all(probe.returncode is not None for probe in probes)
    if noop_witness:
        assert events == ['entered', 'completed']  # call completion alone is insufficient
        with pytest.raises(AssertionError):
            assert_sampler_shutdown(owner, events, probes)
    else:
        with pytest.raises(AssertionError, match='sampler shutdown incomplete'):
            assert_sampler_shutdown(owner, events, probes)


def test_application_shutdown_owns_helper_returning_during_close(monkeypatch, tmp_path):
    """what_bug_this_catches: lifespan failing to await cleanup of a late pending launch."""
    from fastapi.testclient import TestClient
    from voicelab.server import metrics
    from voicelab.server.app import Settings, create_app
    launch_entered, close_entered, gate = asyncio.Event(), asyncio.Event(), asyncio.Event()
    probe = Probe([], blocked=True)
    launches = []
    returned_after_close = []

    async def launch(pid):
        launches.append(pid)
        launch_entered.set()
        await gate.wait()
        returned_after_close.append(close_entered.is_set())
        return probe

    monkeypatch.setattr(metrics, '_launch_probe', launch)
    app = create_app(Settings(nonce='synthetic-local-control-nonce-001',
        allowed_origin='http://127.0.0.1:22341', artifact_root=tmp_path / 'runtime'))
    owner = app.state.process_metrics
    events = watch_close(monkeypatch, owner, close_entered)

    async def start_pending():
        request = asyncio.create_task(owner.sample())
        await asyncio.wait_for(launch_entered.wait(), timeout=2.5)

        async def return_during_shutdown():
            await asyncio.wait_for(close_entered.wait(), timeout=2.5)
            await asyncio.sleep(.03)
            assert probe.returncode is None
            gate.set()

        return request, asyncio.create_task(return_during_shutdown())

    with TestClient(app) as client:
        request, releaser = client.portal.call(start_pending)
        assert len(launches) == 1 and not request.done()
        assert probe.returncode is None and returned_after_close == []
    assert returned_after_close == [True]
    assert request.done() and releaser.done() and releaser.exception() is None
    assert probe.terminate_calls + probe.kill_calls > 0
    assert probe.wait_calls > 0 and probe.returncode is not None
    assert_sampler_shutdown(owner, events, launches)


def test_application_shutdown_closes_relays_when_sampler_cleanup_unconfirmed(monkeypatch, tmp_path):
    """what_bug_this_catches: sampler shutdown failure skipping nested relay cleanup."""
    from fastapi.testclient import TestClient
    from voicelab.server import metrics
    from voicelab.server.app import Settings, create_app
    probes = []

    async def launch(pid):
        probe = Probe([], blocked=True, unreapable=True)
        probes.append(probe)
        return probe

    monkeypatch.setattr(metrics, '_launch_probe', launch)
    origin = 'http://127.0.0.1:22341'
    nonce = 'synthetic-local-control-nonce-001'
    app = create_app(Settings(nonce=nonce, allowed_origin=origin, artifact_root=tmp_path / 'runtime'))
    owner = app.state.process_metrics
    events = watch_close(monkeypatch, owner)
    relay_calls = []

    class InMemoryRelay:
        async def shutdown(self, reason, *, outcome):
            assert events == ['entered', 'failed']
            await asyncio.sleep(0)  # completion, not merely invocation, is witnessed
            relay_calls.append((reason, outcome))

    # The app has no public relay injection hook. Access only its lifespan-owned
    # registry to insert a synthetic relay; no websocket/provider/device is used.
    sessions = inspect.getclosurevars(app.router.lifespan_context.__wrapped__).nonlocals['sessions']
    sessions['synthetic-relay'] = InMemoryRelay()
    with pytest.raises(RuntimeError, match='own_process_probe_cleanup_unconfirmed'):
        with TestClient(app, base_url=origin) as client:
            response = client.get('/diagnostics', headers={'Origin': origin, 'X-Voicelab-Nonce': nonce})
            assert response.status_code == 200
            for row in response.json()['metrics'].values():
                unknown(row)
            assert probes[0].returncode is None  # known unconfirmed double, not fake reaping
    assert events == ['entered', 'failed']
    assert relay_calls == [('service_restart', 'service_restart')]
    assert len(probes) == 1 and probes[0].returncode is None
    assert probes[0].terminate_calls >= 1 and probes[0].kill_calls >= 1
