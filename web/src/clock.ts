/** Bounded canonical clock_probe/clock_pong alignment; no one-way-delay claim. */
type AlignmentSample={probe_id:string;rtt_ms:number;offset_ms:number;uncertainty_ms:number};
export class ClockAlignment {
  readonly maximum=20;private sent=new Map<string,number>();private terminal=new Set<string>();
  private samples:AlignmentSample[]=[];
  constructor(private bootId:string|undefined=undefined){}
  begin(probeId:string,browserMs:number){if(this.sent.size>=20||this.sent.has(probeId)||!Number.isFinite(browserMs)||browserMs<0)throw new Error('Invalid clock probe');this.sent.set(probeId,browserMs);}
  expire(probeId:string){if(this.sent.has(probeId))this.terminal.add(probeId);}
  expirePending(){for(const id of this.sent.keys())this.terminal.add(id);}
  accept(payload:Record<string,unknown>,receivedMs:number):boolean{
    const id=payload.probe_id;if(typeof id!=='string')return false;const sent=this.sent.get(id);
    if(sent===undefined||this.terminal.has(id)||payload.browser_sent_ms!==sent||payload.clock_domain!=='server_monotonic_ns'||payload.browser_clock_domain!=='browser_performance_ms'||payload.boot_id!==this.bootId)return false;
    const receiveNs=payload.server_received_ns,sendNs=payload.server_sent_ns;
    if(typeof receiveNs!=='number'||typeof sendNs!=='number'||!Number.isSafeInteger(receiveNs)||!Number.isSafeInteger(sendNs)||receiveNs<0||sendNs<receiveNs||!Number.isFinite(receivedMs)||receivedMs<sent)return false;
    const rtt=receivedMs-sent,serverDuration=(sendNs-receiveNs)/1e6;if(serverDuration>rtt)return false;
    this.terminal.add(id);this.samples.push({probe_id:id,rtt_ms:rtt,offset_ms:(receiveNs/1e6+sendNs/1e6)/2-(sent+receivedMs)/2,uncertainty_ms:rtt/2});return true;
  }
  summary(){
    let best:AlignmentSample|undefined;for(const sample of this.samples)if(!best||sample.rtt_ms<best.rtt_ms)best=sample;
    const complete=this.sent.size===20&&this.terminal.size===20;
    return {requested:this.sent.size,completed:this.terminal.size,observed:this.samples.length,missing:this.terminal.size-this.samples.length,
      complete,status:best&&complete&&best.rtt_ms<=60?'estimated':'unknown',minimum_rtt_ms:best?.rtt_ms??null,
      offset_server_minus_browser_ms:best&&complete&&best.rtt_ms<=60?best.offset_ms:null,uncertainty_ms:best?.uncertainty_ms??null,
      assumption:'symmetric path; midpoint +/- RTT/2; browser headline only',one_way_delay_known:false,
      acoustic_uncertainty:'unknown',samples:this.samples.map(s=>({...s}))};
  }
}
