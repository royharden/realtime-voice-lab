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


@asynccontextmanager
async def ui(server: dict, arm: str = 'V'):
    """Open the real UI, enter relay URL + nonce, pick mock/arm, start, await listening."""
    blocked, console, requests, pageerrors = [], [], [], []
    allowed = {server['page_origin'], server['relay_origin']}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True, args=CHROMIUM_ARGS + ['--use-file-for-fake-audio-capture=' + server['capture_wav']])
        try:
            context = await browser.new_context(permissions=['microphone'])
            context.set_default_timeout(4000)

            async def route(request):
                parsed = urlsplit(request.request.url)
                if f'{parsed.scheme}://{parsed.netloc}' in allowed:
                    await request.continue_()
                else:
                    blocked.append(request.request.url)
                    await request.abort('blockedbyclient')
            await context.route('**/*', route)
            await context.add_init_script(path=str(PROBE))
            page = await context.new_page()
            page.on('console', lambda message: console.append(message.text))
            page.on('pageerror', lambda error: pageerrors.append(str(error)))
            page.on('request', lambda request: requests.append((request.method, request.url)))
            await page.goto(server['page_origin'] + '/', wait_until='domcontentloaded', timeout=10000)
            await page.locator('#url').fill(server['relay_url'])
            await page.locator('#nonce').fill(server['nonce'])
            await page.locator('#mode').select_option('mock')
            await page.locator(f'[data-arm="{arm}"]').click()
            # Several <details> exist (stimulus, review, replay); open only the stimulus panel.
            await page.locator('details:has(#stimulus)').evaluate('(element)=>element.open=true')
            await page.locator('#start').click()
            try:
                await wait_state(page, ['listening'], timeout=8000)
            except Exception as error:
                diagnostic = await page.evaluate('''()=>({state:document.querySelector('#state')?.textContent,
                  error:document.querySelector('#error')?.textContent,
                  resources:document.querySelector('#resources')?.textContent})''')
                diagnostic['pageerrors'] = pageerrors
                diagnostic['console'] = console[-10:]
                raise AssertionError('UI startup diagnostic: ' +
                    json.dumps(diagnostic).replace(server['nonce'], '[redacted]')) from error
            yield page, {'blocked': blocked, 'console': console, 'requests': requests}
        finally:
            await browser.close()


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
            finally:
                await browser.close()

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
            await _end_and_wait_closed(page)
            assert await page.locator('#session-evidence [data-read]').is_disabled()
            cross = await page.evaluate('''async (origin)=>{
              try{await fetch(origin + '/health', {mode:'cors', credentials:'omit'}); return 'readable';}
              catch(error){return 'blocked';}}''', server['relay_origin'])
            assert cross == 'blocked'
            assert not any(method == 'POST' and url.endswith('/evidence') for method, url in seen['requests'])

    with serve('localhost') as server:
        asyncio.run(scenario(server))
