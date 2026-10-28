import './style.css';
import {Resampler, AUDIO_POLICY, encodePCM, decodePCM} from './audio';
import type {ResampledBlock} from './audio';
type State = 'idle'|'requesting_permission'|'connecting'|'listening'|'awaiting_response'|'playing'|'interrupting'|'disconnected'|'reconnecting'|'ending'|'closed'|'permission_denied'|'device_lost'|'silence_timeout'|'failed';
type Wire = Record<string, unknown> & {type:string};
const app = document.querySelector<HTMLDivElement>('#app')!;
app.innerHTML = `<header><a class="brand" href="#">◉ <span>VOICE LAB</span></a><span class="pill">LOCAL EXPERIMENT · M6</span></header>
<main><section class="intro"><p class="eyebrow">REALTIME SYSTEMS / COMPARATIVE WORKBENCH</p><h1>Listen to the whole path.</h1><p>One fictional booking task. Three audio pipelines. Every interruption, uncertain boundary, and committed effect stays visible.</p></section>
<div class="notice" id="evidence"><b>KEYLESS MOCK</b> Source construction · runtime unverified. Synthetic tones are diagnostic signals; they cannot establish spoken-task correctness or acoustic latency.</div>
<div class="workspace"><section class="card session"><div class="section-head"><h2>Session console</h2><span id="state" class="pill">idle</span></div><div class="arms" role="group" aria-label="Pipeline"><button class="arm selected" data-arm="V"><b>V</b><span>Native voice<small>Wire mock</small></span></button><button class="arm" data-arm="C"><b>C</b><span>Deterministic cascade<small>STT → controller → TTS mock</small></span></button><button class="arm" data-arm="K"><b>K</b><span>Canned control<small>No generation</small></span></button></div>
<div class="mode-controls"><label>Session mode<select id="mode"><option value="mock" selected>Mock · keyless diagnostic</option><option value="live">Live · gated / unverified / separate authorization required</option></select></label><label id="live-consent-label" hidden><input id="live-consent" type="checkbox"> I understand live may incur costs and requires separately authorized operator manifests. This acknowledgement does not authorize or activate a provider.</label></div><label>Local relay URL<input id="url" placeholder="ws://127.0.0.1:REGISTERED_PORT/ws" spellcheck="false"></label><label>Session nonce<input id="nonce" type="password" autocomplete="off" placeholder="Use the nonce printed by your local relay"></label>
<div class="scope"><span id="mic" class="dot"></span><div><strong id="headline">Ready when you are</strong><p id="detail">Start requests microphone permission. End releases audio and transport.</p></div><div class="signal" aria-hidden="true">▂ ▄ ▆ ▃ ▇ ▄ ▂</div></div>
<div class="actions"><button id="start" class="primary">Start session</button><button id="end">End</button><button id="interrupt">Interrupt</button><button id="disconnect">Disconnect</button><button id="retry">Explicit retry</button></div><p id="error" role="alert"></p>
<details><summary>Mock typed stimulus · diagnostic only</summary><p>Typed input bypasses speech interpretation and cannot pass speech adjudication.</p><form id="stimulus"><input id="text" aria-label="Mock typed stimulus" placeholder="Ask opening hours or available slots"><button>Send diagnostic turn</button></form></details>
<div class="section-head"><h3>Conversation</h3><span>Fictional service · English</span></div><div id="conversation" class="conversation"><p class="empty">Transcripts and explicit responses appear here.</p></div></section>
<aside><section class="card"><p class="eyebrow">AUTHORITATIVE EFFECT</p><h2>Task journal</h2><pre id="task">No task event received.</pre><p class="muted">An interruption does not undo a committed booking. The relay journal owns the effect.</p></section><section class="card"><p class="eyebrow">MEASUREMENT BOUNDARY</p><h2>Timing inspector</h2><dl><dt>Context sample rate</dt><dd id="rate">Unknown</dd><dt>Wire PCM</dt><dd>24,000 Hz · mono PCM16</dd><dt>Render boundary</dt><dd>Output timestamp estimate</dd><dt>Acoustic calibration</dt><dd>Unmeasured</dd><dt>Latency / p50 / p95</dt><dd>Not evaluated</dd></dl><pre id="timing">No rendered sample observed.</pre></section><section class="card"><p class="eyebrow">ALL ATTEMPTS</p><h2>Cost & completeness</h2><pre id="cost">Unknown until relay accounting arrives.</pre><p class="muted">Mock is not a live price or billing certification.</p></section></aside></div>
<section class="card trace"><div class="section-head"><div><p class="eyebrow">BROWSER PERFORMANCE CLOCK · MILLISECONDS</p><h2>Lifecycle trace</h2></div><button id="download">Export local trace</button></div><p class="muted">Receipt, endpoint decision, commit, worklet fence and rendered ACK are separate events. This bounded view retains the latest 500 events; export includes that window only.</p><div id="trace" role="log" aria-live="off"></div></section><footer>V / C / K compare pinned pipelines. No arm is presumed faster. Live requires separately authorized relay admission; no live execution is established.</footer></main>`;
const el = <T extends HTMLElement = HTMLElement>(id:string) => document.getElementById(id) as T;
let mode:'mock'|'live'='mock';
let retainedSession:string|undefined, disconnectedAt=0, reconnects=0, resuming=false;
let state:State='idle', arm='V', socket:WebSocket|undefined, context:AudioContext|undefined, stream:MediaStream|undefined, node:AudioWorkletNode|undefined, source:MediaStreamAudioSourceNode|undefined;
let captureResampler:Resampler|undefined, playbackResampler:Resampler|undefined;
let connection=0, epoch=0, turn=0, sequence=0, token=0, activeResponse:string|undefined, responseSamples=0, expectedSequence=-1;
let lastVoice=0, voiced=false, captureBaseFrame:number|undefined, captureLead:Float32Array[]=[], leadFrames=0, playbackDone=false, playbackQueued=0,
  pendingCancel:{responseId:string|undefined;epoch:number}|undefined,
 silenceTimer:ReturnType<typeof setTimeout>|undefined, sessionTimer:ReturnType<typeof setTimeout>|undefined, lastAck:{response_id:string;rendered_through_sample:number}|undefined;
