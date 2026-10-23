/* Pinned 10 ms RMS windows, -45 dBFS, two consecutive windows (20 ms).
 * Observations are render-graph samples, never acoustic/device measurements. */
class WindowDetector {
  constructor(){this.length=Math.max(1,Math.round(sampleRate*.01));this.reset();}
  reset(){this.count=0;this.sum=0;this.hotSamples=0;this.runHotSamples=0;this.run=0;this.candidate=null;this.onset=null;this.lastVoice=null;this.detected=null;}
  sample(value,frame){
    if(this.count===0)this.windowStart=frame;
    this.sum+=value*value;if(value*value>=Math.pow(10,-45/10))this.hotSamples++;this.count++;
    if(this.count<this.length)return;
    const hot=this.sum/this.count>=Math.pow(10,-45/10);
    if(hot){if(this.run===0)this.candidate=this.windowStart;this.run++;this.runHotSamples+=this.hotSamples;
      if(this.run>=2&&this.runHotSamples>=Math.ceil(sampleRate*.02)){if(this.onset===null){this.onset=this.candidate;this.detected=frame;}this.lastVoice=frame;}}
    else{this.run=0;this.runHotSamples=0;this.candidate=null;}
    this.count=0;this.sum=0;this.hotSamples=0;
  }
}
class LabAudio extends AudioWorkletProcessor {
  constructor(options){
    super();this.graphId=options.processorOptions.graphId;this.lastQuantum=null;this.queue=[];this.queued=0;this.epoch=0;this.connection=0;
    this.probe=null;this.capture=false;this.captureOutstanding=0;this.closed=false;this.captureDetector=new WindowDetector();this.renderDetector=new WindowDetector();
    this.renderResponse=null;this.lastRenderedFrame=null;this.rendered=new Map();this.onsetSent=false;this.endpointSent=false;
    this.port.onmessage=({data})=>{
      if(data.type==='close'){this.probe=null;this.capture=false;this.queue=[];this.queued=0;this.closed=true;this.port.postMessage({type:'closed',frame:currentFrame});return;}
      if(data.type==='probe'){
        if(this.capture||this.queue.length||this.probe||data.graph_id!==this.graphId||!Number.isSafeInteger(data.id)||!['gap','offset','cancel'].includes(data.kind)||!Number.isSafeInteger(data.requested_frame)||!Number.isSafeInteger(data.gap_frames)||data.gap_frames<sampleRate*.02||data.gap_frames>sampleRate){this.port.postMessage({type:'probe_rejected',id:data.id,graph_id:this.graphId,frame:currentFrame});return;}
        const pulseFrames=Math.ceil(sampleRate*.04),start=Math.max(currentFrame,data.requested_frame);
        if(data.kind==='cancel'&&(!Number.isSafeInteger(data.cancel_after_frames)||data.cancel_after_frames<1||data.cancel_after_frames>=pulseFrames)){this.port.postMessage({type:'probe_rejected',id:data.id,graph_id:this.graphId,frame:currentFrame});return;}
        this.probe={id:data.id,graphId:this.graphId,sampleId:data.sample_id,kind:data.kind,requestedFrame:data.requested_frame,start,pulseFrames,gapFrames:data.gap_frames,
          cutoffFrame:data.kind==='cancel'?data.requested_frame+data.cancel_after_frames:null,
          second:start+pulseFrames+data.gap_frames,started:false,pulses:new Set(),firstFrame:null,lastFrame:null,nonSilentFrames:0};
        return;
      }
      if(data.type==='probe_cancel'){
        if(this.probe&&data.id===this.probe.id){
          const p=this.probe;this.probe=null;
          this.probeEvent('probe_fence_applied',p,currentFrame,{intended_cutoff_frame:data.intended_cutoff_frame??null,cancel_origin:'explicit_main_thread'});
          this.probeEvent('probe_canceled',p,currentFrame,{cancel_origin:'explicit_main_thread'});
        }return;
      }
      if(data.type==='fence'){
        if(this.probe){const p=this.probe;this.probe=null;this.probeEvent('probe_fence_applied',p,currentFrame,{cancel_origin:'session_fence'});this.probeEvent('probe_canceled',p,currentFrame,{cancel_origin:'session_fence'});}
        if(!Number.isSafeInteger(data.epoch)||!Number.isSafeInteger(data.connection)||data.epoch<this.epoch||data.connection<this.connection){this.port.postMessage({type:'invalid_fence'});return;}
        const acks=Array.from(this.rendered,([responseId,through])=>({responseId,through}));
        const droppedFrames=this.queued,lastOldRenderedFrame=this.lastRenderedFrame;
        this.epoch=data.epoch;this.connection=data.connection;this.queue=[];this.queued=0;this.lastRenderedFrame=null;
        this.renderResponse=null;this.renderDetector.reset();this.onsetSent=false;this.rendered.clear();
        this.port.postMessage({type:'fence_applied',frame:currentFrame,contextTime:currentFrame/sampleRate,epoch:this.epoch,connection:this.connection,acks,droppedFrames,lastOldRenderedFrame,boundary:'worklet_render_graph',acousticTail:'unknown'});
      }else if(data.type==='capture_ack'){this.captureOutstanding=Math.max(0,this.captureOutstanding-data.frames);
      }else if(data.type==='capture'){
        if(data.enabled&&!this.capture){this.captureDetector.reset();this.endpointSent=false;}
        this.capture=data.enabled;
      }else if(data.type==='audio'){
        if(data.epoch!==this.epoch||data.connection!==this.connection){this.port.postMessage({type:'stale_audio_discarded',responseId:data.responseId,frame:currentFrame,epoch:data.epoch,connection:data.connection});return;}
        if(!(data.samples instanceof Float32Array)||!data.map||!Number.isFinite(data.map.inputStart)||!(data.map.inputPerOutput>0)||!Number.isSafeInteger(data.map.inputLimit)||data.map.inputLimit<0){this.port.postMessage({type:'invalid_audio'});return;}
        if(this.queued+data.samples.length>sampleRate*2){this.port.postMessage({type:'overflow'});return;}
        if(data.samples.length){this.queue.push({...data,offset:0});this.queued+=data.samples.length;}
      }else if(data.type==='drain')this.queue.push({...data,marker:true});
    };
  }
  probeEvent(type,p,frame,extra={}){
    this.port.postMessage({type,id:p.id,probe_id:p.id,graph_id:p.graphId,sample_id:p.sampleId,
      requested_frame:p.requestedFrame,pulse_frames:p.pulseFrames,gap_frames:p.gapFrames,
      intended_cutoff_frame:p.cutoffFrame,frame,first_frame:p.firstFrame,last_frame:p.lastFrame,last_old_frame:p.lastFrame,
      non_silent_frames:p.nonSilentFrames,sample_rate:sampleRate,quantum:this.lastQuantum,
      observation:'observed_render_graph',boundary:'worklet_render_graph',...extra});
  }
  process(inputs,outputs){
    const output=outputs[0]?.[0];if(this.closed){if(output)output.fill(0);return false;}if(!output)return true;this.lastQuantum=output.length;
    const input=inputs[0]?.[0];
    if(this.capture&&input){
      if(this.captureOutstanding+input.length>sampleRate*2){this.capture=false;this.port.postMessage({type:'overflow',direction:'capture'});return true;}
      this.captureOutstanding+=input.length;
      for(let i=0;i<input.length;i++)this.captureDetector.sample(input[i],currentFrame+i);
      const d=this.captureDetector;
      const endpoint=d.lastVoice!==null&&!this.endpointSent&&currentFrame+input.length-1-d.lastVoice>=sampleRate*.6;
      if(endpoint)this.endpointSent=true;
      this.port.postMessage({type:'capture',samples:input.slice(),frame:currentFrame,contextTime:currentFrame/sampleRate,
        onsetFrame:d.onset,detectedFrame:d.detected,lastVoiceFrame:d.lastVoice,endpoint,endpointFrame:endpoint?currentFrame+input.length-1:null});
    }
    let ack=null,consumedFrames=0;
    for(let i=0;i<output.length;i++){
      if(this.probe){
        const p=this.probe,frame=currentFrame+i;
        // A scheduled cutoff is a graph-sample fence, checked before writing output.
        if(p.cutoffFrame!==null&&frame>=p.cutoffFrame){
          this.probeEvent('probe_fence_applied',p,frame,{cancel_origin:'scheduled_worklet'});
          this.probeEvent('probe_canceled',p,frame,{cancel_origin:'scheduled_worklet'});
          this.probe=null;output[i]=0;continue;
        }
        if(frame<p.start){output[i]=0;continue;}
        if(!p.started){p.started=true;this.probeEvent('probe_started',p,frame);}
        const second=p.kind==='gap'||p.kind==='offset',end=second?p.second+p.pulseFrames:p.start+p.pulseFrames;
        if(frame>=end){this.probeEvent('probe_done',p,frame);this.probe=null;output[i]=0;continue;}
        const pulse=frame<p.start+p.pulseFrames?0:second&&frame>=p.second?1:-1;
        const local=pulse===0?frame-p.start:frame-p.second;
        output[i]=pulse<0?0:.08*Math.sin(2*Math.PI*440*local/sampleRate);
        // Tap the exact sample written to this render graph buffer.
        if(Math.abs(output[i])>1e-8){
          if(p.firstFrame===null)p.firstFrame=frame;p.lastFrame=frame;p.nonSilentFrames++;
          if(!p.pulses.has(pulse)){p.pulses.add(pulse);this.probeEvent('probe_pulse',p,frame,{pulse});}
        }
        continue;
      }
      while(this.queue[0]?.marker){const marker=this.queue.shift();if(marker.epoch===this.epoch&&marker.connection===this.connection){if(ack){this.port.postMessage({...ack,consumedFrames,queuedFrames:this.queued,quantum:output.length});ack=null;consumedFrames=0;}this.port.postMessage({type:'drained',responseId:marker.responseId,epoch:this.epoch,connection:this.connection,frame:currentFrame+i});}}
      const item=this.queue[0];if(!item){output[i]=0;this.renderDetector.sample(0,currentFrame+i);continue;}
      if(item.epoch!==this.epoch||item.connection!==this.connection){this.queued-=item.samples.length-item.offset;this.queue.shift();i--;continue;}
      if(this.renderResponse!==item.responseId){this.renderResponse=item.responseId;this.renderDetector.reset();this.onsetSent=false;}
      output[i]=item.samples[item.offset++];this.lastRenderedFrame=currentFrame+i;this.queued--;consumedFrames++;
      // Exclusive source index: only samples whose delayed output reached the graph.
      const through=Math.min(item.map.inputLimit,Math.max(0,Math.floor(item.map.inputStart+item.offset*item.map.inputPerOutput)));
      const previous=this.rendered.get(item.responseId)??0;this.rendered.set(item.responseId,Math.max(previous,through));
      ack={type:'rendered',responseId:item.responseId,through:this.rendered.get(item.responseId),contextTime:(currentFrame+i+1)/sampleRate,frame:currentFrame+i+1,epoch:this.epoch,connection:this.connection};
      this.renderDetector.sample(output[i],currentFrame+i);
      if(!this.onsetSent&&this.renderDetector.onset!==null){this.onsetSent=true;this.port.postMessage({type:'render_onset',responseId:item.responseId,contextTime:this.renderDetector.onset/sampleRate,frame:this.renderDetector.onset,detectedFrame:this.renderDetector.detected,epoch:this.epoch,connection:this.connection,windowMs:10,sustainMs:20,minimumHotSamples:Math.ceil(sampleRate*.02),thresholdDbfs:-45,boundary:'worklet_render_graph'});}
      if(item.offset===item.samples.length)this.queue.shift();
    }
    if(ack)this.port.postMessage({...ack,consumedFrames,queuedFrames:this.queued,quantum:output.length});
    return true;
  }
}
registerProcessor('lab-audio',LabAudio);
