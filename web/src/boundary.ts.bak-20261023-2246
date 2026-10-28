import {SHAPES,CONSTANTS,LITERALS,CUSTOM_VALIDATORS_UNEXECUTED} from './generated/contracts';
import type {CanonicalTypes,ClientMessage,ServerMessage} from './generated/contracts';
interface Rule {kind:string;name?:string;values?:unknown[];items?:Rule[];item?:Rule;key?:Rule;value?:Rule;fields?:Record<string,{required:boolean;rule:Rule}>;min_length?:number;max_length?:number;ge?:number;gt?:number;le?:number;lt?:number;pattern?:string}
const shapes=SHAPES as Record<string,Rule>;
const object=(value:unknown):value is Record<string,unknown>=>value!==null&&typeof value==='object'&&!Array.isArray(value);
/** Structural browser consumer guard only. Python validators/authority stay canonical. */
export function inspectShape(name:keyof CanonicalTypes,value:unknown){
  let budget=20000;
  const walk=(rule:Rule,data:unknown,path:string,depth:number):string|undefined=>{
    if(--budget<0||depth>40)return path+': structural inspection bound exceeded';
    if(rule.kind==='ref'){
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
export function readStructural<N extends keyof CanonicalTypes>(name:N,value:unknown):CanonicalTypes[N]{const inspected=inspectShape(name,value);if(!inspected.ok)throw new Error(inspected.issues.join('; '));return value as CanonicalTypes[N];}
export function clientWire(value:unknown):ClientMessage {return readStructural('ClientMessage',value);}
export function serverWire(raw:string):ServerMessage {
  if(raw.length>300000)throw new Error('Server envelope exceeds existing browser bound');
  const value:unknown=JSON.parse(raw);const result=readStructural('ServerMessage',value);
  if(result.protocol_version!==CONSTANTS.PROTOCOL_VERSION)throw new Error('Unsupported protocol version');return result;
}
export function isArm(value:unknown):value is CanonicalTypes['Hello']['arm'] {return (LITERALS.Hello_arm as readonly unknown[]).includes(value);}
export {CUSTOM_VALIDATORS_UNEXECUTED};
