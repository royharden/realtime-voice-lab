import './style.css';
import {Resampler, AUDIO_POLICY, encodePCM, decodePCM} from './audio';
import type {ResampledBlock} from './audio';
import {OwnedResources} from './resources';
import {ClockAlignment} from './clock';
type State = 'idle'|'requesting_permission'|'connecting'|'listening'|'awaiting_response'|'playing'|'interrupting'|'disconnected'|'reconnecting'|'ending'|'closed'|'permission_denied'|'device_lost'|'silence_timeout'|'unsupported_audio'|'device_muted'|'recovery_unknown'|'calibrating'|'failed';
type Wire = Record<string, unknown> & {type:string};
const app = document.querySelector<HTMLDivElement>('#app')!;
app.innerHTML = `<header><a class="brand" href="#">◉ <span>VOICE LAB</span></a><span class="pill">LOCAL EXPERIMENT · M6</span></header>
<main><section class="intro"><p class="eyebrow">REALTIME SYSTEMS / COMPARATIVE WORKBENCH</p><h1>Listen to the whole path.</h1><p>One fictional booking task. Three audio pipelines. Every interruption, uncertain boundary, and committed effect stays visible.</p></section>
<div class="notice" id="evidence"><b>KEYLESS MOCK</b> Source construction · runtime unverified. Synthetic tones are diagnostic signals; they cannot establish spoken-task correctness or acoustic latency.</div>
<div class="workspace"><section class="card session"><div class="section-head"><h2>Session console</h2><span id="state" class="pill">idle</span></div><div class="arms" role="group" aria-label="Pipeline"><button class="arm selected" data-arm="V"><b>V</b><span>Native voice<small>Wire mock</small></span></button><button class="arm" data-arm="C"><b>C</b><span>Deterministic cascade<small>STT → controller → TTS mock</small></span></button><button class="arm" data-arm="K"><b>K</b><span>Canned control<small>No generation</small></span></button></div>
<div class="mode-controls"><label>Session mode<select id="mode"><option value="mock" selected>Mock · keyless diagnostic</option><option value="live">Live · gated / unverified / separate authorization required</option></select></label><label id="live-consent-label" hidden><input id="live-consent" type="checkbox"> I understand live may incur costs and requires separately authorized operator manifests. This acknowledgement does not authorize or activate a provider.</label></div><label>Local relay URL<input id="url" placeholder="ws://127.0.0.1:REGISTERED_PORT/ws" spellcheck="false"></label><label>Session nonce<input id="nonce" type="password" autocomplete="off" placeholder="Use the nonce printed by your local relay"></label>
<div class="scope"><span id="mic" class="dot"></span><div><strong id="headline">Ready when you are</strong><p id="detail">Start requests microphone permission. End releases audio and transport.</p></div><div class="signal" aria-hidden="true">▂ ▄ ▆ ▃ ▇ ▄ ▂</div></div>
<div class="actions"><button id="start" class="primary">Start session</button><button id="end">End</button><button id="interrupt">Interrupt</button><button id="disconnect">Disconnect</button><button id="retry">Explicit retry</button></div><p id="error" role="alert"></p><details><summary>Owned resources and cleanup</summary><pre id="resources">No handles acquired.</pre></details>
<details><summary>Mock typed stimulus · diagnostic only</summary><p>Typed input bypasses speech interpretation and cannot pass speech adjudication.</p><form id="stimulus"><input id="text" aria-label="Mock typed stimulus" placeholder="Ask opening hours or available slots"><button>Send diagnostic turn</button></form></details>
<div class="section-head"><h3>Conversation</h3><span>Fictional service · English</span></div><div id="conversation" class="conversation"><p class="empty">Transcripts and explicit responses appear here.</p></div></section>
<aside><section class="card"><p class="eyebrow">AUTHORITATIVE EFFECT</p><h2>Task journal</h2><pre id="task">No task event received.</pre><h3>Session outcome and evidence</h3><pre id="terminal">No terminal observation.</pre><p class="muted">An interruption does not undo a committed booking. The relay journal owns the effect.</p></section><section class="card"><p class="eyebrow">MEASUREMENT BOUNDARY</p><h2>Timing inspector</h2><dl><dt>Context sample rate</dt><dd id="rate">Unknown</dd><dt>Wire PCM</dt><dd>24,000 Hz · mono PCM16</dd><dt>Render boundary</dt><dd>Output timestamp estimate</dd><dt>Acoustic calibration</dt><dd>Unmeasured</dd><dt>Latency / p50 / p95</dt><dd>Not evaluated</dd></dl><pre id="timing">No rendered sample observed.</pre></section><section class="card"><p class="eyebrow">ALL ATTEMPTS</p><h2>Cost & completeness</h2><pre id="cost">Unknown until relay accounting arrives.</pre><p class="muted">Mock is not a live price or billing certification.</p></section></aside></div>
<section class="card trace"><div class="section-head"><div><p class="eyebrow">BROWSER PERFORMANCE CLOCK · MILLISECONDS</p><h2>Lifecycle trace</h2></div><button id="download">Export local trace</button></div><p class="muted">Receipt, endpoint decision, commit, worklet fence and rendered ACK are separate events. Safe export retains up to 1,000 metadata events with explicit sequence gaps. No raw audio, transcripts or nonce are exported.</p><div id="trace" role="log" aria-live="off"></div></section><footer>V / C / K compare pinned pipelines. No arm is presumed faster. Live requires separately authorized relay admission; no live execution is established.</footer></main>`;
const el = <T extends HTMLElement = HTMLElement>(id:string) => document.getElementById(id) as T;
let mode:'mock'|'live'='mock';
type ClientOutcome='permission_denied'|'device_lost'|'silence_timeout'|'abandoned'|'unsupported_audio';
let resources:OwnedResources|undefined;const ownedScopes=new Set<OwnedResources>();
let admitted=false,awaitingResume=false,repeatRequests=0,taskDeadline=0,connectionReady=false;
let silenceTimer:ReturnType<typeof setTimeout>|undefined,sessionTimer:ReturnType<typeof setTimeout>|undefined,
  responseTimer:ReturnType<typeof setTimeout>|undefined,recoveryTimer:ReturnType<typeof setTimeout>|undefined,
  connectTimer:ReturnType<typeof setTimeout>|undefined,fenceTimer:ReturnType<typeof setTimeout>|undefined,
  clockTimer:ReturnType<typeof setTimeout>|undefined,probeTimer:ReturnType<typeof setTimeout>|undefined;
