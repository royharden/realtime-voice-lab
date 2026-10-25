/** D5 v1 consumer types and bounded browser-side content checks.
 * D5 remains the canonical validator/journal/authority owner. No oracle is issued. */
import {LITERALS,MODEL_FIELDS} from './generated/contracts';
import {inspectShape} from './boundary';
import type {ReviewPacket,ReviewItem,ReviewArtifact,PlaybackDeclaration,DecisionSubmission,Judgment,Reason} from './generated/contracts';
export type {ReviewPacket,ReviewItem,ReviewArtifact,PlaybackDeclaration,DecisionSubmission,Judgment,Reason};
export const REASONS=LITERALS.Reason;
export const ROLES=LITERALS.ReviewArtifactRoles;
export type Role=typeof ROLES[number];
export const BINDING_KEYS=MODEL_FIELDS.ReviewBindings;
export function isHash(value:unknown):value is string{return inspectShape('Hash',value).ok;}
export function record(value:unknown):value is Record<string,unknown>{return value!==null&&typeof value==='object'&&!Array.isArray(value);}
function identifier(value:unknown){return inspectShape('Identifier',value).ok;}
function boundedText(value:unknown,max=500){return typeof value==='string'&&value.trim().length>0&&value.length<=max&&!/[\u0000-\u001f\u007f]/.test(value);}
export function offsetTime(value:unknown):value is string{
  if(typeof value!=='string'||value.length>40)return false;
  const match=/^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.\d{1,6})?(?:Z|([+-])(\d\d):(\d\d))$/.exec(value);if(!match)return false;
  const year=Number(match[1]),month=Number(match[2]),day=Number(match[3]),leap=year%4===0&&(year%100!==0||year%400===0);
  const days=[31,leap?29:28,31,30,31,30,31,31,30,31,30,31];
  return year>0&&month>=1&&month<=12&&day>=1&&day<=days[month-1]&&Number(match[4])<=23&&Number(match[5])<=59&&Number(match[6])<=59&&(!match[7]||Number(match[8])<=23&&Number(match[9])<=59)&&Number.isFinite(Date.parse(value));
}
export function safeRelativePath(value:unknown):value is string{
  if(typeof value!=='string'||!value.length||value.length>500||/[:\u0000-\u001f?#%]/.test(value))return false;
  const normalized=value.replaceAll('\\','/');return !normalized.startsWith('/')&&normalized.split('/').every(part=>part.length>0&&part!=='.'&&part!=='..');
}
function exactKeys(value:Record<string,unknown>,keys:readonly string[]){return Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key));}
function requireSafeNumbers(text:string){
  const tokens=text.replace(/"(?:\\.|[^"\\])*"/g,'""').match(/-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g)??[];
  if(tokens.some(token=>/[.eE]/.test(token)||token==='-0'||!Number.isSafeInteger(Number(token))))throw new Error('D5 v1 forbids float/exponent/unsafe integer JSON representations');
}
export function parsePacket(text:string):ReviewPacket {
  if(text.length>512000)throw new Error('Packet exceeds the D5 v1 metadata byte bound');
  requireSafeNumbers(text);
  const packet:unknown=JSON.parse(text);
  const keys=MODEL_FIELDS.ReviewPacket;
  if(!record(packet)||!exactKeys(packet,keys)||packet.schema_version!==1||packet.kind!=='manual_review_packet'||packet.evidence_status!=='unadjudicated'||packet.product_acceptance!==false||!['dev','heldout'].includes(String(packet.split)))throw new Error('Unsupported D5 packet structure');
  if(!isHash(packet.packet_hash)||!identifier(packet.packet_id)||!identifier(packet.run_id)||!identifier(packet.task_id)||!boundedText(packet.created_at)||!boundedText(packet.artifact_root_policy,1000)||!record(packet.gates)||!record(packet.source_hashes)||Object.keys(packet.source_hashes).length>100||!Object.values(packet.source_hashes).every(isHash))throw new Error('Invalid packet identity/source hashes');
  if(!Array.isArray(packet.items)||packet.items.length<1||packet.items.length>8)throw new Error('Packet must contain one to eight items');
  const seen=new Set<string>();
  for(const raw of packet.items){
    if(!record(raw)||!exactKeys(raw,MODEL_FIELDS.ReviewItem)||!identifier(raw.item_id)||seen.has(String(raw.item_id))||!record(raw.bindings)||!exactKeys(raw.bindings,BINDING_KEYS)||!record(raw.artifacts)||!exactKeys(raw.artifacts,ROLES)||!Array.isArray(raw.evidence_gaps)||raw.evidence_gaps.length>100||!raw.evidence_gaps.every(g=>typeof g==='string'&&g.length<=2000))throw new Error('Invalid/duplicate item or closed artifact roles');
    seen.add(String(raw.item_id));const b=raw.bindings;
    if(b.run_id!==packet.run_id||b.task_id!==packet.task_id||b.logical_turn_id!==raw.item_id||!identifier(b.trial_id))throw new Error('Item/run/task/logical identity mismatch');
    for(const key of BINDING_KEYS){const value=b[key];if(key.endsWith('_hash')){if(value!==null&&!isHash(value))throw new Error('Malformed binding hash');}else if(value!==null&&!identifier(value))throw new Error('Malformed binding identifier');}
    for(const role of ROLES){const ref=raw.artifacts[role];if(ref===null)continue;
      const kind=role.endsWith('_audio')?'audio':role==='confirmation'?'confirmation_evidence':role;
      if(!record(ref)||!Object.keys(ref).every(k=>(MODEL_FIELDS.ReviewArtifact as readonly string[]).includes(k))||!safeRelativePath(ref.path)||!isHash(ref.sha256)||ref.kind!==kind||(kind==='audio'&&ref.approved_synthetic!==true))throw new Error('Unsafe/unapproved artifact reference');
    }
  }
  return packet as unknown as ReviewPacket;
}
function compareKeys(a:string,b:string){const ac=Array.from(a,c=>c.codePointAt(0)!),bc=Array.from(b,c=>c.codePointAt(0)!);for(let i=0;i<Math.min(ac.length,bc.length);i++)if(ac[i]!==bc[i])return ac[i]-bc[i];return ac.length-bc.length;}
function asciiString(value:string){return JSON.stringify(value).replace(/[\u007f-\uffff]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));}
function canonical(value:unknown,depth=0):string {
  if(depth>20)throw new Error('Packet nesting bound exceeded');
  if(value===null)return 'null';if(typeof value==='boolean')return String(value);if(typeof value==='string')return asciiString(value);
  if(typeof value==='number'&&Number.isSafeInteger(value))return String(value);
  if(Array.isArray(value))return '['+value.map(v=>canonical(v,depth+1)).join(',')+']';
  if(record(value))return '{'+Object.keys(value).sort(compareKeys).map(key=>asciiString(key)+':'+canonical(value[key],depth+1)).join(',')+'}';
  throw new Error('Unsupported canonical value; leave D5 validation pending');
}
export async function sha256(bytes:ArrayBuffer|Uint8Array){const value=bytes instanceof Uint8Array?new Uint8Array(bytes).buffer:bytes;const digest=await crypto.subtle.digest('SHA-256',value);return Array.from(new Uint8Array(digest),b=>b.toString(16).padStart(2,'0')).join('');}
export async function packetDigest(packet:ReviewPacket){if(new TextEncoder().encode(canonical(packet)+'\n').byteLength>512000)throw new Error('Canonical D5 packet metadata exceeds512000 bytes');const body:Record<string,unknown>={};for(const [key,value] of Object.entries(packet))if(key!=='packet_hash')body[key]=value;return sha256(new TextEncoder().encode(canonical(body)+'\n'));}
export const ROLE_BINDING:Record<Role,typeof BINDING_KEYS[number]>={input_audio:'input_audio_hash',rendered_audio:'rendered_audio_hash',confirmation_audio:'confirmation_audio_hash',confirmation:'confirmation_evidence_hash',journal:'journal_hash',trace:'trace_hash'};
export function itemGaps(item:ReviewItem){const gaps=[...(item.evidence_gaps??[])];for(const key of BINDING_KEYS)if(item.bindings[key]===null)gaps.push('Missing binding: '+key);for(const role of ROLES){const ref=item.artifacts[role];if(!ref)gaps.push('Missing artifact: '+role);else if(ref.sha256!==item.bindings[ROLE_BINDING[role]])gaps.push('Binding mismatch: '+role);}return [...new Set(gaps)];}
export function parseDeclaration(text:string,packet:ReviewPacket):PlaybackDeclaration {
  if(text.length>128000)throw new Error('Playback declaration exceeds bound');requireSafeNumbers(text);const value:unknown=JSON.parse(text);
  const keys=MODEL_FIELDS.PlaybackDeclaration;
  if(!record(value)||!exactKeys(value,keys)||value.schema_version!==1||value.kind!=='manual_playback_declaration'||value.packet_hash!==packet.packet_hash||!identifier(value.item_id)||!(LITERALS.PlaybackDeclaration_role as readonly unknown[]).includes(value.role)||!isHash(value.audio_hash)||!isHash(value.declaration_hash)||value.approved_synthetic!==true||value.policy!=='operator_custody_expiry'||value.declaration_status!=='unverified'||value.product_acceptance!==false||!offsetTime(value.retention_until_utc))throw new Error('Unsupported/unbound canonical D5 playback declaration');
  if(!['wav','raw_pcm'].includes(String(value.container))||!['audio/wav','application/octet-stream'].includes(String(value.media_type))||(value.container==='wav')!==(value.media_type==='audio/wav')||value.encoding!=='signed_little_endian_pcm16'||!Number.isSafeInteger(value.sample_rate)||Number(value.sample_rate)<8000||Number(value.sample_rate)>192000||![1,2].includes(Number(value.channels))||typeof value.channels!=='number'||value.bits_per_sample!==16)throw new Error('Declared PCM16 media metadata is unsupported');
  for(const key of ['approval_reference','manifest_reference','custody_reference'])if(!boundedText(value[key]))throw new Error('Declared retention/approval/custody references missing');
  for(const key of ['input_rights_reference','output_rights_reference'])if(value[key]!==null&&!boundedText(value[key]))throw new Error('Malformed declared rights reference');
  const rights=value.role==='rendered_audio'?value.output_rights_reference:value.input_rights_reference;if(!boundedText(rights))throw new Error('Role-specific declared rights reference is required');
  const item=packet.items?.find(item=>item.item_id===value.item_id),ref=item?.artifacts[value.role as Role];
  if(!item||!ref||ref.sha256!==value.audio_hash||item.bindings[ROLE_BINDING[value.role as Role]]!==value.audio_hash)throw new Error('Declaration packet/item/role/audio hash mismatch');
  const until=Date.parse(value.retention_until_utc);if(until<=Date.now()||until>Date.now()+30*86400000)throw new Error('Playback expiry must be current and bounded to thirty days');
  return value as unknown as PlaybackDeclaration;
}
export async function declarationDigest(declaration:PlaybackDeclaration){const body:Record<string,unknown>={};for(const [key,value] of Object.entries(declaration))if(key!=='declaration_hash')body[key]=value;return sha256(new TextEncoder().encode(canonical(body)+'\n'));}
export function playbackGrant(declaration:PlaybackDeclaration|undefined,itemId:string,role:Role,hash:string){if(!declaration||declaration.item_id!==itemId||declaration.role!==role||declaration.audio_hash!==hash||Date.parse(declaration.retention_until_utc)<=Date.now())throw new Error('Current hash-bound D5 role declaration required');return declaration;}
export function audioBlob(bytes:ArrayBuffer,grant:PlaybackDeclaration):Blob {
  if(typeof grant.sample_rate!=='number'||!Number.isSafeInteger(grant.sample_rate)||grant.sample_rate<8000||grant.sample_rate>192000)throw new Error('Declared sample rate unavailable');
  const view=new DataView(bytes);let pcmBytes=bytes.byteLength;
  const blockAlign=grant.channels*2;
  if(grant.container==='raw_pcm'){
    if(!pcmBytes||pcmBytes%blockAlign)throw new Error('Raw PCM16LE frame alignment differs from the bound declaration');
    if(pcmBytes/(grant.sample_rate*blockAlign)>120)throw new Error('Audio duration exceeds two minutes');
    const header=new ArrayBuffer(44),h=new DataView(header);const tag=(offset:number,text:string)=>Array.from(text).forEach((c,i)=>h.setUint8(offset+i,c.charCodeAt(0)));
    tag(0,'RIFF');h.setUint32(4,36+pcmBytes,true);tag(8,'WAVE');tag(12,'fmt ');h.setUint32(16,16,true);h.setUint16(20,1,true);h.setUint16(22,grant.channels,true);h.setUint32(24,grant.sample_rate,true);h.setUint32(28,grant.sample_rate*blockAlign,true);h.setUint16(32,blockAlign,true);h.setUint16(34,16,true);tag(36,'data');h.setUint32(40,pcmBytes,true);
    return new Blob([header,bytes],{type:'audio/wav'}); // Volatile wrapper, not a new source artifact.
  }
  const tag=(at:number,n:number)=>String.fromCharCode(...new Uint8Array(bytes,at,n));
  if(bytes.byteLength<44||tag(0,4)!=='RIFF'||tag(8,4)!=='WAVE'||view.getUint32(4,true)+8!==bytes.byteLength)throw new Error('Unsupported WAV container');
  let offset=12,format=false,data=false;
  while(offset+8<=bytes.byteLength){const size=view.getUint32(offset+4,true),end=offset+8+size;if(end>bytes.byteLength)throw new Error('Truncated WAV chunk');const name=tag(offset,4);
    if(name==='fmt '){if(format||size<16||view.getUint16(offset+8,true)!==1||view.getUint16(offset+10,true)!==grant.channels||view.getUint32(offset+12,true)!==grant.sample_rate||view.getUint32(offset+16,true)!==grant.sample_rate*blockAlign||view.getUint16(offset+20,true)!==blockAlign||view.getUint16(offset+22,true)!==16)throw new Error('WAV must match declared PCM16 rate/channels/alignment');format=true;}
    if(name==='data'){if(data||!size||size%blockAlign)throw new Error('Malformed WAV audio');pcmBytes=size;data=true;}offset=end+(size%2);
  }
  if(offset!==bytes.byteLength||!format||!data||pcmBytes/(grant.sample_rate*blockAlign)>120)throw new Error('WAV format/data/duration is unavailable');return new Blob([bytes],{type:'audio/wav'});
}
