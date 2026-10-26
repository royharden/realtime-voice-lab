"""Real web/ UI against the keyless mock relay (plan W3). NOT YET RUN.

Uses Playwright Chromium (validation extra; `playwright install chromium` is a later-phase
step) and the existing harness drivers: probe.js instrumentation, journey.wait_state /
complete_turn, and browser.snapshot. The trace export is read raw (the
harness trace_export returns a privacy projection, unsuitable for a leak check). The guarded
BrowserDriver itself is not used: it requires an activation spec with real reviewed-fixture
custody and read access that tests must not fabricate. Fake media devices only; no speech.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

import pytest

pytest.importorskip('playwright.async_api')
from playwright.async_api import async_playwright  # noqa: E402

import voicelab.harness  # noqa: E402
from voicelab.harness.browser import snapshot  # noqa: E402
from voicelab.harness.journey import complete_turn, wait_state  # noqa: E402

PROBE = Path(voicelab.harness.__file__).with_name('probe.js')
# Same fake-device flags as BrowserDriver, without a fixture file (no reviewed speech asset).
CHROMIUM_ARGS = ['--autoplay-policy=no-user-gesture-required', '--use-fake-ui-for-media-stream',
                 '--use-fake-device-for-media-stream']

# Setup actions keep their original Playwright budgets. Observation never retries an action,
# dispatches a handler, or changes the response/recovery/session deadlines in the application.
SETUP_ACTION_MS = 4000
SETUP_OBSERVATION_SECONDS = 0.75
SETUP_OBSERVER = r''';(()=>{
  const events=[];
  const describe=node=>node instanceof Element?{
    tag:node.tagName,id:node.id,arm:node.getAttribute('data-arm')}:null;
  const record=(kind,event)=>{
    if(events.length<80)events.push({kind,at:performance.now(),
      target:describe(event?.target),trusted:event?.isTrusted??null});
  };
  for(const kind of ['DOMContentLoaded','pointerdown','pointerup','mousedown','mouseup','click',
                     'input','change','visibilitychange'])
    document.addEventListener(kind,event=>record(kind,event),{capture:true,passive:true});
  window.addEventListener('load',event=>record('load',event),{once:true,passive:true});
  Object.defineProperty(window,'__nextwaveSetupEvents',{value:()=>[...events]});
})();'''

SETUP_SNAPSHOT = r'''selector=>{
  const read=fn=>{try{return fn();}catch(error){return {unavailable:String(error)};}};
  const describe=node=>node instanceof Element?{
    tag:node.tagName,id:node.id,arm:node.getAttribute('data-arm'),
    classes:node.getAttribute('class')}:null;
  const nodes=selector?[...document.querySelectorAll(selector)]:[];
  const node=nodes[0],rect=node?.getBoundingClientRect(),style=node&&getComputedStyle(node);
  const x=rect?Math.max(0,Math.min(innerWidth-1,rect.x+rect.width/2)):0;
  const y=rect?Math.max(0,Math.min(innerHeight-1,rect.y+rect.height/2)):0;
  const hits=rect?document.elementsFromPoint(x,y):[];
  const navigation=performance.getEntriesByType('navigation')[0];
  return {
    at:performance.now(),ready_state:document.readyState,visibility:document.visibilityState,
    focused:document.hasFocus(),active_element:describe(document.activeElement),
    dom_content_loaded:{start:navigation?.domContentLoadedEventStart??null,
      end:navigation?.domContentLoadedEventEnd??null},
    control:{selector,matches:nodes.length,node:describe(node),connected:node?.isConnected??null,
      // These are instantaneous DOM observations; Playwright's native action log is authoritative
      // for its stability/visibility/enabled/receives-events checks during the actual action.
      dom_visible:!!rect&&rect.width>0&&rect.height>0&&style.visibility!=='hidden'&&style.display!=='none',
      dom_enabled:node?!node.matches(':disabled')&&node.getAttribute('aria-disabled')!=='true':null,
      onclick_type:node?typeof node.onclick:null,
      rect:rect?{x:rect.x,y:rect.y,width:rect.width,height:rect.height}:null,
      style:style?{display:style.display,visibility:style.visibility,pointer_events:style.pointerEvents}:null,
      hit_point:rect?{x,y}:null,hit_stack:hits.slice(0,5).map(describe),
      center_receives_events:node&&hits[0]?node===hits[0]||node.contains(hits[0]):null},
    state:document.querySelector('#state')?.textContent??null,
    error:document.querySelector('#error')?.textContent??null,
    resources:document.querySelector('#resources')?.textContent??null,
    app:read(()=>window.__voiceLabDiagnostics?.snapshot?.()??null),
    connection:read(()=>{const driver=window.__voiceLabDriver?.snapshot?.();
      return driver?{openSockets:driver.openSockets,liveTracks:driver.liveTracks,
        resumeCount:driver.resumeCount,lastFence:driver.lastFence}:null;}),
    device_calls:window.__nextwaveDeviceCalls??[],events:window.__nextwaveSetupEvents?.()??[],
    resources_loaded:performance.getEntriesByType('resource').slice(-16).map(entry=>({
      name:entry.name.split(/[?#]/)[0],type:entry.initiatorType,start:entry.startTime,
      duration:entry.duration,response_end:entry.responseEnd}))
  };
}'''


async def _setup_snapshot(page, selector):
    """A stalled renderer must not prevent the original control failure being reported."""
    try:
        return await asyncio.wait_for(page.evaluate(SETUP_SNAPSHOT, selector),
                                      timeout=SETUP_OBSERVATION_SECONDS)
    except Exception as error:
        return {'unavailable': type(error).__name__ + ': ' + str(error),
                'page_closed': page.is_closed()}


class _SetupDiagnostics:
    def __init__(self, page):
        self.page, self.started, self.steps = page, time.monotonic(), []

    async def run(self, stage, selector, action, budget_ms):
        observing = time.monotonic()
        step = {'stage': stage, 'selector': selector, 'budget_ms': budget_ms,
                'before': await _setup_snapshot(self.page, selector)}
        step['observation_elapsed_ms'] = round((time.monotonic() - observing) * 1000, 3)
        self.steps.append(step)
        started = time.monotonic()
        step['started_ms'] = round((started - self.started) * 1000, 3)
        try:
            result = await action()
        except Exception as error:
            step['result'] = 'failed'
            # Playwright includes its complete native actionability/click call log here.
            step['error'] = type(error).__name__ + ': ' + str(error)
            raise
        else:
            step['result'] = 'completed'
            return result
        finally:
            step['elapsed_ms'] = round((time.monotonic() - started) * 1000, 3)


@asynccontextmanager
async def ui(server: dict, arm: str = 'V', *, device_free: bool = False,
             observer_script: str = ''):
    """Open the real UI, enter relay URL + nonce, pick mock/arm, start, await listening."""
    blocked, console, requests, pageerrors = [], [], [], []
    setup_failure, setup_error = None, None
    allowed = {server['page_origin'], server['relay_origin']}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True, args=CHROMIUM_ARGS + ['--mute-audio', '--use-file-for-fake-audio-capture=' + server['capture_wav']])
        try:
            context = await browser.new_context(permissions=[] if device_free else ['microphone'])
            context.set_default_timeout(SETUP_ACTION_MS)

            async def route(request):
                parsed = urlsplit(request.request.url)
                if f'{parsed.scheme}://{parsed.netloc}' in allowed:
                    await request.continue_()
                else:
                    blocked.append(request.request.url)
                    await request.abort('blockedbyclient')
            await context.route('**/*', route)
            if device_free:
                # Registered before probe.js: even its bound media method must hit this guard.
                guard = '''(()=>{
                  window.__nextwaveDeviceCalls=[];
                  for(const name of ['getUserMedia','enumerateDevices']){
                    navigator.mediaDevices[name]=async()=>{
                      window.__nextwaveDeviceCalls.push(name);
                      throw new DOMException('Device access forbidden in typed mock','NotAllowedError');
                    };
                  }
                })();'''
                # One script gives a defined guard-before-probe ordering.
                await context.add_init_script(script=guard + PROBE.read_text(encoding='utf-8') +
                                              observer_script + SETUP_OBSERVER)
            else:
                await context.add_init_script(script=PROBE.read_text(encoding='utf-8') +
                                              observer_script + SETUP_OBSERVER)
            page = await context.new_page()
            setup = _SetupDiagnostics(page)
            browser_events = []

            def observe(kind, detail):
                if len(browser_events) < 40:
                    browser_events.append({'kind': kind, 'detail': detail,
                        'elapsed_ms': round((time.monotonic() - setup.started) * 1000, 3)})

            page.on('console', lambda message: console.append(message.text))
            page.on('pageerror', lambda error: pageerrors.append(str(error)))
            page.on('request', lambda request: requests.append((request.method, request.url)))
            page.on('console', lambda message: observe('console', message.text))
            page.on('pageerror', lambda error: observe('pageerror', str(error)))
            page.on('requestfailed', lambda request: observe('requestfailed', {
                'url': request.url, 'failure': request.failure}))
            try:
                await setup.run('navigate_domcontentloaded', None,
                    lambda: page.goto(server['page_origin'] + '/', wait_until='domcontentloaded',
                                      timeout=10000), 10000)
                await setup.run('fill_relay_url', '#url',
                    lambda: page.locator('#url').fill(server['relay_url']), SETUP_ACTION_MS)
                await setup.run('fill_nonce', '#nonce',
                    lambda: page.locator('#nonce').fill(server['nonce']), SETUP_ACTION_MS)
                await setup.run('select_mock_mode', '#mode',
                    lambda: page.locator('#mode').select_option('mock'), SETUP_ACTION_MS)
                arm_selector = f'[data-arm="{arm}"]'
                await setup.run('select_arm_' + arm, arm_selector,
                    lambda: page.locator(arm_selector).click(), SETUP_ACTION_MS)
                # Preserve the existing panel expansion; the arm/Start interactions stay native.
                await setup.run('open_stimulus_panel', 'details:has(#stimulus)',
                    lambda: page.locator('details:has(#stimulus)').evaluate(
                        '(element)=>element.open=true'), SETUP_ACTION_MS)
                await setup.run('click_start', '#start',
                    lambda: page.locator('#start').click(), SETUP_ACTION_MS)

                async def listening():
                    states = ['listening', 'permission_denied', 'unsupported_audio', 'failed'] if device_free else ['listening']
                    await wait_state(page, states, timeout=8000)
                    assert await page.locator('#state').inner_text() == 'listening'

                await setup.run('await_listening', '#state', listening, 8000)
            except Exception as error:
                setup_error = error
                setup_failure = {'steps': setup.steps, 'failed_stage': setup.steps[-1]['stage'],
                    'failure': type(error).__name__ + ': ' + str(error),
                    'after_failure': await _setup_snapshot(page, setup.steps[-1]['selector']),
                    'pageerrors': pageerrors, 'console': console[-10:],
                    'browser_events': browser_events, 'requests': requests[-16:], 'blocked': blocked,
                    'observation_budget_ms': SETUP_OBSERVATION_SECONDS * 1000}
            else:
                yield page, {'blocked': blocked, 'console': console, 'requests': requests,
                             'pageerrors': pageerrors, 'setup_steps': setup.steps}
        finally:
            if setup_failure is not None:
                setup_failure['cleanup'] = {
                    'before_close': await _setup_snapshot(page, None),
                    'browser_connected_before': browser.is_connected(),
                    'context_pages_before': len(context.pages)}
            try:
                await browser.close()
            except Exception as error:
                if setup_failure is None:
                    raise
                setup_failure['cleanup']['close_error'] = type(error).__name__ + ': ' + str(error)
            finally:
                if setup_failure is not None:
                    setup_failure['cleanup']['browser_connected_after'] = browser.is_connected()
                    setup_failure['cleanup']['page_closed_after'] = page.is_closed()
        if setup_error is not None:
            # Do not chain a raw fill/Playwright exception: its call log may include the nonce.
            raise AssertionError('UI setup diagnostic: ' +
                json.dumps(setup_failure).replace(server['nonce'], '[redacted]')) from None


async def _raw_trace_download(page) -> str:
    """The app's own #download export as raw text (harness trace_export returns a projection,
    which would hide a leaked nonce)."""
    async with page.expect_download(timeout=4000) as pending:
        await page.locator('#download').click()
    download = await pending.value
    path = await download.path()
    assert path is not None
    return Path(path).read_text(encoding='utf-8')


async def _listening(page):
    """Reach listening explicitly (the UI parks in awaiting_response after a drained turn)."""
    if await page.locator('#state').inner_text() in {'playing', 'awaiting_response'}:
        await page.locator('#retry').click()
    await wait_state(page, ['listening'])


async def _end_and_wait_closed(page):
    await page.locator('#end').click()
    await wait_state(page, ['closed'], timeout=8000)


def test_boot_initializes_controls_without_pageerrors(serve):
    """what_bug_this_catches: null onclick/temporal initialization errors before Start is usable.
    A rendered page alone is insufficient: required handlers and all panels must initialize.
    """
    async def scenario(server):
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.goto(server['page_origin'], wait_until='networkidle', timeout=10000)
                assert errors == [], 'boot pageerrors: ' + json.dumps(errors)
                assert await page.locator('#state').inner_text() == 'idle'
                for control in ('start', 'end', 'interrupt', 'disconnect', 'retry', 'download'):
                    assert await page.locator('#' + control).evaluate('(node)=>typeof node.onclick') == 'function'
                for panel in ('session-evidence', 'review-workbench', 'replay-inspector', 'relay-metadata'):
                    assert await page.locator('#' + panel).inner_text()
                assert await page.locator('#replay-inspector [data-rp="raw"]').evaluate(
                    '(node)=>typeof node.onclick') == 'function'
                assert await page.locator('#replay-inspector [data-rp="raw-offset"]').count() == 1
            finally:
                await browser.close()

    with serve('127.0.0.1') as server:
        asyncio.run(scenario(server))


def test_same_origin_metadata_buttons_use_protected_post_only(serve):
    """what_bug_this_catches: metadata panels using GET/fallback or posting nonce in a URL."""
    async def scenario(server):
        async with ui(server) as (page, seen):
            for route in ('admission', 'diagnostics'):
                async with page.expect_response(lambda response: response.url ==
                                                server['relay_origin'] + '/' + route) as pending:
                    await page.get_by_test_id(route + '-read').click()
                response = await pending.value
                assert response.status == 200
                assert response.request.method == 'POST'
                assert response.request.post_data == '{}'
                assert response.request.headers['x-voicelab-nonce'] == server['nonce']
                await page.wait_for_function('''()=>document.querySelector(
                  '[data-testid="relay-metadata-status"]').textContent.startsWith('Received structural metadata')''')
            assert not any(method == 'GET' and url.endswith(('/admission', '/diagnostics'))
                           for method, url in seen['requests'])
            assert server['nonce'] not in page.url
            await _end_and_wait_closed(page)

    with serve('127.0.0.1') as server:
        asyncio.run(scenario(server))


def test_mock_journey_through_ui_and_post_end_evidence_inspector(serve):
    """what_bug_this_catches: a UI that cannot complete a scheduled mock turn against the real
    relay, an End that leaves session handles or skips the terminal, a post-End inspector
    that cannot read saved metadata from the same origin or that fills unknowns, and the
    launch nonce leaking into the page URL, DOM, console or trace export."""
    async def scenario(server):
        async with ui(server) as (page, seen):
            state = await complete_turn(page, 'what are your hours')
            assert state['driver']['responseDone'] >= 1
            assert 'protocol_error' not in (state['error'] or '')
            await _end_and_wait_closed(page)
            inspector = page.locator('#session-evidence')
            await inspector.locator('[data-read]').click()
            await page.wait_for_function(
                '()=>document.querySelector("#session-evidence [data-status]").textContent'
                '.startsWith("Received saved metadata")', timeout=12000)
            status = await inspector.locator('[data-status]').inner_text()
            assert 'Unknowns preserved' in status
            # Each projection key renders as <details><summary>key</summary><pre>JSON</pre>.
            # Parse the values: fixed notes in the projection also contain the word "null".
            sections = await page.evaluate('''()=>Object.fromEntries(
              [...document.querySelectorAll('#session-evidence [data-results] details')].map(
                d=>[d.querySelector('summary').textContent, JSON.parse(d.querySelector('pre').textContent)]))''')
            identity = sections['identity']
            assert identity['session_id'] and identity['run_id'] and identity['trial_id']
            # The producer's evidence root carries no epochs; they must stay null, not be filled.
            assert identity['connection_epoch'] is None and identity['response_epoch'] is None
            raw_trace = await _raw_trace_download(page)
            dom = await page.content()
            evidence_posts = [url for method, url in seen['requests']
                              if method == 'POST' and url.endswith('/evidence')]
            assert evidence_posts and all(url.startswith(server['relay_origin']) for url in evidence_posts)
            for surface in (page.url, dom, raw_trace, '\n'.join(seen['console'])):
                assert server['nonce'] not in surface
            assert seen['blocked'] == []

    with serve('127.0.0.1') as server:
        asyncio.run(scenario(server))


def test_barge_in_after_commit_fences_stale_epoch_audio(serve):
    """what_bug_this_catches: cancelling mid-response without advancing the worklet fence, so
    stale-epoch audio still renders, or cancellation rolling back a committed booking. The
    probe interrupts on the effect-committing task; old-epoch frames must be discarded."""
    async def scenario(server):
        async with ui(server) as (page, _seen):
            await complete_turn(page, 'list slots')
            await complete_turn(page, 'select slot 1')
            await _listening(page)  # baseline after any retry, so its fence is not counted
            baseline = await snapshot(page)
            state = await complete_turn(page, 'yes confirm', cancel_after_commit=True)
            assert state['driver']['cancelTriggered'] > baseline['driver']['cancelTriggered']
            # Wait until #task holds the relay's post-cancel snapshot (the interrupt reply carries
            # no effect_committed), not the confirm outcome itself. A cancelAcks counter is not
            # enough: an earlier retry-triggered interrupt reply can arrive late and bump it.
            await page.wait_for_function('''()=>{try{
              return !('effect_committed' in JSON.parse(document.querySelector('#task').textContent));
            }catch(error){return false;}}''', timeout=4000)
            state = await snapshot(page)
            prior_epoch = (baseline['driver']['lastFence'] or {}).get('epoch', 0)
            fence = state['driver']['lastFence']
            assert fence is not None and fence['epoch'] > prior_epoch
            assert state['driver']['fenceCounter'] > baseline['driver']['fenceCounter']
            task = json.loads(state['task'] or '{}')
            assert 'effect_committed' not in task, '#task must show the post-cancel snapshot'
            booking = task.get('booking') or (task.get('task') or {}).get('booking')
            assert booking, 'cancellation after commit must preserve the booking'
            prior = state['driver']['staleDiscards']
            await page.evaluate('()=>window.__voiceLabDriver.staleControl()')
            await page.wait_for_function('(prior)=>window.__voiceLabDriver.snapshot().staleDiscards>prior',
                                         arg=prior, timeout=2000)
            after = await page.evaluate('()=>window.__voiceLabDriver.snapshot()')
            assert after['lastFence']['epoch'] == fence['epoch']  # stale frame did not move the fence
            await _end_and_wait_closed(page)

    with serve('127.0.0.1') as server:
        asyncio.run(scenario(server))


def test_separate_origin_serving_is_visibly_refused(serve):
    """what_bug_this_catches: relaxing CORS or reading evidence cross-origin when the UI is
    served from a different origin than the relay. Page at localhost, relay at 127.0.0.1:
    the inspector must show the incomplete-serving refusal, never POST evidence, and the
    relay must not grant cross-origin reads (no Access-Control-Allow-Origin)."""
    async def scenario(server):
        async with ui(server) as (page, seen):
            status = page.locator('#session-evidence [data-status]')
            await page.wait_for_function(
                '()=>document.querySelector("#session-evidence [data-status]").textContent'
                '.startsWith("Incomplete serving seam")', timeout=4000)
            assert 'Cross-origin evidence reads are refused' in await status.inner_text()
            for route in ('admission', 'diagnostics'):
                await page.get_by_test_id(route + '-read').click()
                await page.wait_for_function('''()=>document.querySelector(
                  '[data-testid="relay-metadata-status"]').textContent.startsWith('Local metadata unavailable')''')
            assert not any(method == 'POST' and url.endswith(('/admission', '/diagnostics'))
                           for method, url in seen['requests'])
            await _end_and_wait_closed(page)
            assert await page.locator('#session-evidence [data-read]').is_disabled()
            cross = await page.evaluate('''async (origin)=>{
              try{await fetch(origin + '/health', {mode:'cors', credentials:'omit'}); return 'readable';}
              catch(error){return 'blocked';}}''', server['relay_origin'])
            assert cross == 'blocked'
            assert not any(method == 'POST' and url.endswith('/evidence') for method, url in seen['requests'])

    with serve('localhost') as server:
        asyncio.run(scenario(server))
