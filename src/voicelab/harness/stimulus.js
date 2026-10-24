/* Installed only for an explicitly admitted, reviewed synthetic fixture cohort.
   Audio stays in memory and enters the real app's capture MediaStream/worklet. */
(() => {
  let context=null,destination=null,buffer=null,source=null,playing=false,closed=false;
  let complete=0,lastEnd=null,pendingReject=null;
  async function ensure(){
    if(closed)throw new Error('Fixture source closed');
    if(!context){context=new AudioContext({sampleRate:24000});destination=context.createMediaStreamDestination();}
    await context.resume();
  }
  navigator.mediaDevices.getUserMedia=async constraints=>{
    if(!constraints?.audio||constraints.video)throw new DOMException('Synthetic audio only','NotSupportedError');
    await ensure();
    return new MediaStream(destination.stream.getAudioTracks().map(track=>track.clone()));
  };
  Object.defineProperty(window,'__voiceLabFixtureSource',{value:Object.freeze({
    async load(bytes){
      if(buffer||playing)throw new Error('Fixture may be bound once');
      await ensure();buffer=await context.decodeAudioData(new Uint8Array(bytes).buffer);
      if(buffer.numberOfChannels!==1)throw new Error('Reviewed mono fixture required');
      return {sample_rate:buffer.sampleRate,frame_count:buffer.length,
        observation:'decoded_approved_fixture',boundary:'synthetic_media_stream'};
    },
    async play(options){
      await ensure();if(!buffer||playing)throw new Error('No idle bound fixture');
      if(!Number.isInteger(options.start_frame)||!Number.isInteger(options.end_frame)||
         options.start_frame<0||options.end_frame<=options.start_frame||options.end_frame>buffer.length)
        throw new Error('Fixture sample window refused');
      playing=true;source=context.createBufferSource();source.buffer=buffer;source.connect(destination);
      const start=options.start_frame/buffer.sampleRate,duration=(options.end_frame-options.start_frame)/buffer.sampleRate;
      await new Promise((resolve,reject)=>{
        pendingReject=reject;
        source.onended=()=>{source?.disconnect();source=null;playing=false;complete++;
          pendingReject=null;lastEnd={context_time:context.currentTime,frame_count:options.end_frame-options.start_frame};resolve();};
        try{source.start(context.currentTime+.05,start,duration);}catch(error){playing=false;reject(error);}
      });
      return {completed:complete,context_time:lastEnd.context_time,frame_count:lastEnd.frame_count,
        boundary:'synthetic_media_stream',observation:'source_completed_not_render_adjudication'};
    },
    snapshot(){return {playing,complete,closed,sample_rate:buffer?.sampleRate??null,
      owned_handles:{audio_context:context&&context.state!=='closed'?1:0,source:source?1:0,
        media_tracks:destination?.stream.getTracks().filter(track=>track.readyState==='live').length??0},
      boundary:'synthetic_media_stream',physical_audio:'unknown'};},
    async close(){
      closed=true;pendingReject?.(new Error('Fixture source abandoned'));pendingReject=null;
      if(source){source.onended=null;source.stop();source.disconnect();source=null;}
      playing=false;destination?.stream.getTracks().forEach(track=>track.stop());
      if(context&&context.state!=='closed')await context.close();buffer=null;destination=null;
      return this.snapshot();
    }
  }),writable:false});
})();