let clockAlignment=new ClockAlignment(),clockSequence=0,clockEpoch=0,clockBootId:string|undefined,responseDeadline=0,repeatNext=false,responseDeadlineBasis:'speech_end_estimate'|'typed_debug'|'repeat_attempt_only'|'unknown'='unknown';
let graphCounter=0,graphId=0,probeSequence=0;
let activeProbe:number|undefined,probeCount=0,probeRequest:Record<string,number|string|null>|undefined,lastProbe:Record<string,unknown>|undefined;
const probeEvents:Record<string,number|string|null>[]=[];
let workletQuantum:number|null=null,workletQueuedFrames:number|null=null;
function ownedHandles(){
  const result={media_tracks:0,audio_context:0,worklet:0,source:0,socket:0,timers:0,raf:0,object_urls:0,listeners:0,pending_permission:0,pending_context_close:0,cleanup_errors:0};
  for(const scope of ownedScopes)for(const key of Object.keys(result) as (keyof typeof result)[])result[key]+=scope.snapshot()[key];
  return result;
}
function cleanupPending(){return [...ownedScopes].some(scope=>scope.canceled&&!scope.settled);}
function resourcesChanged(){
  for(const scope of ownedScopes)if(scope.settled)ownedScopes.delete(scope);
  const handles=ownedHandles();el('mic').classList.toggle('live',handles.media_tracks>0);
  el('resources').textContent=JSON.stringify({handles,status:cleanupPending()?'cleanup_pending':Object.values(handles).every(n=>n===0)?'observed_released':'active',physical_device:'unknown'},null,2);
  updateControls();
  el('mic').title=handles.media_tracks?'Microphone track active':'No owned active microphone track';
  if(state==='ending'&&Object.values(handles).every(n=>n===0))setState('closed','All tracked resources released. Physical-device timing remains unknown.');
}
function newResources(){const scope=new OwnedResources(resourcesChanged);ownedScopes.add(scope);return scope;}
function updateControls(){
  const fresh=['idle','closed','failed','permission_denied','device_lost','device_muted','silence_timeout','recovery_unknown'].includes(state);
  el<HTMLButtonElement>('start').disabled=(!fresh&&state!=='disconnected')||cleanupPending();
  el<HTMLButtonElement>('retry').disabled=cleanupPending()||!['listening','playing','awaiting_response','disconnected','failed','permission_denied','device_lost','device_muted','silence_timeout','recovery_unknown'].includes(state);
  el<HTMLButtonElement>('interrupt').disabled=!['listening','playing','awaiting_response'].includes(state);
  el<HTMLButtonElement>('disconnect').disabled=!admitted||awaitingResume;
  el<HTMLSelectElement>('mode').disabled=!fresh||cleanupPending();
  document.querySelectorAll<HTMLButtonElement>('[data-arm]').forEach(b=>{b.disabled=!fresh||cleanupPending();});
}
function clearTimer(id:ReturnType<typeof setTimeout>|undefined){resources?.clearTimer(id);}

let retainedSession:string|undefined, disconnectedAt=0, reconnects=0, resuming=false;
let state:State='idle', arm='V', socket:WebSocket|undefined, context:AudioContext|undefined, stream:MediaStream|undefined, node:AudioWorkletNode|undefined, source:MediaStreamAudioSourceNode|undefined;
let captureResampler:Resampler|undefined, playbackResampler:Resampler|undefined;
let connection=0, epoch=0, turn=0, transportCounter=0,currentTransportTurn:string|undefined,sequence=0, token=0, activeResponse:string|undefined, responseSamples=0, expectedSequence=-1;
let lastVoice=0, voiced=false, captureBaseFrame:number|undefined, captureLead:Float32Array[]=[], leadFrames=0, playbackDone=false, playbackQueued=0,
  pendingCancel:{responseId:string|undefined;epoch:number;minimumCutoff:number}|undefined;
