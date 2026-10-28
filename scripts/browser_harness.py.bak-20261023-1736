"""Later operator-activated localhost calibration/soak. Never runs on import.

This is orchestration SOURCE, not evidence of a browser run. Requires separately
resolved playwright==1.49.1 and its reviewed Chromium installation.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, HTTPRedirectHandler, ProxyHandler, build_opener


def local_url(value, schemes):
    parsed = urlsplit(value)
    if (parsed.scheme not in schemes or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or parsed.port is None or not 20000 <= parsed.port <= 29999
            or parsed.username or parsed.password or parsed.fragment):
        raise ValueError('registered loopback URL required')
    return value


def validate_activation(manifest, activation_reference):
    if not activation_reference or manifest.get('activation_reference') != activation_reference:
        raise ValueError('explicit later runtime activation reference required')
    if manifest.get('mode') != 'mock' or not manifest.get('run_id'):
        raise ValueError('this offline driver permits pinned mock runs only')
    local_url(manifest['frontend_url'], {'http'})
    local_url(manifest['relay_url'], {'ws'})
    if not manifest.get('ports_checked_reference'):
        raise ValueError('operator must run agent-ports check immediately before activation')
    claims = manifest.get('port_claim_paths', [])
    if len(claims) != 2 or not all(Path(path).is_file() for path in claims):
        raise ValueError('two existing port claim receipts required')
    # Existence is an operator checkpoint, never fabricated ownership verification.
    fixture = Path(manifest['licensed_input_wav'])
    if not manifest.get('fixture_license_reference') or hashlib.sha256(fixture.read_bytes()).hexdigest() != manifest['input_sha256']:
        raise ValueError('approved input WAV provenance/hash required')


CALIBRATION_JS = r"""async () => {
  const context = new AudioContext(); await context.resume();
  const module = `class Probe extends AudioWorkletProcessor {
    constructor(){super();this.previous=false;this.offset=0;this.cutoff=Infinity;
      this.port.onmessage=e=>{if(e.data.cutoff!==undefined)this.cutoff=e.data.cutoff;};}
    process(inputs,outputs){const input=inputs[0]?.[0];const output=outputs[0][0];
      for(let i=0;i<output.length;i++){const frame=currentFrame+i;
        const sample=frame>=this.cutoff?0:(input?.[i]||0);output[i]=sample;
        const on=Math.abs(sample)>.1;if(on&&!this.previous)this.port.postMessage({kind:'onset',frame,quantum:output.length});
        if(!on&&this.previous)this.port.postMessage({kind:'offset',frame,quantum:output.length});
        if(frame===this.cutoff)this.port.postMessage({kind:'cutoff',frame,quantum:output.length});
        this.previous=on;}return true;}}
    registerProcessor('calibration-probe',Probe);`;
  const url=URL.createObjectURL(new Blob([module],{type:'text/javascript'}));
  await context.audioWorklet.addModule(url);URL.revokeObjectURL(url);
  const records=[];let previousStamp=null;
  try{for(let repeat=0;repeat<30;repeat++){
    const node=new AudioWorkletNode(context,'calibration-probe');node.connect(context.destination);
    const gap=Math.round(context.sampleRate*.1),start=Math.ceil((context.currentTime+.1)*context.sampleRate);
    const buffer=context.createBuffer(1,gap+Math.round(context.sampleRate*.03),context.sampleRate);
    const data=buffer.getChannelData(0);data.fill(.2,0,Math.round(context.sampleRate*.01));
    data.fill(.2,gap);const source=context.createBufferSource();source.buffer=buffer;source.connect(node);
    const cutoff=start+gap+Math.round(context.sampleRate*.02);node.port.postMessage({cutoff});
    const events=[];const done=new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('probe timeout')),2000);
      node.port.onmessage=e=>{events.push(e.data);if(e.data.kind==='cutoff'){clearTimeout(timer);resolve();}};});
    source.start(start/context.sampleRate);await done;const stamp=context.getOutputTimestamp();
    const onsets=events.filter(e=>e.kind==='onset');const cut=events.find(e=>e.kind==='cutoff');
    records.push({scheduled_gap_ms:gap/context.sampleRate*1000,
      observed_gap_ms:onsets.length===2?(onsets[1].frame-onsets[0].frame)/context.sampleRate*1000:null,
      observed_quantum_ms:cut.quantum/context.sampleRate*1000,quantum_samples:cut.quantum,
      cutoff_error_samples:cut.frame-cutoff,mapping_monotonic:stamp.performanceTime>=0&&stamp.contextTime>=0&&
        (!previousStamp||(stamp.performanceTime>previousStamp.performanceTime&&stamp.contextTime>previousStamp.contextTime)),
      clock_domain:'browser_performance_ms',raw_events:events,output_timestamp:stamp,
      boundary:'render_graph_probe; physical acoustic uncertainty unknown'});
    previousStamp=stamp;source.stop();source.disconnect();node.disconnect();node.port.close();
  }
  await context.audioWorklet.addModule('/audio-worklet.js');
  const appNode=new AudioWorkletNode(context,'lab-audio');
  const tap=new AudioWorkletNode(context,'calibration-probe');appNode.connect(tap);tap.connect(context.destination);
  const anchors=[],graph=[];appNode.port.onmessage=e=>anchors.push(e.data);tap.port.onmessage=e=>graph.push(e.data);
  const wave=new Float32Array(Math.round(context.sampleRate*.2));wave.fill(.2);
  appNode.port.postMessage({type:'audio',samples:wave,epoch:0,connection:0,responseId:'calibration',startSample:0});
  await new Promise(resolve=>setTimeout(resolve,30));
  appNode.port.postMessage({type:'fence',epoch:1,connection:0});
  appNode.port.postMessage({type:'audio',samples:wave,epoch:0,connection:0,responseId:'stale',startSample:0});
  await new Promise(resolve=>setTimeout(resolve,100));
  const appOnset=anchors.find(e=>e.type==='render_onset'),tapOnset=graph.find(e=>e.kind==='onset');
  const fence=anchors.find(e=>e.type==='fence_applied');const stamp=context.getOutputTimestamp();
  const offset=graph.find(e=>e.kind==='offset');
  const fenceCutoffValid=!!fence&&!!offset&&Math.abs(offset.frame-fence.frame)<=offset.quantum;
  const appMs=appOnset?stamp.performanceTime+(appOnset.contextTime-stamp.contextTime)*1000:null;
  const graphMs=tapOnset?stamp.performanceTime+(tapOnset.frame/context.sampleRate-stamp.contextTime)*1000:null;
  const tolerance=Math.max(20,(tapOnset?.quantum||128)/context.sampleRate*2000);
  const anchorValid=appMs!==null&&graphMs!==null&&Math.abs(appMs-graphMs)<=tolerance;
  const offsetRejected=appMs!==null&&graphMs!==null&&Math.abs(appMs+1000-graphMs)>tolerance;
  // A deliberately past source time must clamp to current playback, never map as heard in the past.
  const past=context.createBufferSource();past.buffer=context.createBuffer(1,context.sampleRate*.01,context.sampleRate);
  past.buffer.getChannelData(0).fill(.2);const pastProbe=new AudioWorkletNode(context,'calibration-probe');
  past.connect(pastProbe);pastProbe.connect(context.destination);const before=context.currentTime;
  const pastEvents=[];pastProbe.port.onmessage=e=>pastEvents.push(e.data);past.start(Math.max(0,before-.5));
  await new Promise(resolve=>setTimeout(resolve,100));
  const pastOnset=pastEvents.find(e=>e.kind==='onset');
  const pastValid=!!pastOnset&&pastOnset.frame/context.sampleRate>=before-(pastOnset.quantum/context.sampleRate);
  appNode.disconnect();appNode.port.close();tap.disconnect();tap.port.close();past.stop();past.disconnect();pastProbe.disconnect();pastProbe.port.close();
  return {rows:records,controls:{app_worklet_anchor_valid:anchorValid,injected_offset_rejected:offsetRejected,
    past_scheduling_anchor_valid:pastValid,fence_ack_observed:!!fence,app_fence_cutoff_within_quantum:fenceCutoffValid,
    stale_epoch_frame_refused:anchors.some(e=>e.type==='stale_audio_discarded'),
    raw_app_anchors:anchors,raw_graph_anchors:graph,raw_past_anchors:pastEvents,
    acoustic_uncertainty:'unknown; render graph and output timestamp estimate only'}};
  }finally{await context.close();}
}"""


async def configure_page(context, manifest, nonce, arm='K'):
    page = await context.new_page()
    await page.goto(manifest['frontend_url'], wait_until='domcontentloaded', timeout=10000)
    await page.locator('#url').fill(manifest['relay_url'])
    await page.locator('#nonce').fill(nonce)
    await page.locator(f'[data-arm="{arm}"]').click()
    return page


def server_resources(manifest, nonce):
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise ValueError('diagnostic redirect refused')
    relay = urlsplit(manifest['relay_url'])
    url = urlunsplit(('http', relay.netloc, '/diagnostics', '', ''))
    local_url(url, {'http'})
    frontend = urlsplit(manifest['frontend_url'])
    origin = urlunsplit((frontend.scheme, frontend.netloc, '', '', ''))
    request = Request(url, headers={'X-Voicelab-Nonce': nonce, 'Origin': origin})
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=2) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError('diagnostic frame bound')
        result = json.loads(raw)
        if result.get('scope') != 'own_server_process_only' or result.get('clock_domain') != 'server_monotonic_ns':
            raise ValueError('unexpected metrics scope')
        return result
    except Exception:
        return {'scope': 'own_server_process_only', 'metrics': {}, 'reason': 'diagnostic observation unavailable'}


async def sample_resources(pages, sessions, browser_session, manifest, nonce):
    heaps = {}
    for index, session in enumerate(sessions):
        metrics = await session.send('Performance.getMetrics')
        values = {item['name']: item['value'] for item in metrics['metrics']}
        heaps[str(index)] = values.get('JSHeapUsedSize')
    browser_metrics = await browser_session.send('SystemInfo.getProcessInfo')
    server = await asyncio.to_thread(server_resources, manifest, nonce)
    metrics = server.get('metrics', {})
    def observed(name):
        item = metrics.get(name, {})
        return item.get('value') if item.get('observation') == 'observed' else None
    return {'monotonic_seconds': time.monotonic(), 'browser_heaps_by_context': heaps,
            'browser_heap_bytes': max(v for v in heaps.values() if v is not None) if all(v is not None for v in heaps.values()) else None,
            'rss_bytes': observed('rss_bytes'), 'owned_handles': None, 'queue_seconds': None,
            'sockets': observed('open_sockets'), 'files': observed('open_files'),
            'async_tasks': observed('asyncio_tasks'), 'event_loop_lag_ms': observed('event_loop_lag_ms'),
            'server_observations': server,
            'browser_process_observations': browser_metrics,
            'page_states': [await page.locator('#state').inner_text() for page in pages]}


async def run(manifest, operation, nonce):
    from playwright.async_api import async_playwright
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=[
            '--autoplay-policy=no-user-gesture-required',
            '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
            '--use-file-for-fake-audio-capture=' + str(Path(manifest['licensed_input_wav']).resolve())])
        contexts, pages, sessions = [], [], []
        try:
            count = 1 if operation == 'calibrate' else 3
            for _ in range(count):
                context = await browser.new_context(permissions=['microphone'])
                context.set_default_timeout(3000)
                contexts.append(context)
                pages.append(await configure_page(context, manifest, nonce))
                session = await context.new_cdp_session(pages[-1])
                await session.send('Performance.enable')
                sessions.append(session)
            if operation == 'calibrate':
                observed = await pages[0].evaluate(CALIBRATION_JS)
                return {'kind': 'render_graph_calibration_observations', **observed,
                        'full_gate_complete': False,
                        'unmet_controls': ['independent raw sample audit', 'post-run matching calibration', 'physical acoustic uncertainty remains unknown']}
            browser_session = await browser.new_browser_cdp_session()
            deadline = time.monotonic() + 1800
            summary = {'aggregate_turns': 0, 'interruptions': 0, 'reconnects': 0,
                       'contexts': 3, 'fourth_session_refused': False}
            samples = []
            stop_sampling = asyncio.Event()

            async def sampler():
                while not stop_sampling.is_set():
                    tick = time.monotonic()
                    row = await sample_resources(pages, sessions, browser_session, manifest, nonce)
                    row['after_warmup'] = summary['aggregate_turns'] >= 20
                    samples.append(row)
                    try:
                        await asyncio.wait_for(stop_sampling.wait(), timeout=max(.01, tick + 2 - time.monotonic()))
                    except asyncio.TimeoutError:
                        pass

            sampling = asyncio.create_task(sampler())
            try:
                # Restart bounded sessions after <=8 turns; do not bypass session limits.
                while summary['aggregate_turns'] < 200 and time.monotonic() < deadline:
                    for page in pages:
                        await page.locator('#start').click()
                    await asyncio.sleep(.5)
                    # Exercise/refuse an actual fourth context while three are admitted.
                    if not summary['fourth_session_refused']:
                        fourth = await browser.new_context(permissions=['microphone'])
                        try:
                            probe = await configure_page(fourth, manifest, nonce)
                            await probe.locator('#start').click()
                            await asyncio.sleep(.5)
                            summary['fourth_session_refused'] = 'admission' in (await probe.locator('#error').inner_text()).lower()
                        finally:
                            await fourth.close()
                    for turn in range(5):
                        for page in pages:
                            if time.monotonic() >= deadline or summary['aggregate_turns'] >= 200:
                                break
                            text = ('hours', 'list slots', 'select slot 1', 'confirm', 'status')[turn]
                            if await page.locator('#state').inner_text() in {'playing', 'awaiting_response'}:
                                await page.locator('#retry').click()
                            await page.locator('#text').fill(text)
                            await page.locator('#stimulus').evaluate('(form)=>form.requestSubmit()')
                            summary['aggregate_turns'] += 1
                            await asyncio.sleep(.15)
                            if summary['interruptions'] < 40:
                                await page.locator('#interrupt').click()
                                summary['interruptions'] += 1
                            if summary['aggregate_turns'] % 15 == 0 and summary['reconnects'] < 10:
                                await page.locator('#disconnect').click()
                                await asyncio.sleep(.1)
                                await page.locator('#retry').click()
                                summary['reconnects'] += 1
                                await asyncio.sleep(.5)
                    for page in pages:
                        await page.locator('#end').click()
                    await asyncio.sleep(.1)
            finally:
                stop_sampling.set()
                await sampling
            summary['coverage_actions_are_attempts'] = True
            summary['limitation'] = 'Must audit traces for admitted/terminal counts; actions alone do not establish coverage or recovery'
            return {'samples': samples, 'summary': summary, 'full_gate_complete': False,
                    'unmet_controls': ['post-End owned handle proof', 'worklet maximum queue observation',
                                      'trace-audited interruption and recovery outcomes']}
        finally:
            for context in contexts:
                await context.close()
            await browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['calibrate', 'soak'])
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--activation-reference', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
    validate_activation(manifest, args.activation_reference)
    nonce = os.environ.get('VOICELAB_NONCE', '')
    if not 16 <= len(nonce) <= 128:
        raise ValueError('operator launch nonce required; never write it into artifacts')
    async def bounded():
        return await asyncio.wait_for(run(manifest, args.operation, nonce), timeout=1900)
    result = asyncio.run(bounded())
    Path(args.out).write_text(json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
