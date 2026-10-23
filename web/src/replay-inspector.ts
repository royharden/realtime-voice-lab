import {readStructural,inspectShape} from './boundary';
import {SOURCE_HASHES,MODEL_FIELDS} from './generated/contracts';
import type {ReproductionExpectation,SealedArtifact} from './generated/contracts';
import {isHash,record,safeRelativePath,sha256} from './review-model';
interface ReviewLink {packet_hash:string;run_id:string;task_id:string;item_id:string;bindings:Record<string,string|null>;source_hashes:Record<string,string>}
interface FileRow {ref:SealedArtifact;bytes:ArrayBuffer;file:File;hash:string;text:string}
const MAX_BYTES=96_000_000;
const COLUMNS=['run_id','task_id','trial_id','logical_turn_id','transport_turn_id','observed_trial_id','observed_session_id','observed_boot_id','session_id','boot_id','attempt_id','response_id','connection_epoch','response_epoch','sequence','kind','clock_domain','timestamp','outcome','terminal_outcome','admission','receipt_latency_ms','render_latency_ms','unknown_reason','observed_run_id','observed_logical_turn_id','observed_transport_turn_id','provider_request_id','logical_operation_id','state','dispatch_authorized','dispatched','started','terminal_reason','reservation','settled','currency','price_version','cost_complete','receipt_status','render_status','censored','censoring','attained','status','failure_reason','timing_boundary','timing_domain','task_correct','spoken_correct'];
/** A saved-file view only. It never calls the live receiver, reporter or authority. */
export function mountReplayInspector(root:HTMLElement,options:{reviewBinding:()=>ReviewLink|null;focusReview:()=>void}){
  root.className='card replay-inspector';root.innerHTML=`<div class="section-head"><div><p class="eyebrow">SAVED LOCAL REPLAY · READ ONLY</p><h2>Run and trace inspector</h2></div><button data-rp="clear">Clear saved inspection</button></div>
  <p class="notice">This view inspects explicitly selected dev evidence. It cannot start a run, open a microphone/socket, call a provider, mutate the task journal or regenerate reports. Hash equality is content binding; runtime, rights authority and independent acceptance remain unverified.</p>
  <div class="review-imports"><label>D5 sealed reproduction expectation<input data-rp="seal" type="file" accept=".json,application/json"></label><label>Externally supplied expectation file SHA256<input data-rp="pin" maxlength="64" spellcheck="false" placeholder="Exact original bytes pin from approved handoff"></label></div>
  <label class="review-check"><input data-rp="approved" type="checkbox"> I explicitly selected this approved synthetic dev evidence from the separately authorized owned root. Browser file selection cannot establish OS ancestry or custody.</label>
  <p data-rp="status" role="status">No saved evidence selected; nothing is discovered automatically.</p><pre data-rp="pins">No declared run pins.</pre>
  <div class="review-comparison"><section><h3>Exact sealed files</h3><div data-rp="files"></div></section><section><h3>Declared identity and scope</h3><pre data-rp="binding">No saved binding selected.</pre><button data-rp="review" disabled>Inspect bound approved audio in workbench</button><p class="muted">Audio stays in the canonical review packet/playback-declaration view above. This button never autoplays. Unknown or mismatched run/code/build/fixture/plan/original-manifest identity stays unbound.</p><details><summary>Current generated binding provenance</summary><pre data-rp="schema"></pre></details></section></div>
  <label>Selected saved file<select data-rp="role" disabled></select></label><label>Original record collection<select data-rp="collection"><option value="0">Whole saved object / JSONL order</option></select></label><div class="review-pages"><button data-rp="previous" disabled>Previous records</button><span data-rp="page">No records</span><button data-rp="next" disabled>Next records</button></div>
  <p data-rp="scope">Original ordering and IDs will remain visible; no cross-clock subtraction or outcome inference occurs.</p><div data-rp="rows" class="replay-table"></div><details><summary>Original selected record text · local only</summary><pre data-rp="original">No record selected.</pre></details>
  <details><summary>Replay-owned local references</summary><pre data-rp="handles"></pre></details>`;
  const q=<T extends HTMLElement=HTMLElement>(name:string)=>root.querySelector<T>(`[data-rp="${name}"]`)!;
  let generation=0,requestSequence=0,seal:ReproductionExpectation|undefined,sealHash:string|undefined,pending=0,pendingBytes=0,page=0,pages=1;
  const files=new Map<string,FileRow>(),requests=new Map<string,number>(),messages=new Map<string,string>();
  const handles=()=>({saved_files:files.size,saved_bytes:[...files.values()].reduce((n,row)=>n+row.bytes.byteLength,0),pending_reads:pending,pending_bytes:pendingBytes,microphones:0,sockets:0,providers:0,authority_mutations:0});
  const updateHandles=()=>{q('handles').textContent=JSON.stringify(handles(),null,2);};
  function status(text:string){q('status').textContent=text;updateHandles();}
  function pinned(){return !!seal&&sealHash===q<HTMLInputElement>('pin').value&&isHash(sealHash)&&q<HTMLInputElement>('approved').checked;}
  function entries(){if(!seal)return [] as [string,SealedArtifact|null][];return [...MODEL_FIELDS.ReportInputs.map(name=>['input:'+name,seal!.inputs[name]] as [string,SealedArtifact|null]),...Object.entries(seal.expected_outputs).map(([name,ref])=>['output:'+name,ref] as [string,SealedArtifact])];}
  function clear(){generation++;seal=undefined;sealHash=undefined;files.clear();requests.clear();messages.clear();page=0;pages=1;q<HTMLInputElement>('seal').value='';q<HTMLInputElement>('pin').value='';q<HTMLInputElement>('approved').checked=false;q('pins').textContent='No declared run pins.';q<HTMLSelectElement>('role').replaceChildren();q<HTMLSelectElement>('role').disabled=true;q<HTMLSelectElement>('collection').replaceChildren(new Option('Whole saved object / JSONL order','0'));q('page').textContent='No records';q<HTMLButtonElement>('previous').disabled=true;q<HTMLButtonElement>('next').disabled=true;q<HTMLInputElement>('raw-offset').value='0';q('rows').replaceChildren();q('original').textContent='No selected bytes.';q('scope').textContent='No replayed measurements or outcomes exist in this view.';renderFiles();bindReview();status(pending?'Cleared; stale reads remain counted until settlement and cannot be admitted.':'Cleared saved evidence references.');}
  async function read(file:File,limit:number,own:number){
    if(!file.size||file.size>limit||handles().saved_bytes+pendingBytes+file.size>MAX_BYTES)throw new Error('Explicit selected file exceeds byte bound');pending++;pendingBytes+=file.size;updateHandles();
    try{const bytes=await file.arrayBuffer();if(own!==generation)throw new Error('Inspection replaced');const hash=await sha256(bytes);if(own!==generation)throw new Error('Inspection replaced');return {bytes,hash};}finally{pending--;pendingBytes-=file.size;updateHandles();}
  }
  function bindReview(){
    const view=options.reviewBinding();const missing:string[]=[];
    if(!pinned()||!seal)missing.push('Approved pinned sealed metadata missing');
    if(!view)missing.push('No selected canonical review packet/item');
    if(seal&&view){if(view.run_id!==seal.pins.run_id)missing.push('run_id differs');
      for(const key of ['code_hash','build_hash','fixture_manifest_hash','trial_plan_hash'] as const)if(view.bindings[key]!==seal.pins[key])missing.push(key+' differs/unknown');
      if(view.source_hashes.manifest!==seal.inputs.original_manifest.sha256)missing.push('Original manifest hash not pinned by review source');
      if(!files.has('input:original_manifest'))missing.push('Original manifest bytes not selected/hash matched');
    }
    q('binding').textContent=JSON.stringify({status:missing.length?'unknown_or_mismatched':'local_content_bound_not_authorized',issues:missing,review:view?{packet_hash:view.packet_hash,run_id:view.run_id,task_id:view.task_id,item_id:view.item_id,bindings:view.bindings}:null,oracle:'unverified',product_acceptance:false},null,2);
    q<HTMLButtonElement>('review').disabled=missing.length>0;return missing.length===0;
  }
  function cell(value:unknown){if(value===null||value===undefined)return 'unknown / not supplied';if(typeof value==='number'&&Number.isInteger(value)&&!Number.isSafeInteger(value))return 'unknown exact integer; see original bytes';if(typeof value==='object')return '[saved metadata object]';return String(value).slice(0,700);}
  function show(){
    const role=q<HTMLSelectElement>('role').value,row=files.get(role);q('rows').replaceChildren();q('original').textContent='No selected bytes.';pages=1;
    if(!row){q('scope').textContent='Missing selected bytes are unknown, never a successful replay.';q('page').textContent='No records';q<HTMLButtonElement>('previous').disabled=true;q<HTMLButtonElement>('next').disabled=true;return;}
    try{
      const isLines=/\.jsonl$/i.test(row.ref.path)||['input:turns','input:trace','input:oracle','input:evidence'].includes(role);
      const plainText=!isLines&&!/\.json$/i.test(row.ref.path);
      let originals=isLines?row.text.split(/\r?\n/).filter(line=>line.trim()):[row.text];if(originals.length>100000)throw new Error('Saved record count exceeds inspector bound');
      let values:unknown[]=plainText?[{status:'saved_text_content_only'}]:originals.map(raw=>{try{return JSON.parse(raw) as unknown;}catch{return null;}});
      const collection=q<HTMLSelectElement>('collection'),selectedCollection=collection.value;collection.replaceChildren(new Option('Whole saved object / JSONL order','0'));
      const collections:{label:string;rows:unknown[]}[]=[];
      if(!isLines&&record(values[0])){
        const object=values[0];
        for(const [key,value] of Object.entries(object))if(Array.isArray(value))collections.push({label:key+' (original order)',rows:value});
        if(record(object.transport_attempts))for(const [parent,attempts] of Object.entries(object.transport_attempts))if(Array.isArray(attempts))collections.push({label:'transport_attempts / '+parent+' (original child order)',rows:attempts});
        if(collections.length>2048)throw new Error('Saved collection count exceeds bounded inspection');
        collections.forEach((entry,i)=>collection.append(new Option(entry.label,String(i+1))));
        const picked=Number(selectedCollection);if(Number.isInteger(picked)&&picked>0&&picked<=collections.length){collection.value=String(picked);values=collections[picked-1].rows;originals=values.map(value=>JSON.stringify(value));}
      }
      pages=Math.max(1,Math.ceil(values.length/50));page=Math.min(page,pages-1);q('page').textContent=`${page+1}/${pages}; ${values.length} provided records (not a scheduled/start denominator)`;q<HTMLButtonElement>('previous').disabled=page===0;q<HTMLButtonElement>('next').disabled=page===pages-1;
      q('scope').textContent='LOCAL SAVED INSPECTION: file order is preserved. Planned and observed IDs remain distinct. Null means unknown; censoring is shown only when explicitly saved. Declared success is not independent acceptance. Server nanoseconds/browser milliseconds are never subtracted.';
      const table=document.createElement('table'),head=document.createElement('tr');for(const name of ['file_index','shape',...COLUMNS]){const th=document.createElement('th');th.textContent=name;head.append(th);}table.append(head);
      const start=page*50;values.slice(start,start+50).forEach((value,offset)=>{
        const tr=document.createElement('tr');const kind=role==='input:trace'?'VoiceEvent':role==='input:turns'?'TurnResult':undefined;
        const shape=kind?inspectShape(kind,value):undefined;
        const index=document.createElement('td');index.textContent=String(start+offset);tr.append(index);
        const state=document.createElement('td');state.textContent=value===null?'malformed / unknown':shape?shape.ok?'structural only; validators/authority unknown':shape.issues.join('; '):'saved metadata; unclassified';tr.append(state);
        for(const key of COLUMNS){const td=document.createElement('td');td.textContent=record(value)?cell(value[key]):'unknown';tr.append(td);}tr.onclick=()=>{q('original').textContent=(isLines?'Original JSONL record: ':'Parsed JSON record; unsafe integer precision unknown; exact source is available via offset control: ')+originals[start+offset].slice(0,100000)+(originals[start+offset].length>100000?'\n[display truncated; source bytes unchanged]':'');};table.append(tr);
      });q('rows').append(table);
      if(values.length)q('original').textContent=(isLines?'Original JSONL record: ':'Parsed record view; exact source bytes remain separately hash bound: ')+originals[start].slice(0,100000);
      if(!isLines&&values.length===1){const pre=document.createElement('pre');pre.textContent=row.text.slice(0,100000)+(row.text.length>100000?'\n[display truncated; no completeness inferred]':'');q('rows').append(pre);}
    }catch(error){q('scope').textContent='Saved bytes are hash bound but inspection is unavailable: '+(error instanceof Error?error.message:'unknown');}
    bindReview();
  }
  async function choose(name:string,ref:SealedArtifact,file:File){
    const expectation=seal;if(!expectation)return;const own=generation,id=++requestSequence;requests.set(name,id);files.delete(name);messages.set(name,'Checking explicitly selected bytes...');renderFiles();bindReview();if(q<HTMLSelectElement>('role').value===name){q('rows').replaceChildren();q('original').textContent='Replacement selection pending; prior bytes are no longer admitted.';}
    try{
      if(!pinned())throw new Error('Explicit sealed-file pin and approved-root declaration required before saved artifact intake');
      if(!safeRelativePath(ref.path))throw new Error('Unsafe sealed relative reference');if(ref.kind==='audio'||/\.(?:wav|pcm|pcm16)$/i.test(ref.path))throw new Error('Audio stays in the canonical packet/playback-declaration workbench');const {bytes,hash}=await read(file,32_000_000,own);
      if(own!==generation||requests.get(name)!==id)return;if(hash!==ref.sha256)throw new Error('Exact file digest mismatch; bytes not admitted');
      const text=new TextDecoder('utf-8',{fatal:true}).decode(bytes);
      if(name==='input:manifest'||name==='input:original_manifest'){
        const manifest=readStructural('RunManifest',JSON.parse(text));if(manifest.split!=='dev')throw new Error('Heldout saved artifacts stay closed');
        for(const key of Object.keys(expectation.pins) as (keyof ReproductionExpectation['pins'])[])if(manifest[key as keyof typeof manifest]!==expectation.pins[key])throw new Error('Declared saved manifest pin differs: '+key);
      }
      files.set(name,{ref,file,bytes,hash,text});messages.set(name,'Exact byte hash matched. Authority/measurements/reproduction are not established.');
    }catch(error){if(own===generation&&requests.get(name)===id)messages.set(name,error instanceof Error?error.message:'Saved artifact unavailable');}
    if(own===generation){renderFiles();show();bindReview();updateHandles();}
  }
  function renderFiles(){q('files').replaceChildren();const selector=q<HTMLSelectElement>('role'),old=selector.value;selector.replaceChildren();
    for(const [name,ref] of entries()){
      const card=document.createElement('div');card.className='review-artifact';const title=document.createElement('h4');title.textContent=name;card.append(title);const description=document.createElement('p');description.textContent=ref?ref.path+' / '+ref.sha256:'Explicitly absent in sealed inputs; unknown, not omitted';card.append(description);
      if(ref){const input=document.createElement('input');input.type='file';input.setAttribute('aria-label','Select saved '+name);input.onchange=()=>{const file=input.files?.[0];if(file)void choose(name,ref,file);};card.append(input);const message=document.createElement('p');message.textContent=messages.get(name)??'No selected artifact';card.append(message);selector.append(new Option(name,name));}q('files').append(card);
    }selector.disabled=!seal;if(Array.from(selector.options).some(o=>o.value===old))selector.value=old;
  }
  q<HTMLInputElement>('seal').onchange=async()=>{
    const file=q<HTMLInputElement>('seal').files?.[0];if(!file)return;clear();const own=generation;
    try{const {bytes,hash}=await read(file,512000,own);const candidate=readStructural('ReproductionExpectation',JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes)));
      if(candidate.pins.split!=='dev')throw new Error('Heldout sealed inputs remain closed before artifact intake');
      const refs=[...Object.values(candidate.inputs),...Object.values(candidate.expected_outputs)].filter((r):r is SealedArtifact=>!!r);if(refs.length>64||refs.some(ref=>!safeRelativePath(ref.path)))throw new Error('Unbounded/unsafe sealed file references');
      if(own!==generation)return;seal=candidate;sealHash=hash;q('pins').textContent=JSON.stringify({expectation_file_sha256:hash,declared_pins:candidate.pins,declared_versions:candidate.versions,generator_sources:candidate.generator_source_sha256,verification:'content declarations only; no workflow/reproduction/authority run'},null,2);renderFiles();bindReview();status('Saved expectation parsed. Enter its externally supplied exact-byte SHA256 and approve the explicit root scope before selecting artifacts.');
    }catch(error){if(own===generation)status(error instanceof Error?error.message:'Saved expectation unavailable');}
  };
  q('raw').onclick=()=>{const row=files.get(q<HTMLSelectElement>('role').value);if(!row)return;const offset=Number(q<HTMLInputElement>('raw-offset').value);if(!Number.isSafeInteger(offset)||offset<0||offset>row.text.length){status('Source text offset outside selected file');return;}q('original').textContent=`Exact source text characters ${offset}..${Math.min(row.text.length,offset+100000)} of ${row.text.length}; bytes hash ${row.hash}\n`+row.text.slice(offset,offset+100000);};
  q<HTMLInputElement>('pin').oninput=()=>{bindReview();};q<HTMLInputElement>('approved').onchange=()=>{bindReview();};q<HTMLSelectElement>('role').onchange=()=>{page=0;q<HTMLSelectElement>('collection').value='0';show();};q<HTMLSelectElement>('collection').onchange=()=>{page=0;show();};q('previous').onclick=()=>{page=Math.max(0,page-1);show();};q('next').onclick=()=>{page=Math.min(pages-1,page+1);show();};q('clear').onclick=clear;q('review').onclick=()=>{if(bindReview())options.focusReview();};
  q('schema').textContent=JSON.stringify({current_generated_source_hashes:SOURCE_HASHES,custom_validation:'not executed by structural inspector'},null,2);window.addEventListener('pagehide',clear);updateHandles();bindReview();
  return Object.freeze({clear,handles,refresh:bindReview});
}
