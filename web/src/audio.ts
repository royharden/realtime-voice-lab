/** Pinned causal Blackman-windowed sinc SRC; no device calibration is implied. */
export const AUDIO_POLICY = Object.freeze({wireRate:24000, queueSeconds:2, filterHalfLength:64,
  cutoffNyquistFraction:0.90, windowMs:10, sustainMs:20, thresholdDbfs:-45, hangoverMs:600});
export interface SampleMap {
  outputStart:number; inputStart:number; inputPerOutput:number;
  delayInputSamples:number; delaySeconds:number; inputLimit:number;
}
export interface ResampledBlock {samples:Float32Array; map:SampleMap}
export class Resampler {
  private buffer:number[]=[]; private base=0; private received=0; private emitted=0; private finished=false;
  readonly delayInputSamples:number;
  constructor(readonly inputRate:number, readonly outputRate:number) {
    if(!Number.isFinite(inputRate)||!Number.isFinite(outputRate)||inputRate<8000||outputRate<8000||inputRate>384000||outputRate>384000)throw new Error('Unsupported sample rate');
    this.delayInputSamples=inputRate===outputRate?0:AUDIO_POLICY.filterHalfLength;
  }
  get delaySeconds(){return this.delayInputSamples/this.inputRate;}
  push(input:Float32Array):ResampledBlock {
    if(this.finished)throw new Error('Resampler already flushed');
    if(input.length>this.inputRate*AUDIO_POLICY.queueSeconds)throw new Error('Resampler input exceeds two seconds');
    for(const sample of input){if(!Number.isFinite(sample))throw new Error('Non-finite audio');this.buffer.push(sample);}
    this.received+=input.length;return this.produce(false);
  }
  /** Emit the causal filter tail once; zero padding is not added to inputLimit. */
  flush():ResampledBlock {if(this.finished)throw new Error('Resampler already flushed');this.finished=true;return this.produce(true);}
  private produce(tail:boolean):ResampledBlock {
    const start=this.emitted, step=this.inputRate/this.outputRate, radius=this.delayInputSamples;
    const limit=this.received+(tail?radius:0), result:number[]=[];
    const cutoff=.5*AUDIO_POLICY.cutoffNyquistFraction*Math.min(1,this.outputRate/this.inputRate);
    while(this.emitted*step<limit){
      const center=this.emitted*step-radius;let value=0,weights=0;
      if(radius===0)value=this.buffer[Math.round(center)-this.base]??0;
      else for(let index=Math.ceil(center-radius);index<=Math.floor(center+radius);index++){
        const distance=index-center, x=2*cutoff*distance;
        const sinc=Math.abs(x)<1e-12?1:Math.sin(Math.PI*x)/(Math.PI*x);
        const window=.42+.5*Math.cos(Math.PI*distance/radius)+.08*Math.cos(2*Math.PI*distance/radius);
        const weight=2*cutoff*sinc*window;
        weights+=weight;value+=(this.buffer[index-this.base]??0)*weight;
      }
      result.push(radius===0?value:value/weights);this.emitted++;
    }
    const keep=Math.max(0,Math.ceil(this.emitted*step-radius*2));
    const discard=Math.min(this.buffer.length,Math.max(0,keep-this.base));
    this.buffer.splice(0,discard);this.base+=discard;
    return {samples:Float32Array.from(result),map:{outputStart:start,inputStart:start*step-radius,
      inputPerOutput:step,delayInputSamples:radius,delaySeconds:this.delaySeconds,inputLimit:this.received}};
  }
}
export function encodePCM(input:Float32Array):string {
  const bytes=new Uint8Array(input.length*2),view=new DataView(bytes.buffer);
  input.forEach((sample,index)=>view.setInt16(index*2,Math.round(Math.max(-1,Math.min(1,sample))*(sample<0?32768:32767)),true));
  let binary='';for(const byte of bytes)binary+=String.fromCharCode(byte);return btoa(binary);
}
export function decodePCM(encoded:string):Float32Array {
  if(encoded.length>256000)throw new Error('Audio message exceeds bound');
  const binary=atob(encoded);if(binary.length%2)throw new Error('Odd PCM byte count');
  const bytes=Uint8Array.from(binary,char=>char.charCodeAt(0)),view=new DataView(bytes.buffer);
  return Float32Array.from({length:bytes.length/2},(_,index)=>view.getInt16(index*2,true)/32768);
}
