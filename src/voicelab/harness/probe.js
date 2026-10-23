/* Installed only by the explicitly activated development driver, before navigation.
   Exercises real browser APIs/worklet/transport; never supplies a fake task verdict. */
(() => {
  const sockets = new Set(), tracks = new Set(), nodes = new Set();
  let deny = false, dropAck = false, lastAudio = null, injected = 0, responseDone = 0;
  let cancelAfterCommit = false, cancelTriggered = 0;
  let fenceCounter = 0, lastFence = null;
  let staleDiscards = 0;
  let cancelAcks = 0, resumeCount = 0, resumePending = null, lastClose = null;
  let observedSession = null;
  const canceledEpochs = new Set();
  const NativeSocket = window.WebSocket, NativeNode = window.AudioWorkletNode;
  const gum = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  function prune() {
    for(const socket of sockets)if(socket.readyState===NativeSocket.CLOSED)sockets.delete(socket);
    for(const track of tracks)if(track.readyState==='ended')tracks.delete(track);
    for(const node of nodes)if(node.context.state==='closed')nodes.delete(node);
  }
  navigator.mediaDevices.getUserMedia = async (...args) => {
    if (deny) throw new DOMException('Development permission fault', 'NotAllowedError');
    const stream = await gum(...args);
    for (const track of stream.getTracks()) tracks.add(track);
    return stream;
  };
  window.WebSocket = class extends NativeSocket {
    constructor(url, protocols) {
      const parsed = new URL(url);
      if (parsed.hostname !== '127.0.0.1' || parsed.protocol !== 'ws:' ||
          Number(parsed.port) < 20000 || Number(parsed.port) > 29999)
        throw new Error('Development driver refuses external websocket');
      super(url, protocols); sockets.add(this);
      this.addEventListener('message', event => {
        try { const value = JSON.parse(event.data);
          if(value.type==='ready' && value.session_id!==observedSession) {
            observedSession=value.session_id;canceledEpochs.clear();lastAudio=null;resumePending=null;lastClose=null;prune();
          }
          if(value.type === 'audio') lastAudio = value;
          if(value.type === 'event' && value.payload?.kind === 'response_done') responseDone++;
          if(value.type==='state' && Number.isSafeInteger(value.payload?.fence_epoch) &&
              !canceledEpochs.has(value.payload.fence_epoch)) {
            canceledEpochs.add(value.payload.fence_epoch);cancelAcks++;
          }
          if(resumePending!==null && value.type==='state' && value.payload?.state==='listening' &&
              value.connection_epoch===resumePending) {resumeCount++;resumePending=null;}
          if(cancelAfterCommit && value.type === 'task' && value.payload?.effect_committed === true) {
            cancelAfterCommit=false;
            queueMicrotask(()=>{document.querySelector('#interrupt')?.click();cancelTriggered++;injected++;});
          }
        } catch { /* application parser owns malformed input outcome */ }
      });
      this.addEventListener('close',event=>{lastClose={code:event.code,reason:event.reason};});
    }
    send(raw) {
      let value; try { value = JSON.parse(raw); } catch { return super.send(raw); }
      if (dropAck && value.type === 'rendered') { injected++; return; }
      if(value.type==='resume')resumePending=value.connection_epoch;
      return super.send(raw);
    }
  };
  window.AudioWorkletNode = class extends NativeNode {
    constructor(...args) {
      super(...args); nodes.add(this);
      this.port.addEventListener('message',event=>{
        if(event.data?.type==='fence_applied') {
          fenceCounter++; lastFence={epoch:event.data.epoch,connection:event.data.connection,frame:event.data.frame};
        }
        if(event.data?.type==='stale_audio_discarded')staleDiscards++;
      });
    }
  };
  const activeSocket = () => [...sockets].find(s => s.readyState === NativeSocket.OPEN);
  Object.defineProperty(window, '__voiceLabDriver', {value: Object.freeze({
    configure(options) {
      if('permissionDenied' in options)deny=options.permissionDenied===true;
      if('dropRenderedAck' in options)dropAck=options.dropRenderedAck===true;
      if('cancelAfterCommit' in options)cancelAfterCommit=options.cancelAfterCommit===true;
    },
    snapshot() { prune();return {injected,responseDone,cancelTriggered,fenceCounter,lastFence,cancelAcks,resumeCount,lastClose,staleDiscards,
      liveTracks:[...tracks].filter(t=>t.readyState==='live').length,
      openSockets:[...sockets].filter(s=>s.readyState<2).length,
      instrumentation:'driver-owned media/socket observations; app timer/worklet cleanup requires D6 snapshot'}; },
    graphStamp() {
      const node=[...nodes].reverse().find(node=>node.context.state==='running');
      if(!node)throw new Error('No running owned graph');
      const context=node.context,stamp=context.getOutputTimestamp();
      return {sample_rate:context.sampleRate,context_time:context.currentTime,
        graph_id:window.__voiceLabDiagnostics?.snapshot().graph_id??null,
        output_timestamp:{contextTime:stamp.contextTime,performanceTime:stamp.performanceTime},
        output_latency_seconds:context.outputLatency??null,boundary:'browser_output_timestamp_estimate',
        acoustic_uncertainty:'unknown'};
    },
    staleControl() {
      const node=[...nodes].reverse().find(node=>node.context.state==='running');
      if(!node || !lastFence || lastFence.epoch<1)throw new Error('No applied current fence');
      node.port.postMessage({type:'audio',samples:new Float32Array(16),epoch:lastFence.epoch-1,
        connection:lastFence.connection,responseId:'diagnostic-stale',
        map:{inputStart:0,inputPerOutput:24000/node.context.sampleRate,inputLimit:16}});
      return {response_epoch:lastFence.epoch-1,connection_epoch:lastFence.connection};
    },
    fault(kind) {
      if(kind==='device_lost' || kind==='device_muted') {
        const track=[...tracks].find(t=>t.readyState==='live');
        if(!track) throw new Error('No active capture track');
        if(kind==='device_lost') { track.stop(); track.dispatchEvent(new Event('ended')); }
        else track.dispatchEvent(new Event('mute'));
      } else {
        const socket=activeSocket(); if(!socket)throw new Error('No active socket');
        if(kind==='malformed_protocol') socket.dispatchEvent(new MessageEvent('message',{data:'{invalid'}));
        else if(kind==='duplicate_sequence' && lastAudio)
          socket.dispatchEvent(new MessageEvent('message',{data:JSON.stringify(lastAudio)}));
        else if(kind==='queue_overrun' && lastAudio) {
          const m=lastAudio.payload||lastAudio, envelope=lastAudio;
          const node=[...nodes].at(-1); if(!node)throw new Error('No active worklet');
          node.port.postMessage({type:'audio',samples:new Float32Array(Math.ceil(node.context.sampleRate*3)),
            epoch:envelope.response_epoch,connection:envelope.connection_epoch,
            responseId:m.response_id,map:{inputStart:0,inputPerOutput:24000/node.context.sampleRate,inputLimit:72000}});
        } else throw new Error('Fault prerequisite absent or unsupported');
      }
      injected++; return {kind,injected,observation:'observed',scope:'synthetic_browser_boundary'};
    }
  })});
})();