type DiagnosticEvent={seq:number;at:number;kind:string;detail:Record<string,number|string|boolean|null>};
const diagnosticEvents:DiagnosticEvent[]=[];let diagnosticSequence=0,diagnosticDropped=0;
const diagnosticKinds=new Set(['state','capture_start','speech_onset','endpoint_decision','commit_dispatch','capture_sample_map','playback_sample_map','first_or_next_receipt','barge_in_detected','cancel_fence_dispatched','fence_applied','cancel_dispatch','rendered_ack','render_onset_estimate','response_source_complete','response_render_drained','stale_audio_discarded','stale_render_ack_discarded','audio_sequence_gap']);
const numericDiagnosticKeys=new Set(['frame','contextTime','context_time','context_seconds','source_start','inputStart','outputStart','inputPerOutput','inputLimit','delaySeconds','delayInputSamples','performance_ms','receipt_ms','epoch','connection','queued_frames','queuedFrames','quantum','rendered_through_sample','decision_frame','last_voiced_frame','last_voiced_context_frame','hangover_ms','onset_frame','detection_frame','detected_frame','sample_rate','context_sample_rate','input_rate','output_rate','device_frame_base','droppedFrames','lastOldRenderedFrame','fence_context_time','turn','wire_samples','window_ms','sustain_ms','threshold_dbfs','minimum_hot_samples','output_latency_seconds']);
function diagnosticTrace(at:number,kind:string,raw:unknown){
  if(!diagnosticKinds.has(kind))return;
  const detail:DiagnosticEvent['detail']={};
  if(raw&&typeof raw==='object')for(const [key,value] of Object.entries(raw)){
    if(numericDiagnosticKeys.has(key)&&(value===null||typeof value==='number'&&Number.isFinite(value)))detail[key]=value;
    if(key==='next'&&typeof value==='string'&&['idle','requesting_permission','connecting','listening','awaiting_response','playing','interrupting','disconnected','reconnecting','ending','closed','permission_denied','device_lost','silence_timeout','failed'].includes(value))detail.state=value;
    if(key==='boundary'&&['worklet_render_graph','browser_output_timestamp_estimate'].includes(String(value)))detail.boundary=String(value);
    if(key==='observation'&&['observed','estimated','unknown','estimated_energy_endpoint','observed_render_graph_input'].includes(String(value)))detail.observation=String(value);
  }
  diagnosticEvents.push({seq:++diagnosticSequence,at,kind,detail});
  if(diagnosticEvents.length>1000){diagnosticEvents.shift();diagnosticDropped++;}
}
const traces:{at:number;kind:string;detail:unknown}[]=[];
function trace(kind:string, detail:unknown={}) { const item={at:performance.now(),kind,detail}; diagnosticTrace(item.at,kind,detail);traces.push(item); if(traces.length>500)traces.shift(); const row=document.createElement('div'); row.textContent=`${item.at.toFixed(1).padStart(10)}  ${kind}  ${JSON.stringify(detail)}`; el('trace').prepend(row); while(el('trace').children.length>100)el('trace').lastElementChild?.remove(); }
function setState(next:State, detail='') {state=next; el('state').textContent=next; el('headline').textContent=next.replaceAll('_',' '); el('detail').textContent=detail; el('mic').classList.toggle('live',!!stream?.active && ['listening','playing'].includes(next)); trace('state',{next,detail});}
function send(message:Wire) {if(socket?.readyState!==WebSocket.OPEN)throw new Error('Transport is not open');const encoded=JSON.stringify(message);if(socket.bufferedAmount+new TextEncoder().encode(encoded).byteLength>128000)throw new Error('Upload queue exceeds two-second PCM envelope');socket.send(encoded);}
function stopCapture(){node?.port.postMessage({type:'capture',enabled:false});clearTimeout(silenceTimer);}
function fence(){epoch++;playbackQueued=0; node?.port.postMessage({type:'fence',epoch,connection});activeResponse=undefined;responseSamples=0;expectedSequence=-1;playbackResampler=context?new Resampler(24000,context.sampleRate):undefined;trace('cancel_fence_dispatched',{epoch,connection});}
function releaseAudio(){stopCapture();clearTimeout(sessionTimer);source?.disconnect();node?.disconnect();stream?.getTracks().forEach(track=>track.stop());stream=undefined;void context?.close();context=undefined;node=undefined;source=undefined;}
function fail(code:string,message:string){el('error').textContent=`${code}: ${message}`;trace('failure',{code,message});++token;releaseAudio();socket?.close();socket=undefined;setState(code==='permission_denied'?'permission_denied':code==='silence_timeout'?'silence_timeout':code==='device_lost'?'device_lost':'failed',message);}
function end(){++token;setState('ending','Releasing microphone, worklet, timers and socket.');try{if(socket?.readyState===WebSocket.OPEN)send({type:'end'});}catch(error){trace('end_send_failure',String(error));}finally{fence();releaseAudio();socket?.close();socket=undefined;setState('closed','Session ended. Start opens a new session.');}}
function listen(){stopCapture();captureResampler=context?new Resampler(context.sampleRate,24000):undefined;captureBaseFrame=undefined;captureLead=[];leadFrames=0;sequence=0;setState('listening','Speak now. Energy endpoint: 600 ms hangover; eight-second silence limit.');voiced=false;lastVoice=0;node?.port.postMessage({type:'capture',enabled:true});clearTimeout(silenceTimer);silenceTimer=setTimeout(()=>{if(state==='listening')fail('silence_timeout','No completed speech turn. Choose explicit retry or End.');},8000);}
function commit(text?:string){if(text&&mode!=='mock')throw new Error('Typed debug input is unavailable in live mode');if(turn>=8)throw new Error('Eight logical turn limit reached');if(state==='awaiting_response'||state==='playing')throw new Error('Finish or interrupt the active response first');stopCapture();if(!text&&captureResampler)upload(captureResampler.flush());voiced=false;turn++;trace('commit_dispatch',{turn,last_voiced_context_frame:lastVoice,context_sample_rate:context?.sampleRate,clock_domain:'audio_context_samples',policy:text?'typed_mock':'energy_600ms'});send({type:'commit',turn_id:String(turn),connection_epoch:connection,response_epoch:epoch,...(text?{text}:{})});sequence=0;lastAck=undefined;setState('awaiting_response','Waiting for relay response; capture is paused until explicit next turn.');}
function upload(block:ResampledBlock){
  if(!block.samples.length)return;
  trace('capture_sample_map',{...block.map,device_frame_base:captureBaseFrame,input_rate:context?.sampleRate,output_rate:24000});
  // Wire parser bounds each PCM frame to 4,800 samples (200 ms).
  for(let offset=0;offset<block.samples.length;offset+=4800)send({type:'audio',turn_id:String(turn+1),connection_epoch:connection,response_epoch:epoch,sequence:sequence++,sample_rate:24000,pcm16:encodePCM(block.samples.subarray(offset,offset+4800))});
}
function queuePlayback(block:ResampledBlock){if(!block.samples.length)return;trace('playback_sample_map',{...block.map,input_rate:24000,output_rate:context?.sampleRate});if(playbackQueued+block.samples.length>context!.sampleRate*AUDIO_POLICY.queueSeconds)throw new Error('Playback admission exceeds two seconds');playbackQueued+=block.samples.length;node?.port.postMessage({type:'audio',samples:block.samples,map:block.map,epoch,connection,responseId:activeResponse});}
function finishPlayback(){
  if(!activeResponse||!playbackResampler||playbackDone)return;
  playbackDone=true;queuePlayback(playbackResampler.flush());
  node?.port.postMessage({type:'drain',responseId:activeResponse,epoch,connection});
  trace('response_source_complete',{response_id:activeResponse,wire_samples:responseSamples});
}
function interrupt(){
  if(!node||pendingCancel)return;
  const responseId=activeResponse;setState('interrupting','Applying worklet fence before cancellation and capture restart.');
  pendingCancel={responseId,epoch:epoch+1};fence();
}
function conversation(text:string,kind:string){const row=document.createElement('p');row.className=kind;row.textContent=text;el('conversation').querySelector('.empty')?.remove();el('conversation').append(row);}
function receive(raw:string){if(raw.length>300000)throw new Error('Message bound exceeded');const envelope=JSON.parse(raw) as Wire;if(!envelope||typeof envelope.type!=='string'||envelope.protocol_version!==1||!envelope.payload||typeof envelope.payload!=='object')throw new Error('Malformed protocol');const m={...envelope,...(envelope.payload as Record<string,unknown>)} as Wire;trace('relay_'+m.type,m.type==='audio'?{sequence:m.sequence,response_id:m.response_id}:m);
  if(m.type==='ready'){if(m.mode!==mode||m.arm!==arm)throw new Error('Relay admitted a different mode or arm');el('evidence').textContent=`${mode.toUpperCase()} · ${String(m.evidence_label??'Runtime unverified')} · No independent acceptance established.`;retainedSession=String(m.session_id);if(m.resume_required){connection++;epoch++;send({type:'resume',connection_epoch:connection,...(lastAck?{rendered_ack:{type:'rendered',...lastAck}}:{})});}else{connection=Number(m.connection_epoch);epoch=Number(m.response_epoch);}node?.port.postMessage({type:'fence',epoch,connection});sessionTimer=setTimeout(()=>fail('task_deadline','120-second task limit reached.'),120000);listen();return;}
  if(m.type==='error'){fail(String(m.code),String(m.message));return;}
  if(m.type==='ended'){end();return;}
  if(m.type==='task'){el('task').textContent=JSON.stringify(m,null,2);return;}
  if(m.type==='transcript'){conversation(String(m.text??''),String(m.role??'assistant'));return;}
  if(m.type==='event'){if(m.kind==='response_done'){finishPlayback();}if(m.accounting)el('cost').textContent=JSON.stringify(m.accounting,null,2);return;}
  if(m.type==='state'){el('detail').textContent=String(m.state??m.message??'Relay state event');if(m.state==='listening'&&state==='playing')finishPlayback();return;}
  if(m.type==='audio'){
    if(m.connection_epoch!==connection||m.response_epoch!==epoch){trace('stale_audio_discarded');return;}
    if(m.sample_rate!==24000||typeof m.response_id!=='string'||typeof m.pcm16!=='string'||!Number.isInteger(m.sequence))throw new Error('Invalid audio contract');
    if(activeResponse&&activeResponse!==m.response_id)throw new Error('Concurrent response refused');
    if(Number(m.sequence)<=expectedSequence)throw new Error('Duplicate or reordered audio sequence');if(expectedSequence>=0&&Number(m.sequence)!==expectedSequence+1)trace('audio_sequence_gap',{previous:expectedSequence,current:m.sequence});expectedSequence=Number(m.sequence);
    if(activeResponse!==m.response_id){lastAck=undefined;playbackDone=false;playbackResampler=new Resampler(24000,context!.sampleRate);}if(playbackDone)throw new Error('Audio after response completion');activeResponse=m.response_id;const pcm=decodePCM(m.pcm16);trace('first_or_next_receipt',{response_id:activeResponse,source_start:responseSamples,receipt_ms:performance.now(),clock_domain:'browser_performance_ms'});queuePlayback(playbackResampler!.push(pcm));responseSamples+=pcm.length;setState('playing',`${mode==='mock'?'Diagnostic mock':'Unverified live'} playback. Microphone monitors sustained onset for interruption; Interrupt is also available.`);node?.port.postMessage({type:'capture',enabled:true});
  }
}
async function start(){if(!['idle','closed','failed','permission_denied','device_lost','silence_timeout','disconnected'].includes(state))return;const own=++token;resuming=state==='disconnected'&&!!retainedSession&&performance.now()-disconnectedAt<5000&&reconnects<2;if(resuming){reconnects++;await new Promise(resolve=>setTimeout(resolve,reconnects===1?250:1000));if(own!==token)return;}else{retainedSession=undefined;reconnects=0;}el('error').textContent='';try{
  const url=new URL(el<HTMLInputElement>('url').value);if(!['127.0.0.1','localhost','[::1]'].includes(url.hostname)||!['ws:','wss:'].includes(url.protocol)||!url.port)throw new Error('Enter your registered local loopback relay URL with an explicit port');
  if(mode==='live'&&!el<HTMLInputElement>('live-consent').checked)throw new Error('Live requires explicit acknowledgement and separately authorized relay admission');const nonce=el<HTMLInputElement>('nonce').value;if(!nonce)throw new Error('Enter the nonce from your local relay');
  setState('requesting_permission','Microphone permission is requested only by this action.');const acquired=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:false,autoGainControl:false}});if(own!==token){acquired.getTracks().forEach(t=>t.stop());return;}stream=acquired;pendingCancel=undefined;playbackQueued=0;context=new AudioContext();await context.audioWorklet.addModule('/audio-worklet.js');if(own!==token){releaseAudio();return;}await context.resume();captureResampler=new Resampler(context.sampleRate,24000);playbackResampler=new Resampler(24000,context.sampleRate);el('rate').textContent=`${context.sampleRate.toLocaleString()} Hz (actual)`;
  if(!resuming)turn=0;source=context.createMediaStreamSource(stream);node=new AudioWorkletNode(context,'lab-audio',{numberOfInputs:1,numberOfOutputs:1,outputChannelCount:[1]});source.connect(node);node.connect(context.destination);activeResponse=undefined;responseSamples=0;expectedSequence=-1;if(!resuming){connection=0;epoch=0;lastAck=undefined;}node.port.postMessage({type:'fence',epoch,connection});sequence=0;
  for(const track of stream.getTracks()){track.onended=()=>{if(own===token)fail('device_lost','Microphone ended; explicit user action is required.');};track.onmute=()=>{if(own===token)fail('device_lost','Microphone muted; explicit user action is required.');};}
  node.port.onmessage=({data})=>{if(own!==token)return;try{
    if(data.type==='capture'){
      node?.port.postMessage({type:'capture_ack',frames:data.samples.length});
      if(!['listening','playing'].includes(state))return;
      const samples=data.samples as Float32Array;
      if(state==='playing'){
        captureLead.push(samples);leadFrames+=samples.length;
        while(captureLead.length>1&&leadFrames>context!.sampleRate*.04)leadFrames-=captureLead.shift()!.length;
        if(data.detectedFrame===null)return;
        trace('barge_in_detected',{onset_frame:data.onsetFrame,detection_frame:data.detectedFrame,sample_rate:context!.sampleRate,threshold_dbfs:-45,sustain_ms:20,clock_domain:'audio_context_samples',observation:'observed_render_graph_input'});
        captureBaseFrame=data.frame+samples.length-leadFrames;interrupt();return;
      }
      if(captureBaseFrame===undefined){captureBaseFrame=data.frame;trace('capture_start',{frame:data.frame,context_time:data.contextTime,sample_rate:context!.sampleRate});}
      if(data.detectedFrame!==null&&!voiced){voiced=true;clearTimeout(silenceTimer);trace('speech_onset',{onset_frame:data.onsetFrame,detection_frame:data.detectedFrame,sample_rate:context!.sampleRate,observation:'estimated_energy_endpoint'});}
      if(data.lastVoiceFrame!==null)lastVoice=data.lastVoiceFrame;
      upload(captureResampler!.push(samples));
      if(data.endpoint){trace('endpoint_decision',{last_voiced_frame:lastVoice,decision_frame:data.endpointFrame,hangover_ms:600,domain:'audio_context_samples'});commit();}
    }else if(data.type==='overflow'||data.type==='invalid_audio'||data.type==='invalid_fence')fail('audio_contract',data.type);
    else if(data.type==='fence_applied'){
      trace('fence_applied',data);
      if(pendingCancel&&data.epoch===pendingCancel.epoch&&data.connection===connection){
        const canceled=pendingCancel;pendingCancel=undefined;
        const ack=(data.acks as {responseId:string;through:number}[]).find(item=>item.responseId===canceled.responseId);
        const cutoff=ack?.through??0;
        trace('cancel_dispatch',{response_id:canceled.responseId,rendered_through_sample:cutoff,fence_context_time:data.contextTime,acoustic_tail:'unknown'});
        send({type:'interrupt',response_epoch:epoch,rendered_through_sample:cutoff});
        const lead=captureLead,base=captureBaseFrame;listen();captureBaseFrame=base;
        for(const chunk of lead)upload(captureResampler!.push(chunk));
      }
    }else if(data.type==='rendered'){
      if(data.epoch!==epoch||data.connection!==connection||data.responseId!==activeResponse){trace('stale_render_ack_discarded',{response_id:data.responseId});return;}
      playbackQueued=Math.max(0,playbackQueued-Number(data.consumedFrames));lastAck={response_id:data.responseId,rendered_through_sample:data.through};
      send({type:'rendered',...lastAck});trace('rendered_ack',{...lastAck,context_time:data.contextTime,frame:data.frame,epoch,connection,quantum:data.quantum,queued_frames:data.queuedFrames,boundary:'worklet_render_graph',physical_output:'unknown'});
    }else if(data.type==='drained'){
      if(data.epoch!==epoch||data.connection!==connection||data.responseId!==activeResponse)return;
      trace('response_render_drained',{response_id:activeResponse,frame:data.frame});stopCapture();activeResponse=undefined;responseSamples=0;
      setState('awaiting_response','Response graph drained. Explicit retry starts the next turn.');
    }else if(data.type==='render_onset'){
      if(data.epoch!==epoch||data.connection!==connection||data.responseId!==activeResponse)return;
      const stamp=context!.getOutputTimestamp();
      const valid=Number.isFinite(stamp.performanceTime)&&Number.isFinite(stamp.contextTime)&&stamp.performanceTime!>0;
      const estimate=valid?stamp.performanceTime!+(data.contextTime-stamp.contextTime!)*1000:null;
      const timing={boundary:'browser_output_timestamp_estimate',observation:valid?'estimated':'unknown',performance_ms:estimate,context_seconds:data.contextTime,detected_frame:data.detectedFrame,window_ms:10,sustain_ms:20,minimum_hot_samples:data.minimumHotSamples,threshold_dbfs:-45,output_latency_seconds:context!.outputLatency??null,acoustic_uncertainty:'unknown',calibration:'unmeasured'};
      el('timing').textContent=JSON.stringify(timing,null,2);trace('render_onset_estimate',timing);
    }else trace(data.type,data);
  }catch(error){fail('audio_failure',String(error));}};
  setState('connecting',`Opening local ${mode} relay; server admission gates apply.`);url.searchParams.set('nonce',nonce);if(resuming&&retainedSession)url.searchParams.set('session_id',retainedSession);socket=new WebSocket(url);socket.onopen=()=>{if(own!==token)return;send({type:'hello',protocol_version:1,mode,arm,nonce});};socket.onmessage=event=>{if(own!==token)return;try{receive(String(event.data));}catch(error){fail('protocol_error',String(error));}};socket.onerror=()=>{if(own===token)fail('transport_error','Relay connection failed. No automatic capture restart.');};socket.onclose=()=>{if(own!==token)return;stopCapture();releaseAudio();disconnectedAt=performance.now();setState('disconnected','Transport lost. Explicit retry within five seconds attempts bounded resume; prior audio is never replayed.');};
}catch(error){if(own===token)fail(error instanceof DOMException&&error.name==='NotAllowedError'?'permission_denied':'startup_failure',String(error));}}
el<HTMLSelectElement>('mode').onchange=()=>{const select=el<HTMLSelectElement>('mode');if(!['idle','closed','failed','permission_denied','device_lost','silence_timeout','disconnected'].includes(state)){select.value=mode;return;}++token;mode=select.value==='live'?'live':'mock';document.querySelectorAll<HTMLButtonElement>('[data-arm]').forEach(button=>{button.querySelector('small')!.textContent=mode==='live'?'Operator capability gate · unverified':button.dataset.arm==='V'?'Wire mock':button.dataset.arm==='C'?'STT → controller → TTS mock':'No generation';});retainedSession=undefined;el('live-consent-label').hidden=mode!=='live';el<HTMLInputElement>('live-consent').checked=false;el<HTMLInputElement>('text').disabled=mode==='live';el('stimulus').querySelector('button')!.disabled=mode==='live';el('evidence').textContent=mode==='live'?'LIVE · GATED / UNVERIFIED. No live activation is authorized by this UI. Separate operator authorization, capabilities, prices and matching relay mode are required; runtime and acoustic behavior remain unverified.':'KEYLESS MOCK · Source construction / runtime unverified. Synthetic tones and typed stimuli cannot establish spoken-task correctness or acoustic latency.';trace('mode_selected',{mode,admission:'server_required'});};
el('start').onclick=()=>void start();el('end').onclick=end;el('interrupt').onclick=interrupt;el('disconnect').onclick=()=>{stopCapture();socket?.close();};el('retry').onclick=()=>{if(['playing','awaiting_response','listening'].includes(state))interrupt();else void start();};
document.querySelectorAll<HTMLButtonElement>('[data-arm]').forEach(button=>button.onclick=()=>{if(!['idle','closed','failed','disconnected'].includes(state))return;arm=button.dataset.arm!;document.querySelectorAll('.arm').forEach(b=>b.classList.toggle('selected',b===button));});
el('stimulus').onsubmit=event=>{event.preventDefault();const input=el<HTMLInputElement>('text');if(!input.value.trim())return;try{conversation(`Typed diagnostic: ${input.value}`,'user');commit(input.value);input.value='';}catch(error){fail('stimulus_failure',String(error));}};
el('download').onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify({schema_version:1,domain:'browser_performance_ms',evidence:'diagnostic_unvalidated',...browserDiagnostics.drain()},null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='voice-lab-browser-trace.json';link.click();URL.revokeObjectURL(url);};
window.addEventListener('pagehide',end);

