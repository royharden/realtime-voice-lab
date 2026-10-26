"""Actual built UI / real local relay; typed mock, no physical devices or speech.

Faults are synthetic browser-boundary injections. These tests do not establish
physical audio quality, provider performance, soak readiness, or W7/W8 acceptance.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from test_ui_journey import ui, _end_and_wait_closed, _listening
from voicelab.harness.browser import snapshot
from voicelab.harness.journey import complete_turn, wait_state


@pytest.fixture(autouse=True)
def forbid_live_provider(monkeypatch):
    """Fail before provider construction/network, even if mock routing regresses."""
    from voicelab.adapters.live import LiveArmSession
    from voicelab.adapters.transport import ProviderTransport
    attempts = []

    def forbidden(*args, **kwargs):
        attempts.append('provider_or_live_session')
        raise AssertionError('Live/provider entry forbidden in mock browser acceptance')

    monkeypatch.setattr(LiveArmSession, '__init__', forbidden)
    monkeypatch.setattr(ProviderTransport, 'connect', forbidden)
    monkeypatch.setattr(ProviderTransport, 'speech', forbidden)
    yield
    assert attempts == []


async def released(page, seen):
    """Check actual ownership counters after terminal cleanup, not just the state label."""
    await _end_and_wait_closed(page)
    state = await snapshot(page)
    handles = state['app']['owned_handles']
    assert handles and all(value == 0 for value in handles.values()), handles
    assert state['driver']['liveTracks'] == 0
    assert state['driver']['openSockets'] == 0
    assert await page.evaluate('()=>window.__nextwaveDeviceCalls') == []
    assert seen['pageerrors'] == []
    assert seen['blocked'] == []
    # A repeated End must remain closed, without acquiring a replacement session.
    await page.locator('#end').click()
    assert await page.locator('#state').inner_text() == 'closed'
    assert all(value == 0 for value in (await snapshot(page))['app']['owned_handles'].values())


@pytest.mark.parametrize('arm', ['V', 'C'])
def test_device_free_typed_mock_booking_and_end(serve, arm):
    """what_bug_this_catches: mock Start requests a microphone or cannot finish a booking
    without physical capture; End hides resources behind a closed label."""
    async def scenario(server):
        async with ui(server, arm, device_free=True) as (page, seen):
            for text in ('what are your hours', 'list slots', 'select slot 1', 'yes confirm',
                         'booking status'):
                before = (await snapshot(page))['driver']['responseDone']
                state = await complete_turn(page, text)
                assert state['driver']['responseDone'] > before, state
                assert not state['error'], state
            task = json.loads(state['task'])
            assert task.get('booking') or task.get('task', {}).get('booking'), task
            await released(page, seen)

    with serve() as server:
        asyncio.run(scenario(server))


@pytest.mark.parametrize('arm', ['V', 'C'])
def test_device_free_explicit_recovery_preserves_booking(serve, arm):
    """what_bug_this_catches: reconnect starts a different task, forgets a committed
    booking, replays old audio, or leaves the prior connection/resources alive."""
    async def scenario(server):
        async with ui(server, arm, device_free=True) as (page, seen):
            for text in ('list slots', 'select slot 1', 'yes confirm'):
                state = await complete_turn(page, text)
                assert not state['error'], state
            task = json.loads(state['task'])
            booking = task.get('booking') or task.get('task', {}).get('booking')
            assert booking, task
            before = state['driver']
            await page.locator('#disconnect').click()
            await wait_state(page, ['disconnected'])
            await page.locator('#retry').click()
            await wait_state(page, ['listening'], timeout=4500)
            state = await snapshot(page)
            assert state['driver']['resumeCount'] > before['resumeCount']
            assert state['driver']['openSockets'] == 1
            state = await complete_turn(page, 'booking status')
            task = json.loads(state['task'])
            assert (task.get('booking') or task.get('task', {}).get('booking')) == booking, task
            await released(page, seen)

    with serve() as server:
        asyncio.run(scenario(server))


@pytest.mark.parametrize('arm', ['V', 'C'])
def test_device_free_interrupt_preserves_commit_and_fences_stale_audio(serve, arm):
    """what_bug_this_catches: either mock arm rolls back a committed effect on interrupt
    or renders an old epoch after the actual worklet has applied a new fence."""
    async def scenario(server):
        async with ui(server, arm, device_free=True) as (page, seen):
            await complete_turn(page, 'list slots')
            await complete_turn(page, 'select slot 1')
            await _listening(page)
            baseline = await snapshot(page)
            state = await complete_turn(page, 'yes confirm', cancel_after_commit=True)
            assert state['driver']['cancelTriggered'] > baseline['driver']['cancelTriggered']
            await page.wait_for_function('''()=>{try{
              return !('effect_committed' in JSON.parse(document.querySelector('#task').textContent));
            }catch(error){return false;}}''', timeout=4000)
            state = await snapshot(page)
            task = json.loads(state['task'])
            assert task.get('booking') or task.get('task', {}).get('booking'), task
            assert state['driver']['fenceCounter'] > baseline['driver']['fenceCounter']
            fence = state['driver']['lastFence']
            assert fence['epoch'] > (baseline['driver']['lastFence'] or {}).get('epoch', 0)
            prior = state['driver']['staleDiscards']
            await page.evaluate('()=>window.__voiceLabDriver.staleControl()')
            await page.wait_for_function('(prior)=>window.__voiceLabDriver.snapshot().staleDiscards>prior',
                                         arg=prior, timeout=2000)
            assert (await snapshot(page))['driver']['lastFence'] == fence
            await released(page, seen)

    with serve() as server:
        asyncio.run(scenario(server))


@pytest.mark.parametrize('arm', ['V', 'C'])
def test_device_free_protocol_fault_is_visible_and_releases(serve, arm):
    """what_bug_this_catches: malformed relay JSON silently disappears or leaves live
    browser resources after an explicit visible failure and End."""
    async def scenario(server):
        async with ui(server, arm, device_free=True) as (page, seen):
            await page.evaluate("()=>window.__voiceLabDriver.fault('malformed_protocol')")
            await wait_state(page, ['failed'])
            assert 'protocol_error' in await page.locator('#error').inner_text()
            await released(page, seen)

    with serve() as server:
        asyncio.run(scenario(server))
