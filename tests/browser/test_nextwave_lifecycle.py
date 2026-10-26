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


# Observe the actual worklet MessagePort before the application assigns its handler.
# No PCM, task result, or synthetic render verdict is supplied by this observer.
WORKLET_OBSERVER = r'''
;(()=>{
  const Node=window.AudioWorkletNode, events=[];
  let overflow=false;
  window.AudioWorkletNode=class extends Node {
    constructor(...args){
      super(...args);
      const graph=args[2]?.processorOptions?.graphId;
      this.port.addEventListener('message',({data})=>{
        if(!['rendered','drained','fence_applied','stale_audio_discarded'].includes(data?.type))return;
        if(events.length>=2000){overflow=true;return;}
        events.push({type:data.type,graph,responseId:data.responseId??null,
          epoch:data.epoch,connection:data.connection,frame:data.frame,
          consumedFrames:data.consumedFrames??0});
      });
      this.port.start();
    }
  };
  Object.defineProperty(window,'__nextwaveWorkletWitness',{value:()=>({events:[...events],overflow})});
})();'''


def assert_no_replayed_audio(old_responses, events, connection):
    """Only actual worklet consumption is a render witness; an ACK is insufficient."""
    rendered = [event for event in events if event['type'] == 'rendered']
    assert rendered, 'fresh post-recovery worklet rendering must be observed'
    for event in rendered:
        assert event['consumedFrames'] > 0, event
        assert event['responseId'] not in old_responses, 'pre-recovery audio rendered: ' + str(event)
        assert event['connection'] == connection, 'pre-recovery connection rendered: ' + str(event)


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


def accepted_task(state):
    """A finished mock response must contain a task outcome, not a lexical miss."""
    assert not state['error'], state
    task = json.loads(state['task'])
    assert isinstance(task, dict) and task, state
    assert not task.get('lexical_miss'), task
    assert task.get('ok') is True, task
    return task


@pytest.mark.parametrize('arm', ['V', 'C'])
def test_device_free_typed_mock_booking_and_end(serve, arm):
    """what_bug_this_catches: mock Start requests a microphone or cannot finish a booking
    without physical capture; End hides resources behind a closed label."""
    async def scenario(server):
        async with ui(server, arm, device_free=True) as (page, seen):
            for text in ('what are your hours', 'list slots', 'select slot 1', 'yes confirm',
                         'status'):
                before = (await snapshot(page))['driver']['responseDone']
                state = await complete_turn(page, text)
                assert state['driver']['responseDone'] > before, state
                task = accepted_task(state)
                if text == 'yes confirm':
                    assert task.get('effect_committed') is True, task
                    assert task['state'] == 'booked', task
            assert task['state'] == 'complete', task
            assert task.get('booking') or task.get('task', {}).get('booking'), task
            await released(page, seen)

    with serve() as server:
        asyncio.run(scenario(server))


@pytest.mark.parametrize('arm', ['V', 'C'])
def test_device_free_explicit_recovery_preserves_booking(serve, arm):
    """what_bug_this_catches: reconnect starts a different task, forgets a committed
    booking, replays old audio, or leaves the prior connection/resources alive."""
    async def scenario(server):
        async with ui(server, arm, device_free=True, observer_script=WORKLET_OBSERVER) as (page, seen):
            for text in ('list slots', 'select slot 1', 'yes confirm'):
                state = await complete_turn(page, text)
                task = accepted_task(state)
            assert task.get('effect_committed') is True and task['state'] == 'booked', task
            booking = task.get('booking') or task.get('task', {}).get('booking')
            assert booking, task
            before = state['driver']
            # A consumed and drained response establishes real prior audio to guard.
            prior_response = before['lastCompletion']['response_id']
            await page.wait_for_function('''(response)=>window.__nextwaveWorkletWitness().events.some(
              e=>e.type==='drained'&&e.responseId===response)''', arg=prior_response)
            prior = await page.evaluate('()=>window.__nextwaveWorkletWitness()')
            assert not prior['overflow'], prior
            old_responses = {event['responseId'] for event in prior['events']
                             if event['type'] == 'rendered' and event['consumedFrames'] > 0}
            assert prior_response in old_responses, prior
            marker = len(prior['events'])
            await page.locator('#disconnect').click()
            await wait_state(page, ['disconnected'])
            await page.locator('#retry').click()
            await wait_state(page, ['listening'], timeout=4500)
            state = await snapshot(page)
            assert state['driver']['resumeCount'] > before['resumeCount']
            assert state['driver']['openSockets'] == 1
            old_connection = before['lastFence']['connection']
            await page.wait_for_function('''(old)=>window.__nextwaveWorkletWitness().events.some(
              e=>e.type==='fence_applied'&&e.connection>old)''', arg=old_connection)
            connection = (await snapshot(page))['driver']['lastFence']['connection']
            assert connection > old_connection
            state = await complete_turn(page, 'status')
            task = accepted_task(state)
            assert task['state'] == 'complete', task
            assert (task.get('booking') or task.get('task', {}).get('booking')) == booking, task
            fresh_response = state['driver']['lastCompletion']['response_id']
            assert fresh_response not in old_responses
            await page.wait_for_function('''(response)=>window.__nextwaveWorkletWitness().events.some(
              e=>e.type==='drained'&&e.responseId===response)''', arg=fresh_response)
            after = await page.evaluate('()=>window.__nextwaveWorkletWitness()')
            assert not after['overflow'], after
            observed = after['events'][marker:]
            assert_no_replayed_audio(old_responses, observed, connection)
            # Synthetic oracle countercontrols only: copies never alter the real witness.
            # Relabeling replay with a fresh connection must still fail on response identity.
            fresh_render = next(event for event in observed if event['type'] == 'rendered')
            with pytest.raises(AssertionError, match='pre-recovery audio rendered'):
                assert_no_replayed_audio(old_responses,
                    [*observed, {**fresh_render, 'responseId': prior_response}], connection)
            with pytest.raises(AssertionError, match='pre-recovery connection rendered'):
                assert_no_replayed_audio(old_responses,
                    [*observed, {**fresh_render, 'connection': old_connection}], connection)
            await released(page, seen)

    with serve() as server:
        asyncio.run(scenario(server))


@pytest.mark.parametrize('arm', ['V', 'C'])
def test_device_free_interrupt_preserves_commit_and_fences_stale_audio(serve, arm):
    """what_bug_this_catches: either mock arm rolls back a committed effect on interrupt
    or renders an old epoch after the actual worklet has applied a new fence."""
    async def scenario(server):
        async with ui(server, arm, device_free=True) as (page, seen):
            accepted_task(await complete_turn(page, 'list slots'))
            accepted_task(await complete_turn(page, 'select slot 1'))
            await _listening(page)
            baseline = await snapshot(page)
            state = await complete_turn(page, 'yes confirm', cancel_after_commit=True)
            assert state['driver']['cancelTriggered'] > baseline['driver']['cancelTriggered']
            await page.wait_for_function('''()=>{try{
              return !('effect_committed' in JSON.parse(document.querySelector('#task').textContent));
            }catch(error){return false;}}''', timeout=4000)
            state = await snapshot(page)
            assert not state['error'], state
            task = json.loads(state['task'])
            assert not task.get('lexical_miss'), task
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
