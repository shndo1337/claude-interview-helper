// Uses a public FLEURS clip. No microphone, screen capture or Claude requests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {fork} = require('node:child_process');
const {setTimeout: sleep} = require('node:timers/promises');
const root = path.resolve(__dirname, '../..');
const shared = path.resolve(root, '../meetingly-review/app');
const audio = fs.readFileSync(path.resolve(root, '../meetingly-local/fleurs-russian.wav'));
let mono;
for(let offset=12; offset+8<=audio.length;) {
  const size=audio.readUInt32LE(offset+4);
  if(audio.toString('ascii',offset,offset+4)==='data') {mono=audio.subarray(offset+8,offset+8+size);break;}
  offset+=8+size+(size%2);
}
assert(mono?.length);
fs.mkdirSync(path.join(root, 'runtime'), {recursive: true, mode: 0o700});
const log = fs.openSync(path.join(root,'runtime/streaming-test-native.log'),'w');
const worker = fork(path.join(root,'interview_helper/speech/speech-worker.cjs'),[],{
  stdio:['ignore','ignore',log,'ipc'],serialization:'advanced',
});
const pending = new Map(), events = [];
let next=1, readyResolve, readyReject;
const ready = new Promise((resolve,reject)=>{readyResolve=resolve;readyReject=reject;});
const readyTimer = setTimeout(()=>readyReject(Error('Model load timeout')),30000);
worker.on('message',msg=>{
  if(msg.event==='ready') {clearTimeout(readyTimer);readyResolve();}
  else if(msg.event==='error') {clearTimeout(readyTimer);readyReject(Error(msg.error));}
  else if(pending.has(msg.id)) {
    const {resolve,reject,timer}=pending.get(msg.id);pending.delete(msg.id);clearTimeout(timer);
    msg.error ? reject(Error(msg.error)) : resolve(msg.result);
  } else if(msg.event) events.push({...msg, wallMs:performance.now()});
});
worker.on('exit',code=>{
  readyReject(Error('Worker exited: '+code));
  for(const {reject,timer} of pending.values()) {clearTimeout(timer);reject(Error('Worker exited: '+code));}
  pending.clear();
});
function request(action,extra={}) {
  const id=next++;
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{pending.delete(id);reject(Error(action+' timed out'));},30000);
    pending.set(id,{resolve,reject,timer});worker.send({id,action,...extra});
  });
}
function stereo(left,right) {
  const count=Math.max(left.length,right.length)/2;
  const out=Buffer.alloc(count*4);
  for(let i=0;i<count;i++) {
    if(i*2<left.length) out.writeInt16LE(left.readInt16LE(i*2),i*4);
    if(i*2<right.length) out.writeInt16LE(right.readInt16LE(i*2),i*4+2);
  }
  return out;
}
async function recording(name,pcm,paced=false) {
  await request('begin',{recording:name});
  const started=performance.now(), before=events.length;
  let maxRequestMs=0;
  for(let at=0;at<pcm.length;at+=10240) {
    if(paced) await sleep(Math.max(0,started+at/64-performance.now()));
    const t=performance.now();
    await request('audio',{recording:name,pcm:pcm.subarray(at,at+10240)});
    maxRequestMs=Math.max(maxRequestMs,performance.now()-t);
  }
  if(paced) await sleep(Math.max(0,started+pcm.length/64-performance.now()));
  const stop=performance.now();
  const partials=events.slice(before).filter(e=>e.event==='partial');
  const finals=events.slice(before).filter(e=>e.event==='utterance');
  const result=await request('end',{recording:name});
  return {...result,stopLatencyMs:Math.round(performance.now()-stop),maxBlockMs:Math.round(maxRequestMs),
    firstPartialMs:partials.length?Math.round(partials[0].wallMs-started):null,
    partialsBeforeStop:partials.length,finalsBeforeStop:finals.length};
}

(async()=>{
  const report={};
  try {
    worker.send({action:'start',libDir:path.join(shared,'vendor/transcribe/linux-x64'),
      modelPath:path.resolve(root,'../meetingly-local/models/nemotron-3.5-asr-streaming-0.6b-Q5_K_M.gguf'),threads:4});
    await ready;
    const system=await recording('system-paced',stereo(Buffer.alloc(mono.length),mono),true);
    assert(system.text.length>80,'Speech missing');
    assert(system.partialsBeforeStop>0,'Recognition did not happen while recording');
    assert(system.firstPartialMs<mono.length/32,'First transcript arrived after recording stopped');
    assert.deepEqual(system.channels,[1]);
    report.systemPaced=system;console.log('system-paced',JSON.stringify(system));
    const silence=await recording('silence',Buffer.alloc(64000));
    assert.equal(silence.text,'','Previous recording leaked into next one');
    report.silenceReset=true;
    const mic=await recording('microphone',stereo(mono,Buffer.alloc(mono.length)));
    assert(mic.text.length>80);assert.deepEqual(mic.channels,[0]);
    report.microphone=mic;console.log('microphone',JSON.stringify(mic));
    // Immediate stop mid-recording must flush the native model's lookahead.
    const short=await recording('short-tail',stereo(Buffer.alloc(5*32000),mono.subarray(0,5*32000)));
    assert(short.text.length>10);report.shortTail=short;
    // Separate, sequential turns must be retained and labelled in their original order.
    const zeros=Buffer.alloc(mono.length), pause=Buffer.alloc(32000*2);
    const both=await recording('both',Buffer.concat([stereo(mono,zeros),stereo(pause,pause),stereo(zeros,mono)]));
    assert.deepEqual([...both.channels].sort(),[0,1]);
    assert(both.text.startsWith('Микрофон: '));assert(both.text.includes('\nЗвук компьютера: '));
    report.bothChannels=both;console.log('both-channels',JSON.stringify(both));
    await request('begin',{recording:'cancelled'});
    await request('audio',{recording:'cancelled',pcm:stereo(mono.subarray(0,160000),Buffer.alloc(160000))});
    await request('abort');
    assert.equal((await recording('after-abort',Buffer.alloc(32000))).text,'');
    await assert.rejects(request('audio',{recording:'cancelled',pcm:Buffer.alloc(10240)}),/завершена/);
    report.abortIsolation=true;report.staleAudioRejected=true;report.ok=true;
    fs.writeFileSync(path.join(root,'streaming-verification.json'),JSON.stringify(report,null,2));
    console.log('streaming-verification: passed');
  } finally {
    clearTimeout(readyTimer);worker.kill();fs.closeSync(log);
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
