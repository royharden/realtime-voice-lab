import {httpResponse} from './boundary';

/** Explicit bounded configuration/own-process reads. Never probes a provider. */
export function mountRelayMetadata(root:HTMLElement, credentials:()=>{url:string;nonce:string}) {
  root.innerHTML='<h2>Relay configuration and diagnostics</h2><p>Explicit local reads only. Configuration is not a provider probe; metrics are one own-process snapshot.</p><button data-testid="admission-read">Read admission</button><button data-testid="diagnostics-read">Read diagnostics</button><p data-testid="relay-metadata-status" role="status">No snapshot requested.</p><pre data-testid="relay-metadata-results"></pre>';
  const status=root.querySelector<HTMLElement>('[role="status"]')!,output=root.querySelector('pre')!;
  let generation=0,controller:AbortController|undefined;
  function clear(){generation++;controller?.abort();output.textContent='';status.textContent='No current snapshot.';}
  async function read(route:'admission'|'diagnostics') {
    clear();const own=generation,abort=new AbortController();controller=abort;
    const timer=setTimeout(()=>abort.abort(),10000);
    let reader:ReadableStreamDefaultReader<Uint8Array>|undefined;
    try {
      const input=credentials(),url=new URL(input.url);
      url.protocol=url.protocol==='ws:'?'http:':url.protocol==='wss:'?'https:':url.protocol;
      if(url.origin!==location.origin||!['http:','https:'].includes(url.protocol)||!['127.0.0.1','localhost','[::1]'].includes(url.hostname)||url.username||url.password||!input.nonce)throw new Error('same_origin_loopback_required');
      status.textContent='Reading bounded local metadata…';
      const response=await fetch(`${url.origin}/${route}`,{method:'POST',mode:'same-origin',credentials:'omit',redirect:'error',cache:'no-store',referrerPolicy:'no-referrer',headers:{'Content-Type':'application/json','X-Voicelab-Nonce':input.nonce},body:'{}',signal:abort.signal});
      if(!response.ok)throw new Error(response.status===404||response.status===405?'protected_post_alias_unavailable':'metadata_read_refused');
      if(!/^application\/json(?:\s*;|$)/i.test(response.headers.get('content-type')??'')||!response.body)throw new Error('metadata_body_refused');
      reader=response.body.getReader();const chunks:Uint8Array[]=[];let bytes=0;
      while(true){const part=await reader.read();if(part.done)break;bytes+=part.value.length;if(bytes>262144)throw new Error('metadata_byte_bound');chunks.push(part.value);}
      const buffer=new Uint8Array(bytes);let offset=0;for(const chunk of chunks){buffer.set(chunk,offset);offset+=chunk.length;}
      const parsed=httpResponse(route,JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(buffer)));
      if(own!==generation||abort.signal.aborted)return;
      output.textContent=JSON.stringify(parsed,null,2);status.textContent='Received structural metadata; custom validators and runtime acceptance remain unverified.';
    }catch{
      if(own===generation){output.textContent='';status.textContent='Local metadata unavailable or refused. No automatic retry; protected same-origin POST support is required.';}
    }finally{
      clearTimeout(timer);abort.abort();if(reader){try{await reader.cancel();}catch{}finally{reader.releaseLock();}}
      if(controller===abort)controller=undefined;
    }
  }
  root.querySelector<HTMLButtonElement>('[data-testid="admission-read"]')!.onclick=()=>void read('admission');
  root.querySelector<HTMLButtonElement>('[data-testid="diagnostics-read"]')!.onclick=()=>void read('diagnostics');
  return {clear};
}
