"""DIA-SRC-01: caller cancellation cannot cancel the sole late-helper cleanup owner.

Uses the independently reviewed diagnostic Probe double; no OS helper or network.
The second explicit close in teardown only recovers a failing fake, never satisfies
the autonomous-cleanup oracle. Both default and accelerated collection are tested.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest

from test_nextwave_diagnostics import Probe


@pytest.mark.parametrize('collection', ['production', 'accelerated'])
def test_cancelled_aclose_retains_autonomous_late_helper_cleanup(monkeypatch, collection):
    """what_bug_this_catches: cancelled aclose cancelling shared reap before kill/wait."""
    from voicelab.server import metrics

    async def forbidden(*args, **kwargs):
        pytest.fail('no actual OS helper may launch in this regression')
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', forbidden)
    reap_budget = metrics.REAP_TIMEOUT
    assert reap_budget == .2  # production cleanup budget is never shortened
    if collection == 'accelerated':
        monkeypatch.setattr(metrics, 'PROBE_TIMEOUT', .03)
    else:
        assert metrics.PROBE_TIMEOUT == 1.0

    async def run():
        gate, terminated, reaped = asyncio.Event(), asyncio.Event(), asyncio.Event()
        launches = []

        class LateProbe(Probe):
            def terminate(self):
                super().terminate()
                terminated.set()

            async def wait(self):
                result = await super().wait()
                reaped.set()
                return result

        probe = LateProbe([], blocked=True, ignore_terminate=True)
        async def launch(pid):
            launches.append(pid)
            await gate.wait()
            return probe
        monkeypatch.setattr(metrics, '_launch_probe', launch)
        owner = metrics.OwnProcessSampler()
        close = None
        try:
            initial = await asyncio.wait_for(owner.sample(), timeout=2.5)
            assert all(row['value'] is None and row['observation'] == 'unknown'
                       for row in initial.values())
            assert len(launches) == 1
            close = asyncio.create_task(owner.aclose())
            await asyncio.sleep(0)
            gate.set()
            await asyncio.wait_for(terminated.wait(), timeout=2.5)
            close.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(close), timeout=2.5)
            try:
                await asyncio.wait_for(reaped.wait(), timeout=2 * reap_budget + .5)
            except asyncio.TimeoutError:
                pytest.fail('caller cancellation stopped autonomous kill/reap ownership')
            assert probe.kill_calls >= 1 and probe.returncode is not None
            assert len(launches) == 1
            # Repeated close is idempotent after independently witnessed cleanup.
            await asyncio.wait_for(owner.aclose(), timeout=2.5)
            assert len(launches) == 1
        finally:
            gate.set()
            if close is not None and not close.done():
                close.cancel()
                with suppress(asyncio.CancelledError):
                    await close
            # This executes after failure capture and cannot turn the test green.
            with suppress(RuntimeError, ValueError, asyncio.TimeoutError):
                await asyncio.wait_for(owner.aclose(), timeout=2.5)

    asyncio.run(run())
