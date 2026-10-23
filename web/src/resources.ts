/** One start attempt owns its resources, including late permission results and async closes. */
export class OwnedResources {
  canceled=false;pendingAcquisitions=0;pendingClosures=0;cleanupErrors=0;
  private streams=new Set<MediaStream>();private contexts=new Set<AudioContext>();
  private nodes=new Set<AudioNode>();private worklets=new Set<AudioWorkletNode>();
  private sockets=new Set<WebSocket>();private timers=new Set<ReturnType<typeof setTimeout>>();
  private urls=new Set<string>();private rafs=new Set<number>();private removers=new Set<()=>void>();
  private cancelWaits=new Set<()=>void>();
  constructor(private changed:()=>void){}
  async acquireMedia(request:()=>Promise<MediaStream>):Promise<MediaStream|undefined>{
    this.pendingAcquisitions++;this.changed();
    try{const stream=await request();this.streams.add(stream);if(this.canceled){this.stopStream(stream);return undefined;}return stream;}
    finally{this.pendingAcquisitions--;this.changed();}
  }
  addContext(value:AudioContext){this.contexts.add(value);this.changed();return value;}
  addNode<T extends AudioNode>(value:T){this.nodes.add(value);this.changed();return value;}
  addWorklet(value:AudioWorkletNode){this.worklets.add(value);this.changed();return value;}
  addSocket(value:WebSocket){
    this.sockets.add(value);
    value.addEventListener('close',()=>{this.sockets.delete(value);this.changed();},{once:true});
    this.changed();return value;
  }
  listen(target:EventTarget,type:string,handler:EventListener){
    target.addEventListener(type,handler);this.removers.add(()=>target.removeEventListener(type,handler));this.changed();
  }
  timer(callback:()=>void,ms:number){
    if(this.canceled)throw new Error('Resource scope canceled');
    const id=setTimeout(()=>{this.timers.delete(id);this.changed();if(!this.canceled)callback();},ms);
    this.timers.add(id);this.changed();return id;
  }
  clearTimer(id:ReturnType<typeof setTimeout>|undefined){if(id===undefined)return;clearTimeout(id);this.timers.delete(id);this.changed();}
  delay(ms:number):Promise<boolean>{return new Promise(resolve=>{
    if(this.canceled){resolve(false);return;}
    const cancel=()=>resolve(false);this.cancelWaits.add(cancel);
    this.timer(()=>{this.cancelWaits.delete(cancel);resolve(true);},ms);
  });}
  raf(callback:FrameRequestCallback){
    if(this.canceled)throw new Error('Resource scope canceled');
    const id=requestAnimationFrame(at=>{this.rafs.delete(id);this.changed();if(!this.canceled)callback(at);});
    this.rafs.add(id);this.changed();return id;
  }
  objectUrl(blob:Blob){if(this.canceled)throw new Error('Resource scope canceled');const url=URL.createObjectURL(blob);this.urls.add(url);this.changed();return url;}
  private attempt(action:()=>void){try{action();}catch{this.cleanupErrors++;}}
  private stopStream(stream:MediaStream){
    for(const track of stream.getTracks())this.attempt(()=>track.stop());
    if(stream.getTracks().every(track=>track.readyState==='ended'))this.streams.delete(stream);
  }
  close(){
    if(this.canceled)return;this.canceled=true;
    for(const cancel of this.cancelWaits)cancel();this.cancelWaits.clear();
    for(const id of this.timers)clearTimeout(id);this.timers.clear();
    for(const id of this.rafs)cancelAnimationFrame(id);this.rafs.clear();
    for(const remove of this.removers)this.attempt(remove);this.removers.clear();
    for(const url of this.urls)this.attempt(()=>URL.revokeObjectURL(url));this.urls.clear();
    for(const node of this.worklets){
      node.onprocessorerror=null;
      this.attempt(()=>node.port.postMessage({type:'close'}));node.port.onmessage=null;
      this.attempt(()=>node.disconnect());this.attempt(()=>node.port.close());
    }
    // Worklets remain tracked until their owning context reports closed.
    for(const node of this.nodes)this.attempt(()=>node.disconnect());this.nodes.clear();
    for(const stream of this.streams)this.stopStream(stream);
    for(const socket of this.sockets){socket.onopen=null;socket.onmessage=null;socket.onerror=null;socket.onclose=null;
      if(socket.readyState===WebSocket.CLOSED)this.sockets.delete(socket);else this.attempt(()=>socket.close(1000,'client cleanup'));}
    for(const context of this.contexts){
      if(context.state==='closed'){this.contexts.delete(context);continue;}
      this.pendingClosures++;
      try{void context.close().then(()=>{this.contexts.delete(context);},()=>{this.cleanupErrors++;}).finally(()=>{
        this.pendingClosures--;if(this.contexts.size===0)this.worklets.clear();this.changed();});}
      catch{this.pendingClosures--;this.cleanupErrors++;}
    }
    if(this.contexts.size===0)this.worklets.clear();this.changed();
  }
  snapshot(){return {media_tracks:[...this.streams].reduce((n,s)=>n+s.getTracks().filter(t=>t.readyState!=='ended').length,0),
    audio_context:this.contexts.size,worklet:this.worklets.size,source:this.nodes.size,socket:this.sockets.size,
    timers:this.timers.size,raf:this.rafs.size,object_urls:this.urls.size,listeners:this.removers.size,pending_permission:this.pendingAcquisitions,
    pending_context_close:this.pendingClosures,cleanup_errors:this.cleanupErrors};}
  get settled(){return this.canceled&&Object.values(this.snapshot()).every(value=>value===0);}
}
