/* Render and capture only. Epochs are enforced here, at the render boundary. */
class LabAudio extends AudioWorkletProcessor {
  constructor() {
    super(); this.queue = []; this.queued = 0; this.epoch = 0; this.connection = 0;
    this.capture = false; this.rendered = new Map();
    this.port.onmessage = ({data}) => {
      if (data.type === 'fence') {
        this.epoch = data.epoch; this.connection = data.connection;
        this.queue = []; this.queued = 0;
        this.port.postMessage({type:'fence_applied', frame:currentFrame, epoch:this.epoch});
      } else if (data.type === 'capture') this.capture = data.enabled;
      else if (data.type === 'audio') {
        if (data.epoch !== this.epoch || data.connection !== this.connection) {
          this.port.postMessage({type:'stale_audio_discarded'}); return;
        }
        if (this.queued + data.samples.length > sampleRate * 2) {
          this.port.postMessage({type:'overflow'}); return;
        }
        this.queue.push({...data, offset:0}); this.queued += data.samples.length;
      }
    };
  }
  process(inputs, outputs) {
    const output = outputs[0]?.[0]; if (!output) return true;
    const input = inputs[0]?.[0];
    if (this.capture && input) this.port.postMessage({type:'capture', samples:input.slice(), frame:currentFrame});
    for (let i = 0; i < output.length; i++) {
      const item = this.queue[0]; if (!item) { output[i] = 0; continue; }
      if (item.epoch !== this.epoch || item.connection !== this.connection) {
        this.queued -= item.samples.length - item.offset; this.queue.shift(); i--; continue;
      }
      output[i] = item.samples[item.offset++]; this.queued--;
      const through = item.startSample + Math.floor(item.offset * 24000 / sampleRate);
      this.rendered.set(item.responseId, through);
      if (!item.onset && Math.abs(output[i]) > 0.0056) {
        item.onset = true; this.port.postMessage({type:'render_onset',responseId:item.responseId,contextTime:(currentFrame+i)/sampleRate});
      }
      if (item.offset === item.samples.length) {
        this.port.postMessage({type:'rendered',responseId:item.responseId,through,contextTime:(currentFrame+i)/sampleRate});
        this.queue.shift();
      }
    }
    return true;
  }
}
registerProcessor('lab-audio', LabAudio);