type RenderedAck={response_id:string;rendered_through_sample:number};
let lastAck:RenderedAck|undefined,cancelAck:RenderedAck|undefined;
type DiagnosticEvent={seq:number;at:number;kind:string;detail:Record<string,number|string|boolean|null>};
const diagnosticEvents:DiagnosticEvent[]=[];let diagnosticSequence=0,diagnosticDropped=0;
const diagnosticKinds=new Set(['state','capture_start','speech_onset','endpoint_decision','commit_dispatch','capture_sample_map','playback_sample_map','first_or_next_receipt','barge_in_detected','cancel_fence_dispatched','fence_applied','cancel_dispatch','rendered_ack','render_onset_estimate','response_source_complete','response_render_drained','stale_audio_discarded','stale_render_ack_discarded','audio_sequence_gap','audio_rates','device_mute','device_change_observed','client_observation_local','client_status_dispatch','client_status_not_dispatched','relay_terminal','repeat_requested','clock_alignment_probe','clock_probe_missing','clock_probe_rejected','probe_requested','probe_started','probe_pulse','probe_done','probe_canceled','probe_fence_applied','probe_rejected','probe_cancel_dispatch','response_deadline']);
const numericDiagnosticKeys=new Set(['frame','contextTime','context_time','context_seconds','source_start','inputStart','outputStart','inputPerOutput','inputLimit','delaySeconds','delayInputSamples','performance_ms','receipt_ms','epoch','connection','queued_frames','queuedFrames','quantum','rendered_through_sample','decision_frame','last_voiced_frame','last_voiced_context_frame','hangover_ms','onset_frame','detection_frame','detected_frame','sample_rate','context_sample_rate','input_rate','output_rate','device_frame_base','droppedFrames','lastOldRenderedFrame','fence_context_time','turn','wire_samples','window_ms','sustain_ms','threshold_dbfs','minimum_hot_samples','output_latency_seconds','probe_id','requested_frame','gap_ms','offset_ms','id','requestedFrame','firstFrame','lastFrame','sampleRate','nonSilentFrames','pulse','gapFrames','count','sequence','device_sample_rate','active_tracks','browser_send_ms','browser_receive_ms','server_receive_ns','server_send_ns','rtt_ms','offset_ms','uncertainty_ms','graph_id','pulse_frames','gap_frames','intended_cutoff_frame','first_frame','last_frame','last_old_frame','non_silent_frames','cancel_after_frames','output_timestamp_context_seconds','output_timestamp_performance_ms','output_estimate_ms','server_received_ns','server_sent_ns','deadline_ms']);
function diagnosticTrace(at:number,kind:string,raw:unknown){
  if(!diagnosticKinds.has(kind))return;
  const detail:DiagnosticEvent['detail']={graph_id:context?graphId:null};
  if(raw&&typeof raw==='object')for(const [key,value] of Object.entries(raw)){
    if(numericDiagnosticKeys.has(key)&&(value===null||typeof value==='number'&&Number.isFinite(value)))detail[key]=value;
    if(key==='next'&&typeof value==='string'&&['idle','requesting_permission','connecting','listening','awaiting_response','playing','interrupting','disconnected','reconnecting','ending','closed','permission_denied','device_lost','silence_timeout','unsupported_audio','device_muted','recovery_unknown','calibrating','failed'].includes(value))detail.state=value;
    if(key==='basis'&&['speech_end_estimate','typed_debug','repeat_attempt_only','unknown'].includes(String(value)))detail.basis=String(value);
    if(key==='original_boundary'&&['unknown','estimated'].includes(String(value)))detail.original_boundary=String(value);
    if(key==='sample_id'&&typeof value==='string'&&/^synthetic440-g[0-9]+-p[0-9]+$/.test(value))detail.sample_id=value;
    if(key==='cancel_origin'&&['scheduled_worklet','explicit_main_thread','session_fence'].includes(String(value)))detail.cancel_origin=String(value);
    if(key==='mapping_observation'&&['estimated','unknown'].includes(String(value)))detail.mapping_observation=String(value);
    if(key==='receipt'&&['not_observed','unknown','observed'].includes(String(value)))detail.receipt=String(value);
    if(key==='muted'&&typeof value==='boolean')detail.muted=value;
    if(key==='outcome'&&['permission_denied','device_lost','silence_timeout','abandoned','unsupported_audio'].includes(String(value)))detail.outcome=String(value);
    if(key==='boundary'&&['worklet_render_graph','browser_output_timestamp_estimate'].includes(String(value)))detail.boundary=String(value);
    if(key==='observation'&&['observed','estimated','unknown','estimated_energy_endpoint','observed_render_graph_input'].includes(String(value)))detail.observation=String(value);
  }
  diagnosticEvents.push({seq:++diagnosticSequence,at,kind,detail});
  if(diagnosticEvents.length>1000){diagnosticEvents.shift();diagnosticDropped++;}
}
const traces:{at:number;kind:string;detail:unknown}[]=[];
function trace(kind:string, detail:unknown={}) { const item={at:performance.now(),kind,detail}; diagnosticTrace(item.at,kind,detail);traces.push(item); if(traces.length>500)traces.shift(); const row=document.createElement('div'); row.textContent=`${item.at.toFixed(1).padStart(10)}  ${kind}  ${JSON.stringify(detail)}`; el('trace').prepend(row); while(el('trace').children.length>100)el('trace').lastElementChild?.remove(); }
function setState(next:State, detail='') {state=next; el('state').textContent=next; el('headline').textContent=next.replaceAll('_',' '); el('detail').textContent=detail; el('mic').classList.toggle('live',ownedHandles().media_tracks>0); trace('state',{next,detail});updateControls();}
function send(message:Wire) {if(socket?.readyState!==WebSocket.OPEN)throw new Error('Transport is not open');const encoded=JSON.stringify(message);if(socket.bufferedAmount+new TextEncoder().encode(encoded).byteLength>128000)throw new Error('Upload queue exceeds two-second PCM envelope');socket.send(encoded);}
function stopCapture(){node?.port.postMessage({type:'capture',enabled:false});clearTimer(silenceTimer);silenceTimer=undefined;}
function fence(){epoch++;playbackQueued=0;node?.port.postMessage({type:'fence',epoch,connection});activeResponse=undefined;responseSamples=0;expectedSequence=-1;playbackResampler=context?new Resampler(24000,context.sampleRate):undefined;trace('cancel_fence_dispatched',{epoch,connection});}
function releaseAudio(){
  if(activeProbe!==undefined){lastProbe={...probeRequest,observation:'incomplete',boundary:'worklet_render_graph',physical_audio:'unknown',calibration:'unknown',reason:'graph_released_before_terminal',events:probeEvents.map(e=>({...e}))};}
  for(const scope of ownedScopes)scope.close();stream=undefined;context=undefined;node=undefined;source=undefined;socket=undefined;
  captureResampler=undefined;playbackResampler=undefined;captureLead=[];leadFrames=0;playbackQueued=0;
  pendingCancel=undefined;activeProbe=undefined;admitted=false;connectionReady=false;workletQuantum=null;workletQueuedFrames=null;
  silenceTimer=sessionTimer=responseTimer=recoveryTimer=connectTimer=fenceTimer=clockTimer=probeTimer=undefined;
}
function reportClient(outcome:ClientOutcome){
  if(!admitted||awaitingResume||socket?.readyState!==WebSocket.OPEN){trace('client_observation_local',{outcome,receipt:'not_observed'});return;}
  try{send({type:'client_status',outcome});trace('client_status_dispatch',{outcome,receipt:'unknown'});}catch{trace('client_status_not_dispatched',{outcome});}
}
function fail(code:string,message:string){
  if(code==='transport_error'&&resuming&&retainedSession){lostTransport();return;}
  if(['permission_denied','device_lost','silence_timeout','unsupported_audio'].includes(code))reportClient(code as ClientOutcome);
  if(code==='device_muted')reportClient('device_lost');
  el('terminal').textContent=JSON.stringify({observation:'local_browser',code,relay_receipt:'unknown'},null,2);el('error').textContent=`${code}: ${message}`;trace('failure',{code,message});++token;releaseAudio();retainedSession=undefined;awaitingResume=false;
  const named:State[]=['permission_denied','silence_timeout','device_lost','device_muted','unsupported_audio','recovery_unknown'];
  setState(named.includes(code as State)?code as State:'failed',message);
}
function end(){
  ++token;setState('ending','Releasing resources. A pending permission prompt must resolve before cleanup can finish.');
  el('terminal').textContent=JSON.stringify({observation:'local_browser',outcome:'abandoned',relay_receipt:'unknown'},null,2);reportClient('abandoned');if(!admitted&&socket?.readyState===WebSocket.OPEN)try{send({type:'end'});}catch{trace('end_send_failure');}
  fence();retainedSession=undefined;awaitingResume=false;releaseAudio();resourcesChanged();
}
function lostTransport(){
  const canResume=!!retainedSession&&(admitted||resuming);
  ++token;clockAlignment.expirePending();releaseAudio();
  if(!canResume){fail('transport_error','Relay admission did not complete; explicit retry starts a new session.');return;}
  if(!disconnectedAt)disconnectedAt=performance.now();
  const remaining=5000-(performance.now()-disconnectedAt);
  if(remaining<=0||reconnects>=2){fail('recovery_unknown','Reconnect window or two-attempt limit exhausted. End or explicitly start a new session.');return;}
  resources=newResources();setState('disconnected','Transport lost. Explicit retry only: 250 ms then 1 s backoff, at most two attempts within the original five seconds. No audio replay.');
  recoveryTimer=resources.timer(()=>fail('recovery_unknown','Five-second recovery window expired; journal effects are not rolled back.'),remaining);
}
function listen(newTurn=true){if(newTurn){currentTransportTurn=undefined;sequence=0;if(!repeatNext){responseDeadline=0;responseDeadlineBasis='unknown';}}stopCapture();captureResampler=context?new Resampler(context.sampleRate,24000):undefined;captureBaseFrame=undefined;captureLead=[];leadFrames=0;setState('listening','Speak now. Energy endpoint: 600 ms hangover; eight-second silence limit.');voiced=false;lastVoice=0;node?.port.postMessage({type:'capture',enabled:true});clearTimer(silenceTimer);silenceTimer=resources!.timer(()=>{if(state==='listening')fail('silence_timeout','No completed speech turn. Choose explicit retry or End.');},8000);}
function commit(text?:string){if(text&&mode!=='mock')throw new Error('Typed debug input is unavailable in live mode');if(turn>=8)throw new Error('Eight logical turn limit reached');if(state!=='listening')throw new Error('A turn requires the listening state');if(!repeatNext||responseDeadline<=0){responseDeadlineBasis=repeatNext?'repeat_attempt_only':text?'typed_debug':'speech_end_estimate';responseDeadline=performance.now()+10000-(!text&&lastVoice>0&&context?Math.max(0,(context.currentTime-lastVoice/context.sampleRate)*1000):0);}trace('response_deadline',{deadline_ms:responseDeadline,basis:responseDeadlineBasis,original_boundary:responseDeadlineBasis==='repeat_attempt_only'?'unknown':'estimated'});repeatNext=false;if(responseDeadline<=performance.now())throw new Error('Original response deadline has expired');stopCapture();playbackDone=false;if(!text&&captureResampler)upload(captureResampler.flush());voiced=false;const transportId=ensureTransportTurn();turn++;cancelAck=undefined;trace('commit_dispatch',{turn,last_voiced_context_frame:lastVoice,context_sample_rate:context?.sampleRate,clock_domain:'audio_context_samples',policy:text?'typed_mock':'energy_600ms'});send({type:'commit',turn_id:transportId,connection_epoch:connection,response_epoch:epoch,...(text?{text}:{})});sequence=0;lastAck=undefined;setState('awaiting_response','Waiting for relay response; capture is paused until explicit next turn.');clearTimer(responseTimer);responseTimer=resources!.timer(()=>fail('response_timeout','Ten-second response deadline expired; no success is inferred.'),Math.max(1,responseDeadline-performance.now()));}
function ensureTransportTurn(){if(currentTransportTurn===undefined){if(transportCounter>=8)throw new Error('Eight started transport-turn limit reached');currentTransportTurn=String(++transportCounter);lastAck=undefined;}return currentTransportTurn;}
function upload(block:ResampledBlock){
  if(!block.samples.length)return;
  trace('capture_sample_map',{...block.map,device_frame_base:captureBaseFrame,input_rate:context?.sampleRate,output_rate:24000});
  // Wire parser bounds each PCM frame to 4,800 samples (200 ms).
  for(let offset=0;offset<block.samples.length;offset+=4800)send({type:'audio',turn_id:ensureTransportTurn(),connection_epoch:connection,response_epoch:epoch,sequence:sequence++,sample_rate:24000,pcm16:encodePCM(block.samples.subarray(offset,offset+4800))});
}
function queuePlayback(block:ResampledBlock){if(!block.samples.length)return;trace('playback_sample_map',{...block.map,input_rate:24000,output_rate:context?.sampleRate});if(playbackQueued+block.samples.length>context!.sampleRate*AUDIO_POLICY.queueSeconds)throw new Error('Playback admission exceeds two seconds');playbackQueued+=block.samples.length;node?.port.postMessage({type:'audio',samples:block.samples,map:block.map,epoch,connection,responseId:activeResponse});}
function finishPlayback(){
  if(!activeResponse||!playbackResampler||playbackDone)return;
  playbackDone=true;queuePlayback(playbackResampler.flush());
  node?.port.postMessage({type:'drain',responseId:activeResponse,epoch,connection});
  trace('response_source_complete',{response_id:activeResponse,wire_samples:responseSamples});
}
function interrupt(){
  if(!node||pendingCancel||!['playing','awaiting_response','listening'].includes(state))return;clearTimer(responseTimer);
  const responseId=activeResponse??cancelAck?.response_id;setState('interrupting','Applying worklet fence before cancellation and capture restart.');
  pendingCancel={responseId,epoch:epoch+1,minimumCutoff:cancelAck&&cancelAck.response_id===responseId?cancelAck.rendered_through_sample:0};fence();fenceTimer=resources!.timer(()=>fail('audio_failure','Worklet fence acknowledgement timed out.'),1000);
}
function conversation(text:string,kind:string){const row=document.createElement('p');row.className=kind;row.textContent=text;el('conversation').querySelector('.empty')?.remove();el('conversation').append(row);while(el('conversation').children.length>100)el('conversation').firstElementChild?.remove();}
function receive(raw:string){if(raw.length>300000)throw new Error('Message bound exceeded');const envelope=JSON.parse(raw) as Wire;if(!envelope||typeof envelope.type!=='string'||envelope.protocol_version!==1||!envelope.payload||typeof envelope.payload!=='object')throw new Error('Malformed protocol');const m={...envelope,...(envelope.payload as Record<string,unknown>)} as Wire;trace('relay_'+m.type,m.type==='audio'?{sequence:m.sequence,response_id:m.response_id}:m);
  if(m.type==='ready'){
    if(connectionReady||m.mode!==mode||m.arm!==arm||typeof m.session_id!=='string'||!Number.isSafeInteger(m.connection_epoch)||!Number.isSafeInteger(m.response_epoch))throw new Error('Invalid or duplicate relay admission');
    clearTimer(connectTimer);connectionReady=true;admitted=true;
    el('evidence').textContent=`${mode.toUpperCase()} � ${String(m.evidence_label??'Runtime unverified')} � No independent acceptance established.`;
    if(resuming&&retainedSession!==m.session_id)throw new Error('Resumed a different session');if(typeof m.boot_id!=='string')throw new Error('Missing server boot identity');if(resuming&&m.boot_id!==clockBootId)throw new Error('Server restart cannot resume a prior session');if(!resuming){clockBootId=m.boot_id;clockAlignment=new ClockAlignment(clockBootId);clockSequence=0;clockEpoch=Number(m.connection_epoch);}retainedSession=m.session_id;
    if(m.resume_required){
      if(!resuming)throw new Error('Unexpected resume challenge');connection=Number(m.connection_epoch)+1;epoch=Number(m.response_epoch)+1;awaitingResume=true;
      send({type:'resume',connection_epoch:connection,...(lastAck?{rendered_ack:{type:'rendered',...lastAck}}:{})});
      setState('reconnecting','Resume dispatched. Awaiting journal/repeat decision; capture remains paused.');
    }else{if(resuming)throw new Error('Relay lost the requested session');connection=Number(m.connection_epoch);epoch=Number(m.response_epoch);awaitingResume=false;}
    node?.port.postMessage({type:'fence',epoch,connection});
    const remaining=taskDeadline-performance.now();if(remaining<=0){fail('task_deadline','Original 120-second task deadline expired.');return;}
    clearTimer(sessionTimer);sessionTimer=resources!.timer(()=>fail('task_deadline','Original 120-second task limit reached.'),remaining);
    if(awaitingResume)connectTimer=resources!.timer(()=>fail('recovery_unknown','Resume decision timed out.'),Math.max(1,5000-(performance.now()-disconnectedAt)));
    else{disconnectedAt=0;clearTimer(recoveryTimer);listen();beginAlignment();}return;
  }
  if(m.type==='clock_pong'){
    const received=performance.now();if(clockAlignment.accept(m,received)){clearTimer(clockTimer);trace('clock_alignment_probe',{browser_send_ms:m.browser_sent_ms,browser_receive_ms:received,server_received_ns:m.server_received_ns,server_sent_ns:m.server_sent_ns,...clockAlignment.summary().samples.at(-1)});nextClockProbe();}else trace('clock_probe_rejected');return;
  }
  if(m.type==='error'){fail(String(m.code),String(m.message));return;}
  if(m.type==='ended'){
    trace('relay_terminal',{outcome:m.outcome,outcome_source:m.outcome_source,receipt:'observed'});
    el('detail').textContent=`Relay terminal: ${String(m.outcome??'unknown')} (${String(m.outcome_source??'unknown source')})`;
    el('terminal').textContent=JSON.stringify({outcome:m.outcome??null,outcome_source:m.outcome_source??null,evidence_reference:m.evidence_reference??null,trace:m.trace??null,receipt:'observed_relay',accounting_status:'see current cost panel and authenticated session evidence'},null,2);
    ++token;setState('ending','Relay ended the session; releasing owned resources.');retainedSession=undefined;awaitingResume=false;releaseAudio();resourcesChanged();return;
  }
  if(m.type==='task'){el('task').textContent=JSON.stringify(m,null,2);return;}
  if(m.type==='transcript'){conversation(String(m.text??''),String(m.role??'assistant'));return;}
  if(m.type==='event'){if(m.kind==='repeat_requested'){repeatRequests++;repeatNext=true;if(repeatRequests>1){fail('recovery_unknown','A second ambiguous recovery cannot replay a request.');return;}trace('repeat_requested',{count:repeatRequests});el('detail').textContent='Journal retained. Repeat your last request explicitly; no prior audio was replayed.';}if(m.kind==='response_done'){finishPlayback();}if(m.accounting)el('cost').textContent=JSON.stringify(m.accounting,null,2);return;}
  if(m.type==='state'){if(m.task&&typeof m.task==='object')el('task').textContent=JSON.stringify(m.task,null,2);el('detail').textContent=String(m.state??m.message??'Relay state event');if(m.state==='listening'&&awaitingResume){awaitingResume=false;resuming=false;disconnectedAt=0;clearTimer(recoveryTimer);clearTimer(connectTimer);listen();beginAlignment();return;}if(m.state==='listening'&&state==='playing')finishPlayback();return;}
  if(m.type==='audio'){
    if(m.connection_epoch!==connection||m.response_epoch!==epoch){trace('stale_audio_discarded');return;}
    if(m.sample_rate!==24000||typeof m.response_id!=='string'||typeof m.pcm16!=='string'||!Number.isInteger(m.sequence))throw new Error('Invalid audio contract');
    if(activeResponse&&activeResponse!==m.response_id)throw new Error('Concurrent response refused');
    if(Number(m.sequence)<=expectedSequence)throw new Error('Duplicate or reordered audio sequence');if(expectedSequence>=0&&Number(m.sequence)!==expectedSequence+1)trace('audio_sequence_gap',{previous:expectedSequence,current:m.sequence});expectedSequence=Number(m.sequence);
    if(activeResponse!==m.response_id){lastAck=undefined;playbackDone=false;playbackResampler=new Resampler(24000,context!.sampleRate);}if(playbackDone)throw new Error('Audio after response completion');activeResponse=m.response_id;const pcm=decodePCM(m.pcm16);trace('first_or_next_receipt',{response_id:activeResponse,source_start:responseSamples,receipt_ms:performance.now(),clock_domain:'browser_performance_ms'});queuePlayback(playbackResampler!.push(pcm));responseSamples+=pcm.length;setState('playing',`${mode==='mock'?'Diagnostic mock':'Unverified live'} playback. Microphone monitors sustained onset for interruption; Interrupt is also available.`);node?.port.postMessage({type:'capture',enabled:true});
  }
}
function beginAlignment(){if(connection===clockEpoch)nextClockProbe();}
function nextClockProbe(){
  // Server enforces a combined 20-probe budget across reconnects and legacy ping.
  if(!admitted||awaitingResume||!resources||resources.canceled||clockSequence>=20||connection!==clockEpoch)return;
  const id=`clock-g${graphId}-p${clockSequence++}`,sent=performance.now();clockAlignment.begin(id,sent);
  try{send({type:'clock_probe',probe_id:id,browser_sent_ms:sent});clockTimer=resources.timer(()=>{clockAlignment.expire(id);trace('clock_probe_missing');nextClockProbe();},500);}
  catch{clockAlignment.expire(id);trace('clock_probe_missing');}
}
function alignmentSnapshot(){const summary=clockAlignment.summary();return {...summary,connection_epoch:clockEpoch,current_connection_epoch:connection,
  status:connection===clockEpoch?summary.status:'unknown',offset_server_minus_browser_ms:connection===clockEpoch?summary.offset_server_minus_browser_ms:null,
  reason:connection===clockEpoch?'current_connection':'previous_connection_samples_only_no_budget_reset'};}