// D7's read-only hooks expose a closed metadata allowlist, never input values,
// relay text/payloads, nonce, raw PCM, transcripts, tracks or socket objects.
const browserDiagnostics=Object.freeze({
  snapshot(){return {schema_version:1,state,mode,arm,at:performance.now(),clock_domain:'browser_performance_ms',
    owned_handles:{media_tracks:stream?.getTracks().filter(t=>t.readyState==='live').length??0,
      audio_context:context?1:0,worklet:node?1:0,source:source?1:0,socket:socket?1:0,
      timer_references:Number(silenceTimer!==undefined)+Number(sessionTimer!==undefined)},
    queue_seconds:{playback:context?playbackQueued/context.sampleRate:null,
      upload:socket?socket.bufferedAmount/64000:null},
    observation_statuses:{state:'observed',owned_handles:'estimated',playback_queue:context?'estimated':'unknown',
      upload_queue:socket?'estimated':'unknown',physical_audio:'unknown',calibration:'unknown',cleanup:'unknown'},
    limitations:['Handle metrics count current references; pending closure/permission acquisition is not yet tracked.',
      'Upload seconds conservatively approximate PCM16 base64 payload; includes JSON overhead.',
      'Playback queue is main-thread admission minus worklet-consumed frames; no physical-device claim.'],
    trace_cursor:diagnosticSequence,trace_dropped:diagnosticDropped};},
  drain(afterSeq=0,limit=500){
    if(!Number.isSafeInteger(afterSeq)||afterSeq<0||!Number.isSafeInteger(limit)||limit<1||limit>1000)throw new Error('Invalid diagnostic cursor/limit');
    const first=diagnosticEvents[0]?.seq??diagnosticSequence+1;
    const events=diagnosticEvents.filter(e=>e.seq>afterSeq).slice(0,limit).map(e=>({...e,detail:{...e.detail}}));
    return {schema_version:1,events,cursor:events.at(-1)?.seq??afterSeq,latest_seq:diagnosticSequence,
      oldest_seq:first,dropped_before_cursor:Math.max(0,first-afterSeq-1),dropped_total:diagnosticDropped,
      observation:'diagnostic_unvalidated',raw_audio:false,transcripts:false};
  }
});
declare global {interface Window {readonly __voiceLabDiagnostics:typeof browserDiagnostics}}
Object.defineProperty(window,'__voiceLabDiagnostics',{value:browserDiagnostics,writable:false,configurable:false});
