import {httpResponse} from './boundary';
import {evidenceIdentifier,projectSessionEvidence} from './session-evidence-model';
import type {EvidenceOwner} from './session-evidence-model';
const MAX_BYTES=8*1024*1024,TIMEOUT_MS=10000;
type Lease={owner:EvidenceOwner;nonce:string;origin:string};
type RequestHandle={controller:AbortController;timer:ReturnType<typeof setTimeout>|undefined;reader:ReadableStreamDefaultReader<Uint8Array>|undefined};
// Secrets and response bytes stay private to this mounted instance. No telemetry/export API.
export function mountSessionEvidence(root:HTMLElement,options:{canInspect:()=>boolean}){
  root.className='card session-evidence';
  root.innerHTML='<p class="eyebrow">PROTECTED SAVED METADATA</p><h2>Post-End session evidence</h2><p>Inspect only the admitted session after End and cleanup. Saved hashes are references, not verified artifact bytes. No audio is loaded or played.</p><div class="actions"><button data-read data-testid="evidence-read">Inspect / refresh</button><button data-clear>Clear evidence and authorization</button></div><p data-status data-testid="evidence-status" role="status">No owned admission.</p><pre data-owner></pre><div data-results data-testid="evidence-results"></div><p class="muted">Run snapshots are not session totals. Missing fields remain null/unknown. No automatic correctness, acoustic, billing or product acceptance claim.</p>';
  const status=root.querySelector<HTMLElement>('[data-status]')!,ownerView=root.querySelector<HTMLElement>('[data-owner]')!,results=root.querySelector<HTMLElement>('[data-results]')!,read=root.querySelector<HTMLButtonElement>('[data-read]')!;
  let lease:Lease|undefined,generation=0,ended=false,endObservation='unknown',lastEpochs:{connection_epoch:number|null;response_epoch:number|null}={connection_epoch:null,response_epoch:null};
  const pending=new Set<RequestHandle>();
  function handles(){return {requests:pending.size,abort_controllers:pending.size,readers:[...pending].filter(r=>!!r.reader).length,timers:[...pending].filter(r=>r.timer!==undefined).length};}
  function renderOwner(){ownerView.textContent=JSON.stringify({identity:lease?.owner??null,last_browser_envelope_epochs:lastEpochs,end_observation:endObservation,owned_handles:handles(),http_epoch_observation:'unknown'},null,2);read.disabled=!lease||!ended||pending.size>0;}
  function abort(){++generation;for(const request of pending){request.controller.abort();if(request.timer!==undefined){clearTimeout(request.timer);request.timer=undefined;}if(request.reader)void request.reader.cancel().catch(()=>{});}renderOwner();}
  function clear(){abort();lease=undefined;ended=false;lastEpochs={connection_epoch:null,response_epoch:null};endObservation='unknown';results.replaceChildren();status.textContent='Cleared. A new relay admission is required to inspect.';renderOwner();}
  function admit(identity:Record<string,unknown>,nonce:string,origin:string,version:number){
    if(version!==generation)return; // A field/mode/Start/clear change revoked this transport's lease.
    clear();const keys=['session_id','run_id','boot_id','trial_id'] as const;
    if(!keys.every(k=>evidenceIdentifier(identity[k]))||!nonce||nonce.length>256){status.textContent='Owned admission metadata incomplete; evidence target unknown.';return;}
    let parsed:URL;try{parsed=new URL(origin);}catch{status.textContent='Evidence origin unavailable.';return;}
    if(parsed.origin!==window.location.origin||!['http:','https:'].includes(parsed.protocol)||!['127.0.0.1','localhost','[::1]'].includes(parsed.hostname)){
      status.textContent='Incomplete serving seam: browser and relay must share the exact loopback HTTP origin. Cross-origin evidence reads are refused.';return;
    }
    lease={owner:Object.fromEntries(keys.map(k=>[k,identity[k]])) as EvidenceOwner,nonce,origin:parsed.origin};
    status.textContent='Owned admission captured privately. End and release session handles before explicit inspection.';renderOwner();
  }
  function observe(envelope:{session_id?:unknown;connection_epoch?:unknown;response_epoch?:unknown}){
    if(!lease||envelope.session_id!==lease.owner.session_id)return;
    const epoch=(v:unknown)=>typeof v==='number'&&Number.isSafeInteger(v)&&v>=0?v:null;
    lastEpochs={connection_epoch:epoch(envelope.connection_epoch),response_epoch:epoch(envelope.response_epoch)};renderOwner();
  }
  function markEnd(observation:'local_end_requested'|'relay_ended_observed',sessionId?:unknown){
    if(!lease||(observation==='relay_ended_observed'&&sessionId!==lease.owner.session_id))return;ended=true;endObservation=observation;status.textContent='End observed/requested. Explicit inspection is available after session cleanup; terminal snapshot may still be incomplete.';renderOwner();
  }
  function show(projection:ReturnType<typeof projectSessionEvidence>){
    results.replaceChildren();for(const [name,value] of Object.entries(projection)){
      const section=document.createElement('details'),title=document.createElement('summary'),body=document.createElement('pre');
      title.textContent=name.replaceAll('_',' ');body.textContent=JSON.stringify(value,null,2);section.append(title,body);results.append(section);
    }
  }
  async function inspect(){
    if(!lease||!ended||pending.size||!options.canInspect()){status.textContent='Inspection requires End, no active session handles, and no pending read. Retry explicitly after cleanup.';return;}
    const owned=lease,own=generation,request:RequestHandle={controller:new AbortController(),timer:undefined,reader:undefined};
    pending.add(request);let timedOut=false;
    request.timer=setTimeout(()=>{request.timer=undefined;timedOut=true;request.controller.abort();if(request.reader)void request.reader.cancel().catch(()=>{});renderOwner();},TIMEOUT_MS);
    results.replaceChildren();status.textContent='Reading bounded saved metadata; no retries, audio or artifact dereference.';renderOwner();
    const current=()=>own===generation&&lease===owned&&!request.controller.signal.aborted;
    try{
      // Origin/Host are browser controlled. Never manually set them or fall back to GET.
      const response=await fetch(`${owned.origin}/sessions/${encodeURIComponent(owned.owner.session_id)}/evidence`,{method:'POST',mode:'same-origin',credentials:'omit',cache:'no-store',redirect:'error',referrerPolicy:'no-referrer',headers:{'Content-Type':'application/json','X-Voicelab-Nonce':owned.nonce},body:'{}',signal:request.controller.signal});
      if(!current())return;
      if(response.body){request.reader=response.body.getReader();renderOwner();}
      if(!response.ok){throw new Error(response.status===404||response.status===405?'evidence_alias_or_snapshot_unavailable':response.status===401||response.status===403?'evidence_authorization_refused':'evidence_http_error');}
      if(!/^application\/json(?:\s*;|$)/i.test(response.headers.get('content-type')??''))throw new Error('evidence_content_type');
      const declared=response.headers.get('content-length');if(declared&&(!/^\d+$/.test(declared)||Number(declared)>MAX_BYTES))throw new Error('evidence_byte_bound');
      if(!request.reader)throw new Error('evidence_body_unavailable');
      const chunks:Uint8Array[]=[];let bytes=0;
      while(true){const part=await request.reader.read();if(!current())return;if(part.done)break;bytes+=part.value.byteLength;if(bytes>MAX_BYTES)throw new Error('evidence_byte_bound');chunks.push(part.value);}
      const buffer=new Uint8Array(bytes);let offset=0;for(const chunk of chunks){buffer.set(chunk,offset);offset+=chunk.byteLength;}
      let parsed:unknown;try{parsed=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(buffer));}catch{throw new Error('evidence_invalid_json');}
      const projected=projectSessionEvidence(httpResponse('session_evidence',parsed),owned.owner);if(!current())return;
      show(projected);status.textContent='Received saved metadata. Unknowns preserved; whole-run snapshot replaces previous view. Runtime/acoustic/correctness acceptance remains unknown.';
    }catch(error){
      if(own===generation&&lease===owned){const allowed=['evidence_alias_or_snapshot_unavailable','evidence_authorization_refused','evidence_http_error','evidence_content_type','evidence_byte_bound','evidence_body_unavailable','evidence_invalid_json','metadata_structure_bound','evidence_identity_mismatch','evidence_schema_version'];
        const code=error instanceof Error&&allowed.includes(error.message)?error.message:timedOut?'evidence_timeout':request.controller.signal.aborted?'evidence_aborted':'evidence_read_failed';
        status.textContent=`${code}. Metadata incomplete/unknown. No automatic retry; use explicit refresh.`;}
    }finally{
      request.controller.abort();if(request.timer!==undefined){clearTimeout(request.timer);request.timer=undefined;}
      if(request.reader){try{await request.reader.cancel();}catch{/* Abort/cancel remains best effort. */}finally{request.reader.releaseLock();request.reader=undefined;}}
      pending.delete(request);renderOwner();
    }
  }
  read.onclick=()=>void inspect();root.querySelector<HTMLButtonElement>('[data-clear]')!.onclick=clear;renderOwner();
  return {admit,observe,markEnd,clear,handles,admissionVersion:()=>generation};
}