function diagnosticProbe(options:{kind:'gap'|'offset'|'cancel';gap_ms?:number;offset_ms?:number;cancel_after_frames?:number}){
  if(mode!=='mock'||!admitted||awaitingResume||state!=='listening'||activeResponse||activeProbe!==undefined||!node||!context||context.state!=='running')throw new Error('Probe requires an explicitly started, admitted, idle mock graph');
  if(!options||!['gap','offset','cancel'].includes(options.kind))throw new Error('Unknown probe kind');
  const gap=options.gap_ms??100,offset=options.offset_ms??0,pulseFrames=Math.ceil(context.sampleRate*.04);
  const cancelAfter=options.kind==='cancel'?(options.cancel_after_frames??Math.ceil(context.sampleRate*.02)):null;
  if(!Number.isFinite(gap)||gap<20||gap>1000||!Number.isFinite(offset)||offset< -250||offset>250||probeCount>=128||cancelAfter!==null&&(!Number.isSafeInteger(cancelAfter)||cancelAfter<1||cancelAfter>=pulseFrames)||options.kind!=='cancel'&&options.cancel_after_frames!==undefined)throw new Error('Probe exceeds bounded controls');
  const id=++probeSequence;probeCount++;activeProbe=id;lastProbe=undefined;stopCapture();probeEvents.length=0;
  const requestedFrame=Math.round((context.currentTime+.1+offset/1000)*context.sampleRate),gapFrames=Math.round(gap*context.sampleRate/1000);
  probeRequest={schema_version:1,graph_id:graphId,probe_id:id,sample_id:`synthetic440-g${graphId}-p${id}`,sample_rate:context.sampleRate,
    requested_frame:requestedFrame,pulse_frames:pulseFrames,gap_frames:gapFrames,cancel_after_frames:cancelAfter,
    intended_cutoff_frame:cancelAfter===null?null:requestedFrame+cancelAfter,kind:options.kind};
  setState('calibrating','Synthetic render-graph diagnostic; microphone capture paused. No acoustic/calibration pass is asserted.');
  node.port.postMessage({type:'probe',id,...probeRequest});
  trace('probe_requested',probeRequest);
  probeTimer=resources!.timer(()=>{node?.port.postMessage({type:'probe_cancel',id});fail('audio_failure','Diagnostic probe completion not observed.');},3000);
  return {...probeRequest,observation:'requested',boundary:'worklet_render_graph',physical_audio:'unknown',calibration:'unknown'};
}
function cancelDiagnosticProbe(){
  if(activeProbe===undefined||!node||!context)throw new Error('No active probe');
  const intended=Math.ceil(context.currentTime*context.sampleRate);
  node.port.postMessage({type:'probe_cancel',id:activeProbe,intended_cutoff_frame:intended});trace('probe_cancel_dispatch',{probe_id:activeProbe,intended_cutoff_frame:intended});
  return {schema_version:1,graph_id:graphId,probe_id:activeProbe,intended_cutoff_frame:intended,observation:'requested',cancel_origin:'explicit_main_thread'};
}
function observeProbe(data:Record<string,unknown>){
  if(data.id!==activeProbe||data.graph_id!==graphId)return;
  const row:Record<string,number|string|null>={kind:String(data.type),graph_id:graphId,probe_id:activeProbe!,sample_id:String(probeRequest?.sample_id??'unknown')};
  for(const key of ['frame','requested_frame','pulse_frames','gap_frames','intended_cutoff_frame','first_frame','last_frame','last_old_frame','quantum','sample_rate','non_silent_frames','pulse'])if(data[key]===null)row[key]=null;else if(typeof data[key]==='number'&&Number.isFinite(data[key]))row[key]=Number(data[key]);
  if(['scheduled_worklet','explicit_main_thread','session_fence'].includes(String(data.cancel_origin)))row.cancel_origin=String(data.cancel_origin);
  let stamp:AudioTimestamp|undefined;try{stamp=context?.getOutputTimestamp();}catch{/* Missing mappings remain unknown. */}
  const valid=!!stamp&&Number.isFinite(stamp.contextTime)&&Number.isFinite(stamp.performanceTime)&&stamp.performanceTime!>0;
  row.output_timestamp_context_seconds=valid?stamp!.contextTime!:null;row.output_timestamp_performance_ms=valid?stamp!.performanceTime!:null;
  row.output_estimate_ms=valid&&typeof row.frame==='number'?stamp!.performanceTime!+(row.frame/context!.sampleRate-stamp!.contextTime!)*1000:null;
  row.mapping_observation=valid?'estimated':'unknown';
  if(probeEvents.length<128)probeEvents.push(row);trace(String(data.type),row);
  if(data.type==='probe_rejected'){clearTimer(probeTimer);activeProbe=undefined;fail('audio_failure','Worklet rejected diagnostic probe.');return;}
  if(data.type==='probe_done'||data.type==='probe_canceled'){
    clearTimer(probeTimer);lastProbe={...probeRequest,terminal_kind:data.type,observation:'observed_render_graph',boundary:'worklet_render_graph',physical_audio:'unknown',calibration:'unknown',
      observation_statuses:{render_graph:'observed',mapping:valid?'estimated':'unknown',physical_audio:'unknown',calibration:'unknown'},events:probeEvents.map(e=>({...e}))};
    activeProbe=undefined;el('timing').textContent=JSON.stringify(lastProbe,null,2);listen(false);
  }
}
async function start(){
  if(!['idle','closed','failed','permission_denied','device_lost','device_muted','silence_timeout','disconnected','recovery_unknown'].includes(state))return;
  if(cleanupPending()){el('error').textContent='Cleanup is still pending. Resolve any microphone prompt, then retry when owned handles close.';return;}
  const wasDisconnected=state==='disconnected',own=++token;
  resuming=wasDisconnected&&!!retainedSession;
  if(wasDisconnected&&(!resuming||performance.now()-disconnectedAt>=5000||reconnects>=2)){fail('recovery_unknown','Original recovery window is exhausted. End before starting a fresh task.');return;}
  if(resources&&!resources.canceled)resources.close();resources=newResources();const scope=resources;
  sessionTimer=scope.timer(()=>fail('task_deadline','Original 120-second task deadline expired.'),Math.max(1,taskDeadline?taskDeadline-performance.now():120000));
  if(resuming)recoveryTimer=scope.timer(()=>fail('recovery_unknown','Original five-second window expired during reconnect acquisition.'),Math.max(1,5000-(performance.now()-disconnectedAt)));
  if(resuming){
    reconnects++;setState('reconnecting',`Explicit reconnect attempt ${reconnects}/2. Microphone is not yet reacquired.`);
    if(!await scope.delay(reconnects===1?250:1000)||own!==token)return;
    if(performance.now()-disconnectedAt>=5000){fail('recovery_unknown','Backoff exceeded the original recovery window.');return;}
  }else{retainedSession=undefined;reconnects=0;disconnectedAt=0;repeatRequests=0;repeatNext=false;responseDeadline=0;taskDeadline=performance.now()+120000;probeCount=0;
    el('conversation').innerHTML='<p class="empty">No conversation in this session.</p>';el('terminal').textContent='No terminal observation in this session.';el('task').textContent='Awaiting this new session journal; no effect assumed.';el('cost').textContent='Unknown until this session accounting arrives.';el('timing').textContent='No rendered observation in this session; calibration unmeasured.';}
  el('error').textContent='';connectionReady=false;admitted=false;awaitingResume=false;
  try{
    const url=new URL(el<HTMLInputElement>('url').value);
    if(!['127.0.0.1','localhost','[::1]'].includes(url.hostname)||!['ws:','wss:'].includes(url.protocol)||!url.port||url.username||url.password)throw new Error('Enter a registered loopback relay URL with an explicit port');
    if(mode==='live'&&!el<HTMLInputElement>('live-consent').checked)throw new Error('Live requires explicit acknowledgement and separately authorized relay admission');
    const nonce=el<HTMLInputElement>('nonce').value;if(!nonce)throw new Error('Enter the local relay nonce');
    if(!navigator.mediaDevices?.getUserMedia||typeof AudioContext==='undefined'||typeof AudioWorkletNode==='undefined'){fail('unsupported_audio','This browser lacks the required microphone/AudioWorklet APIs.');return;}
    clearTimer(sessionTimer);sessionTimer=scope.timer(()=>fail('task_deadline','Original 120-second task deadline expired.'),Math.max(1,taskDeadline-performance.now()));
    setState('requesting_permission','Microphone is requested only by this action. End cancels this attempt; a late grant is stopped.');
    const acquired=await scope.acquireMedia(()=>navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:false,autoGainControl:false}}));
    if(own!==token||!acquired){scope.close();return;}stream=acquired;pendingCancel=undefined;playbackQueued=0;
    const audio=scope.addContext(new AudioContext());context=audio;graphId=++graphCounter;probeCount=0;
    if(!audio.audioWorklet||typeof audio.getOutputTimestamp!=='function'){fail('unsupported_audio','Audio worklets/output timestamps are unavailable.');return;}
    await audio.audioWorklet.addModule('/audio-worklet.js');if(own!==token){scope.close();return;}
    await audio.resume();if(own!==token){scope.close();return;}
    if(audio.state!=='running'){fail('unsupported_audio','Audio context did not enter running state; explicit user intervention is required.');return;}
    captureResampler=new Resampler(audio.sampleRate,24000);playbackResampler=new Resampler(24000,audio.sampleRate);
    const deviceRate=stream.getAudioTracks()[0]?.getSettings().sampleRate;
    el('rate').textContent=`${audio.sampleRate.toLocaleString()} Hz context; device ${deviceRate??'unknown'} Hz`;
    trace('audio_rates',{context_sample_rate:audio.sampleRate,device_sample_rate:deviceRate??null});
    if(!resuming){turn=0;transportCounter=0;currentTransportTurn=undefined;cancelAck=undefined;}
    source=scope.addNode(audio.createMediaStreamSource(stream));node=scope.addWorklet(new AudioWorkletNode(audio,'lab-audio',{numberOfInputs:1,numberOfOutputs:1,outputChannelCount:[1],processorOptions:{graphId}}));source.connect(node);node.connect(audio.destination);
    activeResponse=undefined;responseSamples=0;expectedSequence=-1;playbackDone=false;if(!resuming){connection=0;epoch=0;lastAck=undefined;}
    node.port.postMessage({type:'fence',epoch,connection});sequence=0;
    for(const track of stream.getTracks()){
      scope.listen(track,'ended',()=>{if(own===token)fail('device_lost','Microphone ended; explicit intervention is required.');});
      scope.listen(track,'mute',()=>{if(own===token){trace('device_mute',{muted:true});fail('device_muted','Microphone muted. Restore the device, then explicitly retry.');}});
    }
    scope.listen(navigator.mediaDevices,'devicechange',()=>{if(own===token)trace('device_change_observed',{active_tracks:ownedHandles().media_tracks});});
    scope.listen(audio,'statechange',()=>{if(own===token&&audio.state!=='running')fail('device_lost','Audio context stopped or suspended; explicit intervention required.');});
    node.onprocessorerror=()=>{if(own===token)fail('unsupported_audio','Audio worklet failed; session audio is unavailable.');};
  node.port.onmessage=({data})=>{if(own!==token)return;try{
    if(data.type.startsWith('probe_')){observeProbe(data);return;}
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
      if(data.detectedFrame!==null&&!voiced){voiced=true;clearTimer(silenceTimer);trace('speech_onset',{onset_frame:data.onsetFrame,detection_frame:data.detectedFrame,sample_rate:context!.sampleRate,observation:'estimated_energy_endpoint'});}
      if(data.lastVoiceFrame!==null)lastVoice=data.lastVoiceFrame;
      upload(captureResampler!.push(samples));
      if(data.endpoint){trace('endpoint_decision',{last_voiced_frame:lastVoice,decision_frame:data.endpointFrame,hangover_ms:600,domain:'audio_context_samples'});commit();}
    }else if(data.type==='overflow'||data.type==='invalid_audio'||data.type==='invalid_fence')fail('audio_contract',data.type);
    else if(data.type==='fence_applied'){
      trace('fence_applied',data);
      if(pendingCancel&&data.epoch===pendingCancel.epoch&&data.connection===connection){
        clearTimer(fenceTimer);const canceled=pendingCancel;pendingCancel=undefined;
        const ack=(data.acks as {responseId:string;through:number}[]).find(item=>item.responseId===canceled.responseId);
        const cutoff=Math.max(ack?.through??0,canceled.minimumCutoff);
        trace('cancel_dispatch',{response_id:canceled.responseId,rendered_through_sample:cutoff,fence_context_time:data.contextTime,acoustic_tail:'unknown'});
        send({type:'interrupt',response_epoch:epoch,rendered_through_sample:cutoff});
        const lead=captureLead,base=captureBaseFrame;listen();captureBaseFrame=base;
        for(const chunk of lead)upload(captureResampler!.push(chunk));
      }
    }else if(data.type==='rendered'){
      if(data.epoch!==epoch||data.connection!==connection||data.responseId!==activeResponse){trace('stale_render_ack_discarded',{response_id:data.responseId});return;}
      workletQuantum=Number(data.quantum);workletQueuedFrames=Number(data.queuedFrames);playbackQueued=Math.max(0,playbackQueued-Number(data.consumedFrames));lastAck={response_id:data.responseId,rendered_through_sample:data.through};cancelAck=lastAck;
      send({type:'rendered',...lastAck});trace('rendered_ack',{...lastAck,context_time:data.contextTime,frame:data.frame,epoch,connection,quantum:data.quantum,queued_frames:data.queuedFrames,boundary:'worklet_render_graph',physical_output:'unknown'});
    }else if(data.type==='drained'){
      if(data.epoch!==epoch||data.connection!==connection||data.responseId!==activeResponse)return;
      trace('response_render_drained',{response_id:activeResponse,frame:data.frame});clearTimer(responseTimer);stopCapture();activeResponse=undefined;responseSamples=0;
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

    if(own!==token)return;
    setState('connecting',`Opening local ${mode} relay; admission gates apply.`);url.searchParams.set('nonce',nonce);
    if(resuming&&retainedSession)url.searchParams.set('session_id',retainedSession);
    const transport=scope.addSocket(new WebSocket(url));socket=transport;
    connectTimer=scope.timer(()=>fail(resuming?'recovery_unknown':'transport_error','Relay connection/admission timed out.'),resuming?Math.max(1,5000-(performance.now()-disconnectedAt)):8000);
    transport.onopen=()=>{if(own!==token)return;try{send({type:'hello',protocol_version:1,mode,arm,nonce});}catch(error){fail('transport_error',String(error));}};
    transport.onmessage=event=>{if(own!==token)return;try{if(typeof event.data!=='string')throw new Error('Binary protocol refused');receive(event.data);}catch(error){fail('protocol_error',String(error));}};
    transport.onerror=()=>{if(own===token){trace('transport_error_observed');if(admitted)lostTransport();else fail('transport_error','Relay connection failed before admission.');}};
    transport.onclose=()=>{if(own===token)lostTransport();};
  }catch(error){
    if(own!==token){scope.close();return;}
    const code=error instanceof DOMException?error.name:'';
    fail(code==='NotAllowedError'||code==='SecurityError'?'permission_denied':code==='NotFoundError'||code==='NotReadableError'?'device_lost':code==='NotSupportedError'||error instanceof ReferenceError?'unsupported_audio':'startup_failure',String(error));
  }
}
el<HTMLSelectElement>('mode').onchange=()=>{const select=el<HTMLSelectElement>('mode');if(!['idle','closed','failed','permission_denied','device_lost','device_muted','silence_timeout','disconnected','recovery_unknown'].includes(state)){select.value=mode;return;}if(state==='disconnected'||cleanupPending()){select.value=mode;el('error').textContent='End the current recovery and wait for cleanup before switching mode.';return;}++token;mode=select.value==='live'?'live':'mock';document.querySelectorAll<HTMLButtonElement>('[data-arm]').forEach(button=>{button.querySelector('small')!.textContent=mode==='live'?'Operator capability gate · unverified':button.dataset.arm==='V'?'Wire mock':button.dataset.arm==='C'?'STT → controller → TTS mock':'No generation';});retainedSession=undefined;el('live-consent-label').hidden=mode!=='live';el<HTMLInputElement>('live-consent').checked=false;el<HTMLInputElement>('text').disabled=mode==='live';el('stimulus').querySelector('button')!.disabled=mode==='live';el('evidence').textContent=mode==='live'?'LIVE · GATED / UNVERIFIED. No live activation is authorized by this UI. Separate operator authorization, capabilities, prices and matching relay mode are required; runtime and acoustic behavior remain unverified.':'KEYLESS MOCK · Source construction / runtime unverified. Synthetic tones and typed stimuli cannot establish spoken-task correctness or acoustic latency.';trace('mode_selected',{mode,admission:'server_required'});};
el('start').onclick=()=>void start();el('end').onclick=end;el('interrupt').onclick=interrupt;el('disconnect').onclick=()=>{if(admitted)lostTransport();};el('retry').onclick=()=>{if(state==='awaiting_response'&&playbackDone&&!activeResponse){listen();}else if(['playing','awaiting_response','listening'].includes(state))interrupt();else void start();};
document.querySelectorAll<HTMLButtonElement>('[data-arm]').forEach(button=>button.onclick=()=>{if(!['idle','closed','failed','permission_denied','device_lost','device_muted','silence_timeout','recovery_unknown'].includes(state)||cleanupPending())return;retainedSession=undefined;arm=button.dataset.arm!;document.querySelectorAll('.arm').forEach(b=>b.classList.toggle('selected',b===button));});
el('stimulus').onsubmit=event=>{event.preventDefault();const input=el<HTMLInputElement>('text');if(!input.value.trim())return;try{conversation(`Typed diagnostic: ${input.value}`,'user');commit(input.value);input.value='';}catch(error){fail('stimulus_failure',String(error));}};
el('download').onclick=()=>{
  const downloadScope=newResources();const link=document.createElement('a');
  try{link.href=downloadScope.objectUrl(new Blob([JSON.stringify({schema_version:1,domain:'browser_performance_ms',evidence:'diagnostic_unvalidated',...browserDiagnostics.drain(0,1000)},null,2)],{type:'application/json'}));
    link.download='voice-lab-browser-trace.json';link.click();downloadScope.raf(()=>{link.remove();downloadScope.close();});
  }catch{link.remove();downloadScope.close();trace('trace_export_failed');}
};
window.addEventListener('pagehide',()=>{end();for(const scope of ownedScopes)scope.close();});

