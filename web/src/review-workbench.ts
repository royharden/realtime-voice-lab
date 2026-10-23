import {REASONS,ROLES,BINDING_KEYS,ROLE_BINDING,parsePacket,packetDigest,parseDeclaration,declarationDigest,playbackGrant,audioBlob,isHash,offsetTime,record,sha256,itemGaps} from './review-model';
import type {ReviewPacket,ReviewItem,Role,PlaybackDeclaration,Judgment,Reason,DecisionSubmission} from './review-model';
type AssetRole=Role|'ledger';
interface SelectedAsset {file:File;hash:string;bytes:ArrayBuffer;role:AssetRole;size:number}
interface Draft {spoken:''|Judgment;task:''|Judgment;confirmation:''|Judgment;reasons:Set<Reason>;actor:string;appointment:string;decidedAt:string;supersedes:string;human:boolean}
const AUDIO_ROLES=new Set<AssetRole>(['input_audio','rendered_audio','confirmation_audio']);
const TOTAL_LIMIT=96_000_000;
const emptyDraft=():Draft=>({spoken:'',task:'',confirmation:'',reasons:new Set(),actor:'',appointment:'',decidedAt:'',supersedes:'',human:false});
function textNode(tag:string,text:string,parent:HTMLElement){const node=document.createElement(tag);node.textContent=text;parent.append(node);return node;}
function scoped<T extends HTMLElement>(root:HTMLElement,selector:string){const node=root.querySelector<T>(selector);if(!node)throw new Error('Review UI element missing');return node;}
/** Local inspection only. No fetch, upload, directory walk, journal writer or scorer. */
export function mountReviewWorkbench(root:HTMLElement,options:{canPlay:()=>boolean;onChanged?:()=>void}){
  root.className='card review-workbench';root.innerHTML=`<div class="section-head"><div><p class="eyebrow">OPERATOR EVIDENCE INSPECTION</p><h2>Manual review workbench</h2></div><button data-rv="clear">Clear local bundle</button></div>
  <p class="notice">UNVERIFIED REVIEW INPUT · No appointment, independent actor, custody release, oracle or product acceptance is issued here. Heldout remains closed. Source construction and browser behavior are unverified.</p>
  <div class="review-imports"><label>D5 dev review packet<input data-rv="packet" type="file" accept=".json,application/json"></label><label>Externally supplied packet content pin<input data-rv="pin" spellcheck="false" maxlength="64" placeholder="SHA256 packet_hash from approved handoff"></label><label>Add canonical D5 playback declarations<input data-rv="access" type="file" accept=".json,application/json" multiple disabled></label><p>Pin each role declaration below using its externally supplied declaration_hash. All declarations remain unverified.</p></div>
  <label class="review-check"><input data-rv="root-ack" type="checkbox"> I explicitly selected these approved synthetic files from the separately authorized owned root. The browser cannot verify absolute filesystem ancestry or external authority.</label>
  <p data-rv="status" role="status">No local packet selected. No files are discovered automatically.</p><pre data-rv="summary">No packet content loaded.</pre>
  <label>Task item<select data-rv="item" disabled><option>Select a packet first</option></select></label>
  <div class="review-comparison"><section><h3>Exact bindings</h3><div data-rv="bindings"></div><h3>Gaps and mismatches</h3><ul data-rv="gaps"></ul></section><section><h3>Local file selections</h3><div data-rv="artifacts"></div><p class="muted">Each role requires its own explicit selection. Hashes bind bytes, not correctness or independence. Playback is opt-in; physical sound remains unknown.</p></section></div>
  <section class="review-inspector"><h3>Trace / authority / confirmation comparison</h3><label>Inspect selected role<select data-rv="inspect"><option value="trace">Trace</option><option value="journal">Authority journal</option><option value="confirmation">Genuine confirmation evidence</option><option value="ledger">Ledger</option></select></label><div class="review-pages"><button data-rv="previous" type="button">Previous rows</button><span data-rv="page">No rows</span><button data-rv="next" type="button">Next rows</button></div><pre data-rv="inspection">Select a hash-matching artifact. No provider text is used as an answer key.</pre></section>
  <form data-rv="form"><h3>Human choices · no suggested correctness</h3><div data-rv="judgments" class="review-judgments"></div><fieldset data-rv="reasons"><legend>Closed reason codes · select 1..8</legend></fieldset>
  <div class="review-imports"><label>Human actor reference<input data-rv="actor" maxlength="160" autocomplete="off" placeholder="Unverified external actor reference"></label><label>Appointment reference<input data-rv="appointment" maxlength="500" autocomplete="off" placeholder="External appointment scope; not verified here"></label><label>Human decision timestamp with timezone<input data-rv="decided-at" maxlength="40" placeholder="YYYY-MM-DDTHH:mm:ssZ or explicit offset"></label><label>Supersedes prior item record hash (optional)<input data-rv="supersedes" maxlength="64" spellcheck="false" placeholder="D5 verifies exact current item history"></label></div>
  <label class="review-check"><input data-rv="human" type="checkbox"> I entered my own human decisions and references. This draft remains unverified; coding actors cannot issue heldout or independent acceptance labels.</label>
  <p data-rv="export-status">No choices entered. Every dimension must be explicitly selected, including unknown.</p><button data-rv="export" type="submit" disabled>Export UNVERIFIED submission</button></form>
  <details><summary>Review-owned resources</summary><pre data-rv="handles">No local resources acquired.</pre></details>`;
  const q=<T extends HTMLElement=HTMLElement>(key:string)=>scoped<T>(root,`[data-rv="${key}"]`);
  let generation=0,itemGeneration=0,packet:ReviewPacket|undefined,accessGeneration=0,pendingPlays=0;
  let inspectionPage=0,inspectionPages=1,index=0,verifiedPacket=false,pendingReads=0,pendingBytes=0,draft=emptyDraft();
  const selected=new Map<string,SelectedAsset>(),assetRequests=new Map<string,number>(),assetMessages=new Map<string,string>();
  const inspectorIssues=new Map<string,string>();
  const declarations=new Map<string,PlaybackDeclaration>(),declarationPins=new Map<string,string>();
  const drafts=new Map<string,Draft>(),played=new Set<string>(),urls=new Set<string>(),audios=new Set<HTMLAudioElement>(),timers=new Set<ReturnType<typeof setTimeout>>(),rafs=new Set<number>();
  let playing:HTMLAudioElement|undefined,playbackSequence=0;
  const key=(role:AssetRole)=>`${packet?.items[index]?.item_id??'none'}:${role}`;
  const loadedBytes=()=>[...selected.values()].reduce((total,row)=>total+row.size,0);
  function handles(){return {selected_files:selected.size,selected_bytes:loadedBytes(),pending_reads:pendingReads,pending_bytes:pendingBytes,pending_plays:pendingPlays,audio_elements:audios.size,object_urls:urls.size,timers:timers.size,raf:rafs.size,physical_audio:'unknown'};}
  function showHandles(){q('handles').textContent=JSON.stringify(handles(),null,2);}
  function message(value:string){q('status').textContent=value;showHandles();}
  function timer(callback:()=>void,ms:number){const id=setTimeout(()=>{timers.delete(id);callback();showHandles();},ms);timers.add(id);showHandles();return id;}
  function stopPlayback(){
    playbackSequence++;
    for(const id of timers)clearTimeout(id);timers.clear();
    for(const audio of audios){audio.onended=null;audio.onerror=null;audio.onpause=null;audio.pause();audio.removeAttribute('src');audio.load();audio.remove();}audios.clear();playing=undefined;
    for(const url of urls)URL.revokeObjectURL(url);urls.clear();showHandles();
  }
  function clear(){
    generation++;itemGeneration++;accessGeneration++;stopPlayback();for(const id of rafs)cancelAnimationFrame(id);rafs.clear();
    selected.clear();inspectorIssues.clear();assetMessages.clear();assetRequests.clear();drafts.clear();played.clear();packet=undefined;declarations.clear();declarationPins.clear();verifiedPacket=false;draft=emptyDraft();index=0;
    q<HTMLInputElement>('packet').value='';q<HTMLInputElement>('access').value='';q<HTMLInputElement>('pin').value='';q<HTMLInputElement>('root-ack').checked=false;q<HTMLInputElement>('access').disabled=true;
    q<HTMLSelectElement>('item').replaceChildren(new Option('Select a packet first',''));q<HTMLSelectElement>('item').disabled=true;q('summary').textContent='No packet content loaded.';renderItem();message(pendingReads?'Cleared. Pending file reads remain counted until they settle; stale results cannot be admitted.':'Local bundle cleared. No review media retained.');
  }
  async function read(file:File,maximum:number,own:number){
    if(!file.size||file.size>maximum||loadedBytes()+pendingBytes+file.size>TOTAL_LIMIT)throw new Error('Selected file exceeds the local byte bound');
    pendingReads++;pendingBytes+=file.size;showHandles();
    try{const bytes=await file.arrayBuffer();if(own!==generation)throw new Error('Selection was replaced');if(bytes.byteLength!==file.size)throw new Error('Selected file size changed');const hash=await sha256(bytes);if(own!==generation)throw new Error('Selection was replaced');return {bytes,hash};}
    finally{pendingReads--;pendingBytes-=file.size;showHandles();}
  }
  function currentItem(){return packet?.items[index];}
  function packetPinned(){return !!packet&&verifiedPacket&&q<HTMLInputElement>('pin').value===packet.packet_hash;}
  function declared(role:Role){const declaration=declarations.get(key(role));if(!declaration||declarationPins.get(key(role))!==declaration.declaration_hash)throw new Error('External canonical declaration pin missing/mismatched for '+role);return declaration;}
  function requirePlayback(role:Role,hash:string){
    if(!packetPinned()||!q<HTMLInputElement>('root-ack').checked)throw new Error('Explicit packet pin and owned-root acknowledgement are required');
    if(!options.canPlay())throw new Error('End the active session and wait for cleanup before review playback');
    return playbackGrant(declared(role),currentItem()!.item_id,role,hash);
  }
  function allGaps(){const item=currentItem();if(!item)return ['No item selected'];const gaps=itemGaps(item);
    for(const role of [...ROLES,'ledger'] as AssetRole[]){const loaded=selected.get(key(role));if(!loaded)gaps.push('Selected artifact not byte-verified: '+role);}
    for(const role of [...ROLES,'ledger'] as AssetRole[]){const issue=inspectorIssues.get(key(role));if(issue)gaps.push(role+': '+issue);}
    if(!packetPinned())gaps.push('External packet content pin missing/mismatched');
    for(const role of ['input_audio','rendered_audio','confirmation_audio'] as Role[])try{const d=declared(role);playbackGrant(d,currentItem()!.item_id,role,currentItem()!.bindings[ROLE_BINDING[role]]??'');}catch{gaps.push('Canonical playback declaration/pin/retention missing: '+role);}
    if(!q<HTMLInputElement>('root-ack').checked)gaps.push('Owned-root operator acknowledgement absent');
    return [...new Set(gaps)];
  }
  function renderGaps(){q('gaps').replaceChildren();for(const gap of allGaps())textNode('li',gap,q('gaps'));if(allGaps().length===0)textNode('li','Local byte/declaration checks complete. Independent review and authority remain unverified.',q('gaps'));}
  function exportProblems(){
    const problems:string[]=[];
    if(!packetPinned()||!currentItem())problems.push('A digest-matching, externally pinned dev packet/item is required.');
    for(const dimension of ['spoken','task','confirmation'] as const)if(!draft[dimension])problems.push('Choose '+dimension+' explicitly.');
    if(!draft.actor.trim()||draft.actor.length>160||/[\u0000-\u001f\u007f]/.test(draft.actor))problems.push('Enter a bounded actor reference.');
    if(!draft.appointment.trim()||draft.appointment.length>500||/[\u0000-\u001f\u007f]/.test(draft.appointment))problems.push('Enter the external appointment reference.');
    if(!offsetTime(draft.decidedAt))problems.push('Enter a human decision timestamp with timezone.');
    if(draft.reasons.size<1||draft.reasons.size>8)problems.push('Select one to eight closed reasons.');
    if(draft.supersedes&&!isHash(draft.supersedes))problems.push('Supersedes must be an exact SHA256 record hash.');
    if(!draft.human)problems.push('Confirm that these are your human choices, still unverified.');
    if(['spoken','task','confirmation'].some(d=>draft[d as 'spoken']==='correct')&&allGaps().length)problems.push('Positive choices cannot be exported while evidence/declarations are missing or mismatched. Use unknown where warranted.');
    if(draft.spoken==='correct'&&(!played.has(key('input_audio'))||!played.has(key('rendered_audio'))))problems.push('Input/rendered playback has not completed locally; hearing is still a human judgment.');
    if(draft.confirmation==='correct'&&!played.has(key('confirmation_audio')))problems.push('Genuine confirmation audio has not completed local playback.');
    return problems;
  }
  function updateExport(){const problems=exportProblems();q<HTMLButtonElement>('export').disabled=problems.length>0;q('export-status').textContent=problems.length?problems.join(' '):'Ready to export an UNVERIFIED submission. D5 must validate actor authority, bindings, history and genuine confirmation; no correctness is inferred here.';renderGaps();}
  function bindDraft(){
    for(const dimension of ['spoken','task','confirmation'] as const){const select=scoped<HTMLSelectElement>(root,`[data-judgment="${dimension}"]`);select.value=draft[dimension];select.onchange=()=>{draft[dimension]=select.value as ''|Judgment;updateExport();};}
    for(const [field,input] of [['actor','actor'],['appointment','appointment'],['decidedAt','decided-at'],['supersedes','supersedes']] as const){q<HTMLInputElement>(input).value=draft[field];q<HTMLInputElement>(input).oninput=()=>{draft[field]=q<HTMLInputElement>(input).value;updateExport();};}
    q<HTMLInputElement>('human').checked=draft.human;q<HTMLInputElement>('human').onchange=()=>{draft.human=q<HTMLInputElement>('human').checked;updateExport();};
    for(const checkbox of q('reasons').querySelectorAll<HTMLInputElement>('input')){checkbox.checked=draft.reasons.has(checkbox.value as Reason);checkbox.onchange=()=>{if(checkbox.checked)draft.reasons.add(checkbox.value as Reason);else draft.reasons.delete(checkbox.value as Reason);updateExport();};}
  }
  function metadataRecords(role:AssetRole,bytes:ArrayBuffer):unknown[]{
    const text=new TextDecoder('utf-8',{fatal:true}).decode(bytes);
    if(role==='trace'){const lines=text.split(/\r?\n/).filter(line=>line.trim());if(!lines.length||lines.length>20000)throw new Error('Trace must contain 1..20000 bounded JSONL records');return lines.map(line=>{const value:unknown=JSON.parse(line);if(!record(value))throw new Error('Trace rows must be metadata objects');return value;});}
    const value:unknown=JSON.parse(text);if(!record(value))throw new Error('Evidence must be a JSON metadata object');return [value];
  }
  function inspect(){
    const role=q<HTMLSelectElement>('inspect').value as AssetRole,asset=selected.get(key(role));
    inspectionPages=1;q<HTMLButtonElement>('previous').disabled=true;q<HTMLButtonElement>('next').disabled=true;q('page').textContent='No selected rows';
    if(!asset){q('inspection').textContent='No hash-matching local '+role+' file selected.';return;}
    try{
      let records=metadataRecords(role,asset.bytes);const bound=currentItem()?.bindings;
      if(role==='trace'&&bound?.observed_session_id){records=records.filter(row=>record(row)&&row.session_id===bound.observed_session_id);if(!records.length)throw new Error('No trace records for the exact bound observed session');}
      if(role==='journal'&&record(records[0])&&Array.isArray(records[0].sessions)&&bound?.observed_session_id){
        records=records[0].sessions.filter(row=>record(row)&&record(row.session)&&row.session.session_id===bound.observed_session_id);if(!records.length)throw new Error('No journal session for the exact bound observed session');
      }
      const total=records.length;inspectionPages=Math.max(1,Math.ceil(total/100));inspectionPage=Math.min(inspectionPage,inspectionPages-1);
      q<HTMLButtonElement>('previous').disabled=inspectionPage===0;q<HTMLButtonElement>('next').disabled=inspectionPage===inspectionPages-1;q('page').textContent=`Page ${inspectionPage+1}/${inspectionPages}; ${total} exact-session rows`;
      let omitted=false;
      const forbidden=/^(?:text|transcript|prompt|content|messages|pcm16|audio|authorization|nonce|headers|token|api_key|secret|raw)$/i;
      const prune=(value:unknown,depth=0):unknown=>{
        if(depth>12){omitted=true;return '[deeper content omitted]';}
        if(Array.isArray(value)){if(value.length>200)omitted=true;return value.slice(0,200).map(v=>prune(v,depth+1));}
        if(record(value)){const out:Record<string,unknown>={};if(Object.keys(value).length>100)omitted=true;for(const [k,v] of Object.entries(value).slice(0,100))if(!forbidden.test(k))Object.defineProperty(out,k,{value:prune(v,depth+1),enumerable:true});return out;}
        if(typeof value==='string'){if(value.length>1000)omitted=true;return value.slice(0,1000);}return value;
      };
      const rendered=JSON.stringify({role,sha256:asset.hash,view:'explicit hash-bound local metadata; actual session filter, never rewritten aliases; not correctness',rows:records.slice(inspectionPage*100,(inspectionPage+1)*100).map(r=>prune(r))},null,2);
      if(rendered.length>120000)omitted=true;
      q('inspection').textContent=rendered.slice(0,120000)+(omitted?'\n[bounded display omitted content; do not infer complete review]':'');
      if(omitted)inspectorIssues.set(key(role),'Bounded inspector omits content; complete evidence interpretation is unavailable here');else inspectorIssues.delete(key(role));
    }catch(error){inspectorIssues.set(key(role),error instanceof Error?error.message:'Evidence parse/scope unknown');q('inspection').textContent='Hash-bound bytes cannot be inspected in this scope: '+inspectorIssues.get(key(role));}
    updateExport();
  }
  async function loadArtifact(role:AssetRole,file:File){
    const item=currentItem();if(!item||!packet)return;
    const own=generation,chosenItem=item.item_id,k=key(role),request=(assetRequests.get(k)??0)+1;assetRequests.set(k,request);selected.delete(k);played.delete(k);stopPlayback();assetMessages.set(k,'Verifying exact selected bytes...');renderArtifacts();
    try{
      const ref=role==='ledger'?undefined:item.artifacts[role];const expected=role==='ledger'?item.bindings.ledger_hash:ref?.sha256;
      if(!isHash(expected)||role!=='ledger'&&expected!==item.bindings[ROLE_BINDING[role]])throw new Error('Missing or mismatched packet artifact/binding');
      const {bytes,hash:actual}=await read(file,AUDIO_ROLES.has(role)?32_000_000:8_000_000,own);
      if(own!==generation||assetRequests.get(k)!==request)return;
      if(actual!==expected)throw new Error('Selected file hash mismatch; bytes were not retained');
      if(!AUDIO_ROLES.has(role))metadataRecords(role,bytes);inspectorIssues.delete(k);
      selected.set(k,{file,hash:actual,bytes,role,size:bytes.byteLength});assetMessages.set(k,'Exact byte hash matches; content/rights/authority not inferred.');
    }catch(error){if(own===generation&&assetRequests.get(k)===request)assetMessages.set(k,error instanceof Error?error.message:'Artifact verification failed');}
    if(own===generation){if(currentItem()?.item_id===chosenItem){renderArtifacts();inspect();}updateExport();showHandles();}
  }
  async function play(role:AssetRole){
    const k=key(role),asset=selected.get(k);if(!asset)return;const own=generation,ownItem=itemGeneration;let playOwn=playbackSequence;
    try{
      const grant=requirePlayback(role as Role,asset.hash);stopPlayback();playOwn=playbackSequence;const blob=audioBlob(asset.bytes,grant),url=URL.createObjectURL(blob);urls.add(url);
      const audio=document.createElement('audio');audio.preload='none';audio.controls=false;audio.src=url;audios.add(audio);playing=audio;
      audio.onended=()=>{if(playOwn!==playbackSequence)return;if(own===generation&&ownItem===itemGeneration){played.add(k);message('Local media element reached ended. Physical hearing and correctness remain unverified.');}stopPlayback();updateExport();};
      audio.onerror=()=>{if(playOwn!==playbackSequence)return;message('Local audio decoder failed; physical sound and evidence interpretation are unknown.');stopPlayback();updateExport();};
      showHandles();pendingPlays++;showHandles();try{await audio.play();}finally{pendingPlays--;showHandles();}
      if(own!==generation||ownItem!==itemGeneration||playing!==audio||playOwn!==playbackSequence){audio.pause();return;}
      const remaining=Date.parse(grant.retention_until_utc)-Date.now();timer(()=>{if(playOwn!==playbackSequence)return;message('Playback stopped at retention expiry. Reselect current declarations before further playback.');stopPlayback();updateExport();},Math.max(1,Math.min(remaining,121000)));
      message('Explicit local '+role+' playback. Boundary: HTML media element, physical sound unknown. No audio is uploaded or captured.');
    }catch(error){if(own!==generation||ownItem!==itemGeneration||playOwn!==playbackSequence)return;stopPlayback();message(error instanceof Error?error.message:'Playback not admitted');updateExport();}
  }
  function renderArtifacts(){
    const host=q('artifacts');host.replaceChildren();const item=currentItem();if(!item)return;
    for(const role of [...ROLES,'ledger'] as AssetRole[]){
      const row=document.createElement('div');row.className='review-artifact';host.append(row);textNode('h4',role.replaceAll('_',' '),row);
      const ref=role==='ledger'?undefined:item.artifacts[role];textNode('small',role==='ledger'?'Extra explicitly selected ledger; binds ledger_hash only':ref?ref.path:'Missing reference in packet',row);
      const input=document.createElement('input');input.type='file';input.accept=AUDIO_ROLES.has(role)?'.wav,.pcm16,.pcm,audio/wav,application/octet-stream':'.json,.jsonl,application/json';input.disabled=role!=='ledger'&&!ref;
      input.setAttribute('aria-label','Select exact '+role+' artifact');input.onchange=()=>{const file=input.files?.[0];if(file)void loadArtifact(role,file);};row.append(input);
      textNode('p',assetMessages.get(key(role))??(selected.has(key(role))?'Byte verified':'No selected bytes'),row);
      const asset=selected.get(key(role));if(asset)textNode('code',asset.hash,row);
      if(AUDIO_ROLES.has(role)){const d=declarations.get(key(role));textNode('small',d?'D5 declaration_hash: '+d.declaration_hash:'No canonical D5 role declaration selected',row);const pin=document.createElement('input');pin.maxLength=64;pin.spellcheck=false;pin.placeholder='External declaration_hash for this exact item/role';pin.value=declarationPins.get(key(role))??'';pin.setAttribute('aria-label','External '+role+' declaration pin');const roleKey=key(role);pin.oninput=()=>{declarationPins.set(roleKey,pin.value);stopPlayback();updateExport();};row.append(pin);const button=document.createElement('button');button.type='button';button.textContent='Play selected '+role.replaceAll('_',' ');button.disabled=!asset;button.onclick=()=>void play(role);row.append(button);
        const stop=document.createElement('button');stop.type='button';stop.textContent='Stop playback';stop.onclick=stopPlayback;row.append(stop);}
    }
  }
  function renderItem(){
    stopPlayback();q('bindings').replaceChildren();const item=currentItem();
    if(item){const table=document.createElement('table');const body=document.createElement('tbody');for(const field of BINDING_KEYS){const row=document.createElement('tr');textNode('th',field,row);textNode('td',item.bindings[field]??'UNKNOWN / missing',row);body.append(row);}table.append(body);q('bindings').append(table);
      draft=drafts.get(item.item_id)??emptyDraft();drafts.set(item.item_id,draft);
    }else draft=emptyDraft();
    bindDraft();renderArtifacts();inspect();updateExport();showHandles();options.onChanged?.();
  }
  for(const dimension of ['spoken','task','confirmation'] as const){const label=document.createElement('label');label.textContent=dimension+' judgment';const select=document.createElement('select');select.dataset.judgment=dimension;select.append(new Option('Choose explicitly (unknown is a valid choice)',''));for(const choice of ['correct','incorrect','unknown'])select.append(new Option(choice,choice));label.append(select);q('judgments').append(label);}
  for(const reason of REASONS){const label=document.createElement('label');label.className='review-check';const check=document.createElement('input');check.type='checkbox';check.value=reason;label.append(check,document.createTextNode(reason.replaceAll('_',' ')));q('reasons').append(label);}
  q<HTMLInputElement>('packet').onchange=async()=>{
    const file=q<HTMLInputElement>('packet').files?.[0];if(!file)return;clear();const own=generation;message('Reading only the explicitly selected packet metadata...');
    try{const {bytes}=await read(file,512000,own),candidate=parsePacket(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
      if(candidate.split!=='dev')throw new Error('Heldout view is closed: no trusted independent release/appointment authority is installed');
      const digest=await packetDigest(candidate);if(own!==generation)return;if(digest!==candidate.packet_hash)throw new Error('Packet canonical content digest mismatch');
      packet=candidate;verifiedPacket=true;q<HTMLInputElement>('access').disabled=false;
      q('summary').textContent=JSON.stringify({packet_id:packet.packet_id,packet_hash:packet.packet_hash,run_id:packet.run_id,task_id:packet.task_id,created_at:packet.created_at,split:packet.split,evidence_status:packet.evidence_status,product_acceptance:false,content_digest:'matches; D5 canonical validation/authority still external',source_hashes:packet.source_hashes,gates:packet.gates},null,2);
      q<HTMLSelectElement>('item').replaceChildren(...packet.items.map((item,i)=>new Option(item.item_id,String(i))));q<HTMLSelectElement>('item').disabled=false;index=0;renderItem();message('Dev packet content digest matches. Enter external pins and explicitly select artifacts/declarations; no correctness is proposed.');
    }catch(error){if(own===generation){packet=undefined;verifiedPacket=false;message(error instanceof Error?error.message:'Packet unavailable');renderItem();}}
  };
  q<HTMLInputElement>('access').onchange=async()=>{
    const files=Array.from(q<HTMLInputElement>('access').files??[]);if(!files.length||!packet)return;
    const own=generation,request=++accessGeneration,originalPacket=packet;stopPlayback();updateExport();
    try{
      if(files.length>24)throw new Error('Select at most24 explicit D5 role declarations');const incoming=new Map<string,PlaybackDeclaration>();
      for(const file of files){const {bytes}=await read(file,128000,own);const candidate=parseDeclaration(new TextDecoder('utf-8',{fatal:true}).decode(bytes),originalPacket);const actual=await declarationDigest(candidate);if(own!==generation||request!==accessGeneration)return;if(actual!==candidate.declaration_hash)throw new Error('Canonical declaration content hash mismatch');const k=candidate.item_id+':'+candidate.role;if(incoming.has(k))throw new Error('Duplicate item/role declaration in selected batch');incoming.set(k,candidate);}
      if(declarations.size+incoming.size>48)throw new Error('Declaration selection bound exceeded');
      for(const [k,candidate] of incoming){if(declarations.get(k)?.declaration_hash!==candidate.declaration_hash)declarationPins.delete(k);declarations.set(k,candidate);}
      message('Canonical D5 role declarations match their content hashes. Enter external declaration pins; authority remains unverified.');renderArtifacts();updateExport();
    }catch(error){if(own===generation&&request===accessGeneration){message(error instanceof Error?error.message:'Playback declaration unavailable');updateExport();}}
  };
  q<HTMLSelectElement>('item').onchange=()=>{itemGeneration++;inspectionPage=0;index=Number(q<HTMLSelectElement>('item').value);renderItem();};q<HTMLSelectElement>('inspect').onchange=()=>{inspectionPage=0;inspect();};q('previous').onclick=()=>{inspectionPage=Math.max(0,inspectionPage-1);inspect();};q('next').onclick=()=>{inspectionPage=Math.min(inspectionPages-1,inspectionPage+1);inspect();};
  for(const field of ['pin'] as const)q<HTMLInputElement>(field).oninput=()=>{stopPlayback();updateExport();options.onChanged?.();};q<HTMLInputElement>('root-ack').onchange=()=>{stopPlayback();updateExport();};q('clear').onclick=clear;
  q<HTMLFormElement>('form').onsubmit=event=>{
    event.preventDefault();updateExport();if(exportProblems().length||!packet||!currentItem())return;
    const submission:DecisionSubmission={schema_version:1,packet_hash:packet.packet_hash,item_id:currentItem()!.item_id,actor_ref:draft.actor,appointment_ref:draft.appointment,decided_at:draft.decidedAt,judgments:{spoken:draft.spoken as Judgment,task:draft.task as Judgment,confirmation:draft.confirmation as Judgment},reasons:REASONS.filter(reason=>draft.reasons.has(reason)),task_terminal:null,supersedes:draft.supersedes||null};
    const url=URL.createObjectURL(new Blob([JSON.stringify(submission,null,2)+'\n'],{type:'application/json'}));urls.add(url);const link=document.createElement('a');link.href=url;link.download=`UNVERIFIED-review-${packet.packet_hash.slice(0,12)}-${submission.item_id}.json`;link.click();
    const id=requestAnimationFrame(()=>{rafs.delete(id);URL.revokeObjectURL(url);urls.delete(url);link.remove();showHandles();});rafs.add(id);message('UNVERIFIED submission download requested. No journal record, actor verification, oracle label or acceptance has been issued.');
  };
  window.addEventListener('pagehide',clear);renderItem();showHandles();
  return Object.freeze({clear,stopPlayback,handles,replayBinding:()=>{const item=currentItem();if(!packet||!item||!packetPinned())return null;return {packet_hash:packet.packet_hash,run_id:packet.run_id,task_id:packet.task_id,item_id:item.item_id,bindings:{...item.bindings},source_hashes:{...packet.source_hashes}};},focus:()=>root.scrollIntoView({behavior:'smooth',block:'start'}),hasPlayback:()=>audios.size>0||pendingPlays>0});
}
