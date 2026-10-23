"""Real browser/relay source driver, imported only after activation validation."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .activation import loopback_url, owned_file


def metadata(value: object) -> object:
    """No launch nonce, raw capture/output PCM, URLs, or arbitrary exception strings."""
    if isinstance(value, dict):
        return {key: metadata(item) for key, item in value.items()
                if key not in {'nonce', 'pcm16', 'audio', 'samples', 'url', 'authorization', 'api_key',
                               'text', 'transcript', 'transcripts', 'raw_transcript'}}
    if isinstance(value, list):
        return [metadata(item) for item in value]
    return value


WIRE_FIELDS = frozenset({
    'type', 'protocol_version', 'payload', 'mode', 'arm', 'run_id', 'trial_id', 'trial_namespace',
    'boot_id', 'session_id', 'turn_id', 'logical_turn_id', 'transport_turn_id', 'response_id',
    'operation_id', 'logical_operation_id', 'attempt_id', 'sequence', 'connection_epoch',
    'response_epoch', 'sample_rate', 'sample_offset', 'rendered_through_sample', 'state',
    'kind', 'code', 'outcome', 'terminal', 'reason', 'terminal_outcome', 'outcome_source',
    'clock_domain', 'timestamp', 'span_start_ns', 'span_end_ns', 'span_clock_domain',
    'artifact_hash', 'content_hash', 'artifact_persisted', 'trace_hash', 'trace', 'trace_reference',
    'trace_complete', 'event_count', 'terminal_present', 'boot_id', 'evidence_reference',
    'resume_required', 'audio_replayed', 'fence_epoch', 'task', 'booking', 'slot_id',
    'state_version', 'effect_committed', 'duplicate', 'ok', 'spoken_correctness', 'synthetic',
    'ledger', 'attempts', 'events', 'budget', 'known_cost', 'unknown_count', 'unknown_exposure',
    'total_exposure', 'complete', 'currency', 'component', 'reservation', 'settled',
    'price_version', 'source', 'usage', 'provider_requests', 'admission_halted',
    'control_id', 'seed', 'status', 'simulation', 'effect', 'lifecycle_persisted',
})


def wire_metadata(value: object) -> object:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key not in WIRE_FIELDS:
                continue
            if key == 'payload' and isinstance(item, str):
                try:
                    item = json.loads(item)
                except ValueError:
                    continue
            result[key] = wire_metadata(item)
        return result
    if isinstance(value, list):
        return [wire_metadata(item) for item in value]
    return value


def own_diagnostic(spec: dict, nonce: str, path: str = '/diagnostics') -> dict:
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise ValueError('diagnostic redirect refused')
    relay = urlsplit(spec['relay_url'])
    url = urlunsplit(('http', relay.netloc, path, '', ''))
    loopback_url(url, {'http'})
    front = urlsplit(spec['frontend_url'])
    request = Request(url, headers={'X-Voicelab-Nonce': nonce,
                      'Origin': urlunsplit((front.scheme, front.netloc, '', '', ''))})
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=2) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError('diagnostic response bound')
    return json.loads(raw)


class BrowserDriver:
    def __init__(self, spec: dict, nonce: str, sink):
        self.spec, self.nonce, self.sink = spec, nonce, sink
        self.playwright = self.browser = None
        self.contexts = []

    async def __aenter__(self):
        # Verify the intended own relay is mock before creating a browser.
        health = await asyncio.to_thread(own_diagnostic, self.spec, self.nonce, '/health')
        if health.get('mode') != 'mock' or health.get('protocol_version') != 1:
            raise ValueError('mock relay protocol gate refused')
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        try:
            self.browser = await self.playwright.chromium.launch(headless=True, args=[
                '--autoplay-policy=no-user-gesture-required', '--use-fake-ui-for-media-stream',
                '--use-fake-device-for-media-stream',
                '--use-file-for-fake-audio-capture=' + self.spec['_input_wav']])
        except BaseException:
            await self.playwright.stop()
            raise
        return self

    async def __aexit__(self, *exc):
        try:
            for context in self.contexts:
                await context.close()
        finally:
            try:
                if self.browser:
                    await self.browser.close()
            finally:
                if self.playwright:
                    await self.playwright.stop()

    async def page(self, task: dict):
        context = await self.browser.new_context(permissions=['microphone'])
        self.contexts.append(context)
        context.set_default_timeout(4000)
        allowed = urlsplit(self.spec['frontend_url'])
        async def route(request):
            parsed = urlsplit(request.request.url)
            if (parsed.scheme, parsed.netloc) == (allowed.scheme, allowed.netloc):
                await request.continue_()
            else:
                await request.abort('blockedbyclient')
        await context.route('**/*', route)
        await context.add_init_script(path=str(Path(__file__).with_name('probe.js')))
        page = await context.new_page()
        await page.route_web_socket('**/*', lambda route: self._socket_route(route))
        page.on('websocket', lambda socket: self.observe_socket(socket, task))
        await page.goto(self.spec['frontend_url'], wait_until='domcontentloaded', timeout=10000)
        await page.locator('#url').fill(self.spec['relay_url'])
        await page.locator('#nonce').fill(self.nonce)
        await page.locator('#mode').select_option('mock')
        await page.locator(f'[data-arm="{task["arm"]}"]').click()
        await page.locator('details').evaluate('(element)=>element.open=true')
        return page, context

    async def _socket_route(self, route):
        # Restrict even browser-created WebSockets to this exact relay endpoint.
        parsed, expected = urlsplit(route.url), urlsplit(self.spec['relay_url'])
        if (parsed.scheme, parsed.netloc, parsed.path) != (expected.scheme, expected.netloc, expected.path):
            await route.close(code=1008, reason='driver loopback boundary')
        else:
            await route.connect_to_server()

    def observe_socket(self, socket, task):
        def frame(direction, raw):
            try:
                value = json.loads(raw)
                if direction == 'received' and isinstance(value, dict):
                    if value.get('type') == 'ready':
                        task['_session_id'] = value.get('session_id')
                        payload = value.get('payload', {})
                        task['_trial_namespace'] = payload.get('trial_namespace')
                        task['_server_run_id'] = payload.get('run_id') or value.get('run_id')
                        task['_boot_id'] = payload.get('boot_id') or value.get('boot_id')
                        self.sink.emit('plan_session_mapping', task_id=task['task_id'],
                            planned_trial_id=task.get('trial_id'), arm=task['arm'],
                            session_id=task['_session_id'], server_trial_namespace=task['_trial_namespace'],
                            server_run_id=task['_server_run_id'], observed_boot_id=task['_boot_id'],
                            source='received_ready_envelope; observed IDs retained')
                    if value.get('type') == 'ended':
                        task['_ended_envelope'] = wire_metadata(value)
                # Audio payloads never persist in telemetry. Hash references stay intact.
                clean = wire_metadata(value)
                self.sink.emit('wire_' + direction, task_id=task['task_id'], arm=task['arm'],
                               logical_turn_id=task.get('_logical_turn_id'),
                               session_id=task.get('_session_id'),
                               server_trial_namespace=task.get('_trial_namespace'),
                               message=clean, browser_clock_observed=False)
            except (ValueError, TypeError):
                self.sink.emit('wire_parse_error', task_id=task['task_id'], arm=task['arm'],
                               direction=direction, byte_length=len(raw))
        socket.on('framesent', lambda raw: frame('sent', raw))
        socket.on('framereceived', lambda raw: frame('received', raw))


async def snapshot(page) -> dict:
    return await page.evaluate("""() => ({state:document.querySelector('#state')?.textContent,
      error:document.querySelector('#error')?.textContent,
      task:document.querySelector('#task')?.textContent,
      cost:document.querySelector('#cost')?.textContent,
      driver:window.__voiceLabDriver.snapshot(),
      app:window.__voiceLabDiagnostics?.snapshot?.()??null})""")


async def trace_export(page) -> dict:
    # Uses the application's real export path; its retained-window limitation remains visible.
    async with page.expect_download(timeout=4000) as pending:
        await page.locator('#download').click()
    download = await pending.value
    path = await download.path()
    if path is None:
        raise ValueError('browser trace download unavailable')
    raw = Path(path).read_bytes()
    if len(raw) > 4_000_000:
        raise ValueError('browser trace export bound')
    return metadata(json.loads(raw))


async def server_evidence(spec: dict, nonce: str, task: dict, *, terminal: bool = True) -> dict:
    session = task.get('_session_id')
    if not isinstance(session, str) or not re.fullmatch(r's-[a-f0-9]{24}', session):
        raise ValueError('observed opaque session identity required')
    value = {}
    for _ in range(20):
        value = await asyncio.to_thread(own_diagnostic, spec, nonce, f'/sessions/{session}/evidence')
        if not terminal or value.get('session', {}).get('terminal_outcome') is not None:
            break
        await asyncio.sleep(.1)
    if value.get('session', {}).get('session_id') != session:
        raise ValueError('server evidence session mismatch')
    if value['session'].get('trial_id') != task.get('_trial_namespace'):
        raise ValueError('server evidence namespace mismatch')
    value['terminal_observation'] = 'observed' if value['session'].get('terminal_outcome') is not None else 'unknown'
    return metadata(value)


def collect_relay_trace(spec: dict, evidence: dict) -> bytes:
    # Explicit own relay root only. Never glob, open credentials, rewrite IDs, or import DB code.
    root = Path(spec['relay_artifact_root']).resolve(strict=True)
    session = evidence['session']
    name = session['session_id']
    if not re.fullmatch(r's-[a-f0-9]{24}', name):
        raise ValueError('observed session identity required for trace export')
    raw = owned_file(root, f'traces/{name}.jsonl', maximum=16_000_000).read_bytes()
    if not session.get('trace_complete') or hashlib.sha256(raw).hexdigest() != session.get('trace_hash'):
        raise ValueError('trace missing durable terminal or mismatched authoritative hash')
    return raw
