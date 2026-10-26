"""Independent controls for the selected no-probe alternative (not OS-helper repair).

Only this file is the new contract suite. Historical helper-failure tests remain
unchanged and are not imported here. HTTP runs directly in ASGI, with controlled
lifespan and no listener. Page/CDP and relay shutdown collaborators are inert doubles;
the sampler, protected routes, privacy projection and resource analyzer are real.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from copy import deepcopy
import inspect
import json
import multiprocessing.process
import os
import subprocess
import sys
import threading
from types import ModuleType, SimpleNamespace

import httpx
import pytest


UNITS = {'rss_bytes': 'bytes', 'open_sockets': 'count', 'open_files': 'count'}
DISABLED = 'own_process_metrics_disabled'
NONCE = 'no-probe-public-test-nonce-0123456789abcdef'
ORIGIN = 'http://127.0.0.1:22301'


def assert_disabled(rows):
    """Exact independent DTO oracle; unknown resources cannot become zero/success."""
    assert set(rows) == set(UNITS)
    for name, unit in UNITS.items():
        assert rows[name] == {'value': None, 'observation': 'unknown',
                              'unit': unit, 'reason': DISABLED}


def headers(**changes):
    result = {'origin': ORIGIN, 'x-voicelab-nonce': NONCE}
    result.update(changes)
    return {key: value for key, value in result.items() if value is not None}


@pytest.fixture
def no_resources(monkeypatch):
    """Install before importing/constructing a sampler, including rejected old code.

    All OS readers/process/thread/executor entrypoints fail before effects. Task
    creation is forbidden when the metrics module is on the stack; ASGI/test caller
    tasks remain available and are not miscounted as sampler-owned resources.
    Failures use pytest's BaseException so old broad `except Exception` cannot
    swallow an attempted probe and make the safety oracle green.
    """
    calls = []

    def forbidden(name):
        def stop(*args, **kwargs):
            calls.append(name)
            for value in args:
                if inspect.iscoroutine(value):
                    value.close()
            pytest.fail('forbidden resource operation: ' + name)
        return stop

    for name in ('Popen', 'run', 'call', 'check_call', 'check_output'):
        monkeypatch.setattr(subprocess, name, forbidden('subprocess.' + name))
    for name in ('system', 'popen', 'startfile', 'posix_spawn', 'posix_spawnp',
                 'spawnl', 'spawnle', 'spawnlp', 'spawnlpe', 'spawnv', 'spawnve',
                 'spawnvp', 'spawnvpe', 'fork', 'forkpty'):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, forbidden('os.' + name))
    for name in ('create_subprocess_exec', 'create_subprocess_shell', 'to_thread'):
        monkeypatch.setattr(asyncio, name, forbidden('asyncio.' + name))
    for name in ('subprocess_exec', 'subprocess_shell', 'run_in_executor'):
        monkeypatch.setattr(asyncio.BaseEventLoop, name, forbidden('loop.' + name))
    monkeypatch.setattr(threading.Thread, 'start', forbidden('Thread.start'))
    monkeypatch.setattr(multiprocessing.process.BaseProcess, 'start',
                        forbidden('Process.start'))
    monkeypatch.setattr(ThreadPoolExecutor, 'submit', forbidden('ThreadPoolExecutor.submit'))
    monkeypatch.setattr(ProcessPoolExecutor, 'submit', forbidden('ProcessPoolExecutor.submit'))

    # Optional psutil must never touch this host, even on an old HTTP baseline.
    psutil = ModuleType('psutil')
    psutil.Process = forbidden('psutil.Process')
    psutil.Error = type('InertPsutilError', (Exception,), {})
    for name in ('process_iter', 'net_connections', 'virtual_memory'):
        setattr(psutil, name, forbidden('psutil.' + name))
    monkeypatch.setitem(sys.modules, 'psutil', psutil)

    def metrics_caller():
        frame = inspect.currentframe()
        try:
            while frame is not None:
                if frame.f_globals.get('__name__') == 'voicelab.server.metrics':
                    return True
                frame = frame.f_back
            return False
        finally:
            del frame

    original_task = asyncio.tasks.Task

    class GuardedTask(original_task):
        def __init__(self, coro, *args, **kwargs):
            if metrics_caller():
                forbidden('metrics.Task')(coro)
            super().__init__(coro, *args, **kwargs)

    monkeypatch.setattr(asyncio, 'Task', GuardedTask)
    monkeypatch.setattr(asyncio.tasks, 'Task', GuardedTask)
    original_create = asyncio.BaseEventLoop.create_task

    def create_task(loop, coro, *args, **kwargs):
        if metrics_caller():
            forbidden('metrics.loop.create_task')(coro)
        return original_create(loop, coro, *args, **kwargs)

    monkeypatch.setattr(asyncio.BaseEventLoop, 'create_task', create_task)

    def sampler(**kwargs):
        from voicelab.server import metrics
        # Trap the old injection seam too; never replace sample/aclose behavior.
        for name in ('_launch_probe', '_worker'):
            if hasattr(metrics, name):
                monkeypatch.setattr(metrics, name, forbidden('metrics.' + name))
        return metrics.OwnProcessSampler(**kwargs)

    yield SimpleNamespace(calls=calls, sampler=sampler)
    assert calls == [], 'a forbidden attempt must never be treated as zero-resource success'


def test_actual_sampler_repeated_close_and_detached_rows(no_resources):
    """what_bug_this_catches: helper launch, cached observations or mutable shared DTOs."""
    async def run():
        def unused_clock():
            pytest.fail('compatibility clock must not enable or collect a metric')

        owner = no_resources.sampler(clock=unused_clock, clock_ns=unused_clock)
        first = await owner.sample()
        assert_disabled(first)
        first['rss_bytes']['value'] = 0
        first['rss_bytes']['reason'] = 'caller mutation'
        first['extra'] = {'value': 0}
        for _ in range(12):
            assert_disabled(await owner.sample())
        for _ in range(4):
            assert await owner.aclose() is None
            assert_disabled(await owner.sample())
        other = no_resources.sampler()
        assert_disabled(await other.sample())
        await other.aclose()
    asyncio.run(run())


def test_actual_sampler_concurrent_and_cancelled_callers_keep_zero_resources(no_resources):
    """what_bug_this_catches: hidden per-caller tasks/helpers or cancellation-only activation.

    No-probe methods may finish without suspension. Cancel pending callers before
    entry, and cancel an entered caller at its next checkpoint; neither requires
    inventing an artificial wait inside the production sampler.
    """
    async def run():
        owner = no_resources.sampler()
        before = set(asyncio.all_tasks())
        gate = asyncio.Event()

        async def pending(method):
            await gate.wait()
            return await method()

        cancelled = [asyncio.create_task(pending(method)) for method in
                     (owner.sample, owner.aclose, owner.sample, owner.aclose)]
        await asyncio.sleep(0)
        for task in cancelled:
            task.cancel()
        results = await asyncio.gather(*cancelled, return_exceptions=True)
        assert all(isinstance(result, asyncio.CancelledError) for result in results)
        rows = await asyncio.gather(*(owner.sample() for _ in range(24)))
        for row in rows:
            assert_disabled(row)

        async def entered_then_cancelled():
            assert_disabled(await owner.sample())
            asyncio.current_task().cancel()
            assert await owner.aclose() is None
            assert_disabled(await owner.sample())
            await asyncio.sleep(0)

        entered = asyncio.create_task(entered_then_cancelled())
        with pytest.raises(asyncio.CancelledError):
            await entered
        mixed = await asyncio.gather(*(owner.sample() if index % 2 else owner.aclose()
                                       for index in range(30)))
        for index, result in enumerate(mixed):
            if index % 2:
                assert_disabled(result)
            else:
                assert result is None
        assert_disabled(await owner.sample())
        await asyncio.sleep(0)
        assert set(asyncio.all_tasks()) == before, 'sampler left an owned task pending'
    asyncio.run(run())


def make_app(tmp_path):
    from voicelab.server.app import Settings, create_app
    return create_app(Settings(nonce=NONCE, allowed_origin=ORIGIN,
                               artifact_root=tmp_path / 'runtime'))


def test_protected_http_aliases_retain_disabled_reason_in_actual_projection(
        no_resources, tmp_path):
    """what_bug_this_catches: old default sampler or reason lost in closed producer projection."""
    from voicelab.contracts import validate_http_response
    from voicelab.harness.evidence import EvidenceSink
    from voicelab.harness.privacy import observed_metadata
    from voicelab.server import metrics

    async def run():
        app = make_app(tmp_path)
        assert isinstance(app.state.process_metrics, metrics.OwnProcessSampler)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url=ORIGIN) as client:
                bodies = []
                for method in ('GET', 'POST', 'GET', 'POST'):
                    response = await client.request(method, '/diagnostics', headers=headers(),
                                                    content=b'{}' if method == 'POST' else None)
                    assert response.status_code == 200
                    assert NONCE not in response.text
                    body = response.json()
                    validate_http_response('diagnostics', body)
                    assert body['scope'] == 'own_server_process_only'
                    assert body['admitted_sessions'] == 0 and body['boot_id']
                    assert_disabled({name: body['metrics'][name] for name in UNITS})
                    projected = observed_metadata(body)
                    assert_disabled({name: projected['metrics'][name] for name in UNITS})
                    assert {'asyncio_tasks', 'event_loop_lag_ms'} <= body['metrics'].keys()
                    for name, unit in (('asyncio_tasks', 'count'),
                                       ('event_loop_lag_ms', 'milliseconds')):
                        row = body['metrics'][name]
                        assert row['observation'] == 'observed' and row['unit'] == unit
                        assert type(row['value']) in (int, float) and row['value'] >= 0
                    bodies.append(body)
                assert len({body['boot_id'] for body in bodies}) == 1
                sink_root = tmp_path / 'no-probe-evidence'
                sink = EvidenceSink(sink_root, {'run_id': 'no-probe-contract',
                    'mode': 'mock', '_manifest_hash': '0' * 64})
                try:
                    sink.emit('server_observation', server_observations=bodies[0])
                    sink.artifact('diagnostics.json', bodies[0], 'server_diagnostics')
                finally:
                    sink.close(completed=False, reason='incomplete_observation_coverage')
                saved = json.loads((sink_root / 'diagnostics.json').read_text())
                assert_disabled({name: saved['metrics'][name] for name in UNITS})
                saved_row = json.loads((sink_root / 'driver.jsonl').read_text().splitlines()[0])
                assert_disabled({name: saved_row['server_observations']['metrics'][name]
                                 for name in UNITS})
                assert observed_metadata({'reason': 'unapproved reason text'}) == {}
                assert observed_metadata({'unexpected_key': DISABLED}) == {}
        assert_disabled(await app.state.process_metrics.sample())
    asyncio.run(run())


@pytest.mark.parametrize('method', ['GET', 'POST'])
def test_http_boundary_and_post_body_cannot_enable_probe(
        no_resources, monkeypatch, tmp_path, method):
    """what_bug_this_catches: probe option/body accepted or unauthorized requests sampling."""
    from voicelab.server import metrics
    original = metrics.OwnProcessSampler.sample
    samples = []

    async def counted(owner):
        samples.append(owner)
        return await original(owner)

    monkeypatch.setattr(metrics.OwnProcessSampler, 'sample', counted)

    async def run():
        app = make_app(tmp_path)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url=ORIGIN) as client:
                refused = [(ORIGIN, headers(origin=None)),
                           (ORIGIN, headers(origin='http://foreign.example')),
                           (ORIGIN, headers(**{'x-voicelab-nonce': None})),
                           (ORIGIN, headers(**{'x-voicelab-nonce': 'wrong-public-nonce-123456'})),
                           ('http://attacker.example', headers())]
                for base, boundary in refused:
                    response = await client.request(method, base + '/diagnostics',
                        headers=boundary, content=b'{}' if method == 'POST' else None)
                    assert response.status_code == 403
                    assert response.json()['error']['code'] == 'boundary_refused'
                    assert NONCE not in response.text
                if method == 'POST':
                    for body, status in ((b'', 400), (b'{ }', 400), (b'[]', 400),
                                         (b'{"probe":true}', 400),
                                         (b'{"metrics_enabled":true}', 400), (b' ' * 33, 413)):
                        response = await client.post('/diagnostics', headers=headers(), content=body)
                        assert response.status_code == status
                assert samples == [], 'refused input reached the sampler'
                # Query parameters have no activation authority on either alias.
                response = await client.request(method, '/diagnostics?probe=true&metrics_enabled=true',
                    headers=headers(), content=b'{}' if method == 'POST' else None)
                assert response.status_code == 200
                assert_disabled({name: response.json()['metrics'][name] for name in UNITS})
                assert samples == [app.state.process_metrics]
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['none', 'body', 'relay', 'sampler'])
def test_real_sampler_lifespan_close_and_relay_finally_survive_failure(
        no_resources, monkeypatch, tmp_path, failure):
    """what_bug_this_catches: lifespan exit skipping sampler close or relay finally on error."""
    from voicelab.server import metrics
    original = metrics.OwnProcessSampler.aclose
    events = []

    async def close(owner):
        events.append(('sampler', owner))
        await original(owner)
        if failure == 'sampler':
            raise RuntimeError('synthetic-sampler-close-failure')

    monkeypatch.setattr(metrics.OwnProcessSampler, 'aclose', close)

    async def shutdown(reason, **kwargs):
        events.append(('relay', reason, kwargs))
        if failure == 'relay':
            raise RuntimeError('synthetic-relay-close-failure')

    async def run():
        app = make_app(tmp_path)
        owner = app.state.process_metrics
        # Only inject an inert shutdown collaborator into the real route closure.
        # No WebSocket, admitted session, adapter or provider is started.
        endpoint = next(route.endpoint for route in app.routes if route.path == '/ws')
        sessions = inspect.getclosurevars(endpoint).nonlocals['sessions']
        sessions['synthetic-lifecycle-only'] = SimpleNamespace(shutdown=shutdown)
        try:
            async with app.router.lifespan_context(app):
                assert_disabled(await owner.sample())
                if failure == 'body':
                    raise RuntimeError('synthetic-body-failure')
        except RuntimeError as error:
            assert failure != 'none'
            assert str(error) == 'synthetic-' + failure + ('-failure' if failure == 'body'
                                                         else '-close-failure')
        else:
            assert failure == 'none'
        assert [event for event in events if event[0] == 'sampler'] == [('sampler', owner)]
        assert [event for event in events if event[0] == 'relay'] == [
            ('relay', 'service_restart', {'outcome': 'service_restart'})]
        assert_disabled(await owner.sample())
    asyncio.run(run())


def test_actual_resource_consumer_keeps_no_probe_series_incomplete(
        no_resources, monkeypatch, tmp_path):
    """what_bug_this_catches: unknown process DTOs converted to a passing resource/soak gate."""
    from voicelab.harness import observations
    from voicelab.harness.privacy import observed_metadata
    from voicelab.reporting.diagnostics import soak_report

    async def run():
        app = make_app(tmp_path)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url=ORIGIN) as client:
                response = await client.post('/diagnostics', content=b'{}', headers=headers())
                assert response.status_code == 200
                server = observed_metadata(response.json())
                assert_disabled({name: server['metrics'][name] for name in UNITS})

        state = {'schema_version': 1, 'owned_handles': {'socket': 0, 'graph': 0},
                 'observation_statuses': {'owned_handles': 'observed_api_state',
                    'cleanup': 'observed_released', 'playback_queue': 'observed_empty',
                    'upload_queue': 'observed_empty'},
                 'queue_seconds': {'playback': 0, 'upload': 0}}

        async def evaluate(*args):
            return deepcopy(state)

        async def send(*args):
            return {'metrics': [{'name': 'JSHeapUsedSize', 'value': 1000}]}

        async def inline(function, *args, **kwargs):
            # The real observer's HTTP seam receives already captured actual ASGI
            # bytes. Execute the seam inline: no executor, thread or real network.
            assert function is observations.own_diagnostic
            return function(*args, **kwargs)

        monkeypatch.setattr(observations, 'own_diagnostic', lambda *args, **kwargs: deepcopy(server))
        monkeypatch.setattr(asyncio, 'to_thread', inline)
        rows = []
        for index in range(5):
            row = await observations.resource_sample(
                [SimpleNamespace(evaluate=evaluate) for _ in range(3)],
                [SimpleNamespace(send=send) for _ in range(3)], {}, NONCE,
                after_warmup=True, sample_rates=[48000] * 3)
            row['monotonic_seconds'] = float(index)
            assert all(row[field] is None for field in ('rss_bytes', 'sockets', 'files'))
            assert row['browser_heap_bytes'] == 1000 and row['owned_handles'] == 0
            assert row['queue_seconds'] == 0 and row['worklet_queue_seconds'] == 0
            rows.append(row)
        summary = {'aggregate_turns': 200, 'interruptions': 40, 'reconnects': 10,
                   'contexts': 3, 'fourth_session_refused': True,
                   'coverage_actions_are_attempts': False}
        report = soak_report(rows, summary)
        assert report['coverage_complete'] is True
        assert report['resource_gate_pass'] is False and report['passed'] is False
        assert 'resource series incomplete; missing instrumentation remains unknown' in report['failures']
        # Positive countercontrol: the same complete synthetic coverage/series can
        # pass when all three missing resource values are actually supplied.
        complete = deepcopy(rows)
        for row in complete:
            row.update(rss_bytes=1000, sockets=0, files=0)
        positive = soak_report(complete, summary)
        assert positive['resource_gate_pass'] is True and positive['passed'] is True
    asyncio.run(run())
