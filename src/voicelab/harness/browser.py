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


# One canonical producer projection, also enforced by EvidenceSink before writes.
from .privacy import observed_metadata

metadata = observed_metadata
wire_metadata = observed_metadata


def own_diagnostic(spec: dict, nonce: str, path: str = '/diagnostics', *, body: dict | None = None) -> dict:
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise ValueError('diagnostic redirect refused')
    relay = urlsplit(spec['relay_url'])
    url = urlunsplit(('http', relay.netloc, path, '', ''))
    loopback_url(url, {'http'})
    front = urlsplit(spec['frontend_url'])
    data = json.dumps(body, separators=(',', ':')).encode() if body is not None else None
    if data is not None and len(data) > 512:
        raise ValueError('bounded protected control body required')
    request = Request(url, data=data, headers={'X-Voicelab-Nonce': nonce,
                      'Content-Type': 'application/json',
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
        from voicelab.evaluation.exposure import validate_read_access
        validate_read_access(self.spec.get('_artifact_reader'))
        if not self.spec.get('_input_wav'):
            raise ValueError('actual reviewed microphone fixture binding required before browser activation')
        # Verify the intended own relay is mock before creating a browser.
        health = await asyncio.to_thread(own_diagnostic, self.spec, self.nonce, '/health')
        mode = self.spec.get('mode')
        if mode == 'live':
            from .cohort import AdmissionGrant
            grant = self.spec.get('_cohort_grant')
            if not isinstance(grant, AdmissionGrant):
                raise ValueError('live driver needs externally verified programmatic cohort admission')
            grant.recheck(self.spec)
        if mode not in {'mock', 'live'} or health.get('mode') != mode or health.get('protocol_version') != 1:
            raise ValueError('exact relay mode/protocol admission refused; no fallback')
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        try:
            arguments = [
                '--autoplay-policy=no-user-gesture-required', '--use-fake-ui-for-media-stream',
                '--use-fake-device-for-media-stream']
            if self.spec.get('_fixture_stream') is not True:
                arguments.append('--use-file-for-fake-audio-capture=' + self.spec['_input_wav'])
            self.browser = await self.playwright.chromium.launch(headless=True, args=arguments)
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
        from voicelab.evaluation.exposure import validate_read_access
        validate_read_access(self.spec.get('_artifact_reader'))
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
        if self.spec.get('_fixture_stream') is True:
            await context.add_init_script(path=str(Path(__file__).with_name('stimulus.js')))
        await context.add_init_script(path=str(Path(__file__).with_name('probe.js')))
        page = await context.new_page()
        await page.route_web_socket('**/*', lambda route: self._socket_route(route, task))
        page.on('websocket', lambda socket: self.observe_socket(socket, task))
        await page.goto(self.spec['frontend_url'], wait_until='domcontentloaded', timeout=10000)
        await page.locator('#url').fill(self.spec['relay_url'])
        await page.locator('#nonce').fill(self.nonce)
        await page.locator('#mode').select_option(self.spec['mode'])
        if self.spec['mode'] == 'live':
            await page.locator('#live-consent').check()
        await page.locator(f'[data-arm="{task["arm"]}"]').click()
        await page.locator('details').evaluate('(element)=>element.open=true')
        if self.spec.get('_fixture_stream') is True:
            from .access import read_owned
            raw = read_owned(self.spec.get('_artifact_reader'), self.spec['fixture_root'],
                             self.spec['_fixture']['audio']['path'], maximum=6_000_000)
            if hashlib.sha256(raw).hexdigest() != self.spec['_fixture']['audio']['sha256']:
                raise ValueError('exact replay bytes differ from frozen reviewed fixture')
            decoded = await page.evaluate('(bytes)=>window.__voiceLabFixtureSource.load(bytes)', list(raw))
            expected = self.spec['_fixture']['audio']
            if decoded.get('sample_rate') != expected['sample_rate'] or decoded.get('frame_count') != expected['frame_count']:
                raise ValueError('decoded fixture rate/frame count differs from reviewed catalog')
        return page, context

    async def _socket_route(self, route, task):
        # Restrict even browser-created WebSockets to this exact relay endpoint.
        parsed, expected = urlsplit(route.url), urlsplit(self.spec['relay_url'])
        if (parsed.scheme, parsed.netloc, parsed.path) != (expected.scheme, expected.netloc, expected.path):
            await route.close(code=1008, reason='driver loopback boundary')
        else:
            server = await route.connect_to_server()
            if self.spec.get('_network'):
                from .network import MessageNetwork
                network = MessageNetwork(self.spec['_network'], task.get('pair_id') or task['task_id'], self.sink, task)
                route.on_message(lambda message: network.forward('upload', message, server, route.close))
                server.on_message(lambda message: network.forward('download', message, route, route.close))

    def observe_socket(self, socket, task):
        audio_groups = {}
        def flush_audio():
            if self.sink.closed:
                return
            for group in audio_groups.values():
                self.sink.emit('wire_audio_rollup', task_id=task['task_id'], role=task.get('role', 'trial'),
                    session_id=group['session_id'], direction=group['direction'],
                    observed_message_count=group['count'], metadata_stream_sha256=group['digest'].hexdigest(),
                    scope='browser_socket_metadata_only', audio_observation_stride=64)
            audio_groups.clear()
        def frame(direction, raw):
            try:
                value = json.loads(raw)
                if not isinstance(value, dict) or value.get('type') not in {
                        'hello', 'audio', 'commit', 'interrupt', 'rendered', 'resume', 'end', 'client_status',
                        'ping', 'clock_probe', 'ready', 'state', 'transcript', 'event', 'task', 'error',
                        'ended', 'pong', 'clock_pong'}:
                    raise ValueError('closed wire metadata envelope required')
                if direction == 'sent' and isinstance(value, dict) and value.get('type') == 'commit':
                    actual = value.get('turn_id')
                    planned = task.get('_logical_turn_id')
                    if actual is not None and planned is not None:
                        links = task.setdefault('_wire_turns', {})
                        if actual in links and links[actual] != planned:
                            self.sink.emit('driver_identity_conflict', observation='unknown', task_id=task['task_id'],
                                transport_turn_id=actual, logical_turn_id=planned)
                            task['_identity_conflict'] = True
                        else:
                            links[actual] = planned
                if direction == 'received' and isinstance(value, dict):
                    if value.get('type') == 'ready':
                        task['_session_id'] = value.get('session_id')
                        payload = value.get('payload', {})
                        task['_trial_namespace'] = payload.get('trial_namespace')
                        task['_server_run_id'] = payload.get('run_id') or value.get('run_id')
                        task['_boot_id'] = payload.get('boot_id') or value.get('boot_id')
                        task['_ready_metadata'] = wire_metadata(value)
                        self.sink.emit('plan_session_mapping', task_id=task['task_id'],
                            planned_trial_id=task.get('trial_id'), arm=task['arm'],
                            session_id=task['_session_id'], server_trial_namespace=task['_trial_namespace'],
                            server_run_id=task['_server_run_id'], observed_boot_id=task['_boot_id'],
                            source='received_ready_envelope; observed IDs retained')
                    if value.get('type') == 'ended':
                        task['_ended_envelope'] = wire_metadata(value)
                # Audio payloads never persist in telemetry. Hash references stay intact.
                clean = wire_metadata(value)
                if value.get('type') == 'audio':
                    actual_session = value.get('session_id') or task.get('_session_id')
                    key = (direction, actual_session, value.get('turn_id'), value.get('response_id'),
                           value.get('connection_epoch'), value.get('response_epoch'))
                    if key not in audio_groups:
                        audio_groups[key] = {'session_id': actual_session, 'direction': direction,
                                             'count': 0, 'digest': hashlib.sha256()}
                    group = audio_groups[key]
                    group['count'] += 1
                    group['digest'].update(json.dumps(clean, sort_keys=True, separators=(',', ':')).encode())
                    if group['count'] != 1 and group['count'] % 64:
                        return  # counts and metadata digest close explicitly; no raw PCM is persisted
                elif value.get('type') in {'commit', 'state', 'ended'} or (
                        value.get('type') == 'event' and value.get('payload', {}).get('kind') == 'response_done'):
                    flush_audio()
                self.sink.emit('wire_' + direction, task_id=task['task_id'], arm=task['arm'],
                               role=task.get('role', 'trial'),
                               logical_turn_id=(task.get('_logical_turn_id') if value.get('type') != 'audio' else None),
                               session_id=task.get('_session_id'),
                               server_trial_namespace=task.get('_trial_namespace'),
                               message_metadata=clean, browser_clock_observed=False)
            except (ValueError, TypeError):
                self.sink.emit('wire_parse_error', task_id=task['task_id'], arm=task['arm'],
                               direction=direction, byte_length=len(raw))
        socket.on('framesent', lambda raw: frame('sent', raw))
        socket.on('framereceived', lambda raw: frame('received', raw))
        socket.on('close', lambda *args: flush_audio())


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


def collect_ledger_snapshot(spec: dict, evidence: dict) -> bytes:
    meta = evidence.get('ledger_artifact') or {}
    session = evidence['session']['session_id']
    digest = meta.get('artifact_hash', '')
    if meta.get('kind') != 'ledger_snapshot' or not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise ValueError('saved authoritative ledger artifact unknown')
    reference = f'evidence-snapshots/{session}-ledger_snapshot-{digest}.json'
    if meta.get('artifact_reference') != reference:
        raise ValueError('saved ledger reference does not bind the observed session/hash')
    root = Path(spec['relay_artifact_root']).resolve(strict=True)
    raw = owned_file(root, reference, maximum=32_000_000).read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('saved ledger snapshot hash mismatch')
    return raw
