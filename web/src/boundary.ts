import {SHAPES,CONSTANTS,LITERALS,CUSTOM_VALIDATORS_UNEXECUTED} from './generated/contracts';
import type {CanonicalTypes,ClientMessage,ServerMessage,ServerMessageVariant} from './generated/contracts';
interface Rule {kind:string;name?:string;values?:unknown[];items?:Rule[];item?:Rule;key?:Rule;value?:Rule;fields?:Record<string,{required:boolean;rule:Rule}>;min_length?:number;max_length?:number;ge?:number;gt?:number;le?:number;lt?:number;pattern?:string}
const shapes=SHAPES as Record<string,Rule>;
const object=(value:unknown):value is Record<string,unknown>=>value!==null&&typeof value==='object'&&!Array.isArray(value);
/** Structural browser consumer guard only. Python validators/authority stay canonical. */
// Server monotonic ns is an unbounded canonical int (raw host monotonic clock). Past 2^53 a JSON
// number loses precision, so it is admitted as a non-negative integer with precision unknown;
// consumers needing exact arithmetic (ClockAlignment) still require Number.isSafeInteger.
const PRECISION_TOLERANT_INTEGERS=new Set(['ServerNs']);
export function inspectShape(name:keyof CanonicalTypes,value:unknown,stepBudget=20000){
  let budget=stepBudget;
  const walk=(rule:Rule,data:unknown,path:string,depth:number):string|undefined=>{
    if(--budget<0||depth>40)return path+': structural inspection bound exceeded';
    if(rule.kind==='ref'&&PRECISION_TOLERANT_INTEGERS.has(rule.name!)&&typeof data==='number'&&Number.isInteger(data)&&!Number.isSafeInteger(data)){
      return data>=0?undefined:path+': numeric bound';
    }else if(rule.kind==='ref'){
      const target=shapes[rule.name!];if(!target)return path+': canonical rule unavailable';
      const failed=walk(target,data,path,depth+1);if(failed)return failed;
    }else if(rule.kind==='union'){
      if(!rule.items!.some(item=>walk(item,data,path,depth+1)===undefined))return path+': unsupported union value';
    }else if(rule.kind==='literal'){
      if(!rule.values!.includes(data))return path+': unknown closed literal';
    }else if(rule.kind==='model'){
      if(!object(data))return path+': expected metadata object';
      const fields=rule.fields!;
      if(Object.keys(data).some(key=>!Object.hasOwn(fields,key)))return path+': unexpected model field';
      for(const [key,field] of Object.entries(fields)){
        if(!Object.hasOwn(data,key)){if(field.required)return path+'.'+key+': missing';continue;}
        const failed=walk(field.rule,data[key],path+'.'+key,depth+1);if(failed)return failed;
      }
    }else if(rule.kind==='array'){
      if(!Array.isArray(data))return path+': expected list';
      for(let i=0;i<data.length;i++){const failed=walk(rule.item!,data[i],path+'['+i+']',depth+1);if(failed)return failed;}
    }else if(rule.kind==='record'){
      if(!object(data))return path+': expected mapping';
      for(const [key,value] of Object.entries(data)){
        const keyFailure=walk(rule.key!,key,path+'.<key>',depth+1);if(keyFailure)return keyFailure;
        const failed=walk(rule.value!,value,path+'.<value>',depth+1);if(failed)return failed;
      }
    }else if(rule.kind==='string'&&typeof data!=='string')return path+': expected string';
    else if(rule.kind==='integer'&&!(typeof data==='number'&&Number.isSafeInteger(data)))return path+': integer is unavailable/unsafe';
    else if(rule.kind==='number'&&!(typeof data==='number'&&Number.isFinite(data)))return path+': finite number required';
    else if(rule.kind==='boolean'&&typeof data!=='boolean')return path+': boolean required';
    else if(rule.kind==='null'&&data!==null)return path+': null required';
    if(data!==null){
      const length=typeof data==='string'||Array.isArray(data)?data.length:object(data)?Object.keys(data).length:undefined;
      if(length!==undefined&&(rule.min_length!==undefined&&length<rule.min_length||rule.max_length!==undefined&&length>rule.max_length))return path+': length bound';
      if(typeof data==='number'&&(rule.ge!==undefined&&data<rule.ge||rule.gt!==undefined&&data<=rule.gt||rule.le!==undefined&&data>rule.le||rule.lt!==undefined&&data>=rule.lt))return path+': numeric bound';
      if(typeof data==='string'&&rule.pattern&&!new RegExp(rule.pattern).test(data))return path+': canonical pattern mismatch';
    }
    return undefined;
  };
  const rule=shapes[name],issue=rule?walk(rule,value,String(name),0):'Canonical shape unavailable';
  return {ok:issue===undefined,issues:issue?[issue]:[],observation:'structural_only',custom_validators:'not_executed',authority:'unknown'} as const;
}
export function readStructural<N extends keyof CanonicalTypes>(name:N,value:unknown,stepBudget?:number):CanonicalTypes[N]{const inspected=inspectShape(name,value,stepBudget);if(!inspected.ok)throw new Error(inspected.issues.join('; '));return value as CanonicalTypes[N];}
export function clientWire(value:unknown):ClientMessage {return readStructural('ClientMessage',value);}
export function serverWire(raw:string):ServerMessage {
  if(raw.length>300000)throw new Error('Server envelope exceeds existing browser bound');
  // Parser detail is withheld: browser JSON errors can quote frame content (transcripts, PCM).
  let value:unknown;try{value=JSON.parse(raw);}catch{throw new Error('server_frame_json_malformed');}
  const result=readStructural('ServerMessage',value);
  if(result.protocol_version!==CONSTANTS.PROTOCOL_VERSION)throw new Error('Unsupported protocol version');return result;
}
// Discriminator -> generated variant model, derived from the canonical unions (never a browser table).
// A value claimed by two members is ambiguous and refused, as is any value absent from the union.
function closedVariants(alias:string,discriminator:string){
  const map=new Map<string,keyof CanonicalTypes>(),ambiguous=new Set<string>(),union=shapes[alias];
  for(const item of union?.kind==='union'?union.items??[]:[]){
    const rule=item.kind==='ref'?shapes[item.name!]?.fields?.[discriminator]?.rule:undefined;
    for(const value of rule?.kind==='literal'?rule.values??[]:[]){
      if(typeof value!=='string')continue;
      if(map.has(value)&&map.get(value)!==item.name)ambiguous.add(value);else map.set(value,item.name as keyof CanonicalTypes);
    }
  }
  for(const value of ambiguous)map.delete(value);
  return map;
}
const SERVER_VARIANTS=closedVariants('ServerMessageVariant','type');
const EVENT_VARIANTS=closedVariants('EventPayload','kind');
/** Closed registry-v1 consumer: generic envelope guard, then the exact generated variant for `type`. */
export function serverVariant(raw:string):ServerMessageVariant {
  const envelope=serverWire(raw),model=SERVER_VARIANTS.get(envelope.type);
  if(!model)throw new Error('server_payload_variant_unknown');
  if(envelope.type==='event'){const kind=envelope.payload.kind;if(typeof kind!=='string'||!EVENT_VARIANTS.has(kind))throw new Error('server_event_kind_unknown');}
  // Step budget scales with the already byte-bounded (300,000 char) frame so a large but valid
  // ended payload (ledger rows) is not refused; union retries are covered by the 8x factor.
  return readStructural(model,envelope,Math.max(20000,raw.length*8)) as ServerMessageVariant;
}
export const SERVER_PAYLOAD_REGISTRY={version:CONSTANTS.SERVER_PAYLOAD_REGISTRY_VERSION,types:[...SERVER_VARIANTS.keys()],event_kinds:[...EVENT_VARIANTS.keys()],observation:'structural_only',custom_validators:'not_executed'} as const;
export function isArm(value:unknown):value is CanonicalTypes['Hello']['arm'] {return (LITERALS.Hello_arm as readonly unknown[]).includes(value);}
export {CUSTOM_VALIDATORS_UNEXECUTED};
