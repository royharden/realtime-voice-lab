"""Independent sampler controls for the declared D10 diagnostics-r2 interface.

All probe processes/streams below are explicit in-memory doubles. No OS metadata
reader, child process, listener, credential or real device is started by these tests.
Production HTTP aliases use an in-process ASGI client only.
"""
from __future__ import annotations

import asyncio
from collections import deque
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
        self.terminate_calls = self.kill_calls = 0

    async def readline(self):
        await asyncio.sleep(0)
        if self.rows:
            return self.rows.popleft()
        if self.blocked and self.returncode is None:
            await self.done.wait()
        else:
            self.returncode = 0
            self.done.set()
        return b''

    async def wait(self):
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
        task = asyncio.create_task(owner.sample())
        ticks = 0
        while not task.done():
            await asyncio.sleep(.01)
            ticks += 1
            assert ticks < 200, 'bounded collection failed to complete'
        result = await task
        assert ticks > 0
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
    assert all(probe.returncode is not None for probe in probes)