// D7's read-only hooks expose a closed metadata allowlist, never input values,
// relay text/payloads, nonce, raw PCM, transcripts, tracks or socket objects.
const browserDiagnostics=Object.freeze({
  snapshot(){return {schema_version:1,state,mode,arm,at:performance.now(),clock_domain:'browser_performance_ms',
    owned_handles:ownedHandles(),
    queue_seconds:{playback:context?playbackQueued/context.sampleRate:ownedHandles().worklet>0?null:0,upload:socket?socket.bufferedAmount/64000:ownedHandles().socket>0?null:0},
    observation_statuses:{state:'observed',owned_handles:'observed_api_state',playback_queue:context?'estimated':ownedHandles().worklet>0?'unknown_cleanup_pending':'observed_empty',
      upload_queue:socket?'estimated':ownedHandles().socket>0?'unknown_cleanup_pending':'observed_empty',physical_audio:'unknown',calibration:'unknown',
      cleanup:cleanupPending()?'pending':Object.values(ownedHandles()).every(n=>n===0)?'observed_released':'active'},
    worklet:{quantum:workletQuantum,queued_frames:workletQueuedFrames,status:workletQuantum===null?'unknown':'observed'},
    response_deadline:{browser_performance_ms:responseDeadline||null,basis:responseDeadlineBasis,original_boundary:responseDeadlineBasis==='repeat_attempt_only'?'unknown':'estimated'},
    graph_id:context?graphId:null,active_probe:activeProbe??null,clock_alignment:alignmentSnapshot(),last_probe:lastProbe?JSON.parse(JSON.stringify(lastProbe)):null,
    limitations:['Ownership is observed browser API state, not proof of OS/device resource behavior.',
      'Pending permission/context/socket closures remain counted; cleanup errors remain visible.',
      'Queue seconds are conservative admission/buffer estimates; physical output and calibration remain unknown.'],
    trace_cursor:diagnosticSequence,trace_dropped:diagnosticDropped};},
  diagnosticProbe,cancelDiagnosticProbe,
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
updateControls();
