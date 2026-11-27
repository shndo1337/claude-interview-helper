// Nemotron streaming, with one persistent model and separate microphone/call sessions.
// Uses the same transcribe.cpp 0.2.4 ABI as Meetingly's asr-worker.ts.
const path = require('node:path');
const koffi = require(path.resolve(__dirname, '../../../meetingly-review/app/node_modules/koffi'));
const port = process.parentPort || {
  postMessage: value => process.send(value),
  on: (_event, listener) => process.on('message', data => listener({data})),
};
const post = value => port.postMessage(value);
const RATE = 16000;
const BLOCK = 2560;
let engine, channels = [], recording = null, turns = [], computeMs = 0;

function load({libDir, modelPath, threads = 4}) {
  const lib = koffi.load(path.join(libDir, 'libtranscribe.so'));
  const Ext = koffi.struct('transcribe_ext', {size:'uint64', kind:'uint32'});
  const StreamExt = koffi.struct('transcribe_parakeet_stream_ext', {ext:Ext, att_context_right:'int32'});
  koffi.struct('transcribe_model_load_params', {struct_size:'uint64', backend:'int', device:'void *'});
  koffi.struct('transcribe_session_params', {struct_size:'uint64', n_threads:'int', kv_type:'int', n_ctx:'int32'});
  koffi.struct('transcribe_run_params', {
    struct_size:'uint64', task:'int', timestamps:'int', pnc:'int', itn:'int', diarize:'int',
    language:'const char *', target_language:'const char *', keep_special_tags:'bool', family:'void *', spec_k_drafts:'int32',
  });
  koffi.struct('transcribe_stream_params', {
    struct_size:'uint64', family:'void *', commit_policy:'int', stable_prefix_agreement_n:'uint32',
  });
  koffi.struct('transcribe_stream_update', {
    struct_size:'uint64', result_changed:'bool', is_final:'bool', revision:'int32', input_received_ms:'int64',
    audio_committed_ms:'int64', buffered_ms:'int64', committed_changed:'bool', tentative_changed:'bool',
  });
  koffi.struct('transcribe_stream_text', {
    struct_size:'uint64', full_text:'const char *', full_text_bytes:'uint64', committed_text:'const char *',
    committed_text_bytes:'uint64', tentative_text:'const char *', tentative_text_bytes:'uint64', raw_tentative_start_bytes:'uint64',
  });
  const fn = signature => lib.func(signature);
  const status = fn('const char *transcribe_status_string(int status)');
  const check = (code, operation) => {if(code !== 0) throw Error(`${operation}: ${status(code)}`);};
  check(fn('int transcribe_init_backends(const char *dir)')(libDir), 'loading compute backends');
  const mp = {};
  fn('void transcribe_model_load_params_init(_Out_ transcribe_model_load_params *p)')(mp);
  mp.backend = Number(process.env.MEETINGLY_ASR_BACKEND || 1);
  const handle = [null];
  check(fn('int transcribe_model_load_file(const char *path, transcribe_model_load_params *p, _Out_ void **out)')(modelPath, mp, handle), 'loading speech model');
  const ext = koffi.alloc(StreamExt, 1);
  koffi.encode(ext, StreamExt, {ext:{size:koffi.sizeof(StreamExt), kind:1414744912}, att_context_right:6});
  const sessionInit = fn('int transcribe_session_init(void *model, transcribe_session_params *p, _Out_ void **out)');
  const sessionParams = fn('void transcribe_session_params_init(_Out_ transcribe_session_params *p)');
  const runParams = fn('void transcribe_run_params_init(_Out_ transcribe_run_params *p)');
  const streamParams = fn('void transcribe_stream_params_init(_Out_ transcribe_stream_params *p)');
  const updateInit = fn('void transcribe_stream_update_init(_Out_ transcribe_stream_update *u)');
  const textInit = fn('void transcribe_stream_text_init(_Out_ transcribe_stream_text *t)');
  const begin = fn('int transcribe_stream_begin(void *session, transcribe_run_params *rp, transcribe_stream_params *sp)');
  const feed = fn('int transcribe_stream_feed(void *session, float *pcm, int n, _Inout_ transcribe_stream_update *u)');
  const finalize = fn('int transcribe_stream_finalize(void *session, _Inout_ transcribe_stream_update *u)');
  const read = fn('int transcribe_stream_get_text(void *session, _Inout_ transcribe_stream_text *t)');
  const reset = fn('void transcribe_stream_reset(void *session)');
  const sessionFree = fn('void transcribe_session_free(void *session)');
  const modelFree = fn('void transcribe_model_free(void *model)');
  const sessions = [];
  return {
    session() {
      const params = {}, out = [null];
      sessionParams(params); params.n_threads = Math.max(1, Math.min(4, threads));
      check(sessionInit(handle[0], params, out), 'opening speech session');
      const session = out[0]; sessions.push(session);
      return {
        begin() {
          reset(session);
          const rp = {}, sp = {};
          runParams(rp); rp.language = 'ru-RU'; streamParams(sp); sp.family = ext;
          check(begin(session, rp, sp), 'starting speech stream');
        },
        feed(pcm) {const u = {}; updateInit(u); check(feed(session, pcm, pcm.length, u), 'transcribing live audio');},
        text() {
          const t = {}; textInit(t); check(read(session, t), 'reading live transcript');
          return String(t.full_text || `${t.committed_text || ''}${t.tentative_text || ''}`)
            .replace(/<[a-z]{2}-[A-Z]{2}>|<\|[^|>]*\|>/g, '').replace(/\s+/g, ' ').trim();
        },
        finalize() {const u = {}; updateInit(u); check(finalize(session, u), 'finishing speech stream'); return this.text();},
        reset() {reset(session);},
      };
    },
    close() {for(const s of sessions) sessionFree(s); modelFree(handle[0]); koffi.free(ext);},
  };
}

class Channel {
  constructor(asr, channel) {this.asr = asr; this.channel = channel;}
  begin() {
    this.asr.begin(); this.audioMs = 0; this.segmentStart = null; this.text = '';
    this.lastChange = 0; this.fed = false; this.segmentMs = 0;
  }
  push(pcm) {
    this.audioMs += pcm.length * 1000 / RATE;
    this.segmentMs += pcm.length * 1000 / RATE;
    let energy = 0;
    for(const sample of pcm) energy += sample * sample;
    if(Math.sqrt(energy / pcm.length) >= 1e-4) {
      if(this.segmentStart === null) this.segmentStart = this.audioMs - pcm.length * 1000 / RATE;
      this.asr.feed(pcm); this.fed = true;
    }
    const text = this.asr.text();
    if(text !== this.text) {
      if(!this.text && text) this.segmentStart = this.audioMs;
      this.text = text; this.lastChange = this.audioMs;
      if(text) post({event:'partial', recording, channel:this.channel, characters:text.length, audioMs:this.audioMs});
    }
    const quiet = this.audioMs - this.lastChange;
    if((text && (quiet >= 1500 || (/[.?!。？！]["')\]]?$/.test(text) && quiet >= 700))) || (this.fed && this.segmentMs >= 30000)) {
      this.finish(true);
    }
  }
  finish(restart = false) {
    if(this.fed) {
      const text = this.asr.finalize();
      if(text) {
        const turn = {channel:this.channel, startMs:this.segmentStart ?? this.audioMs, endMs:this.audioMs, text};
        turns.push(turn);
        post({event:'utterance', recording, channel:this.channel, characters:text.length, audioMs:this.audioMs});
      }
    }
    this.asr.reset();
    this.text = ''; this.fed = false; this.segmentStart = null; this.segmentMs = 0;
    this.lastChange = this.audioMs;
    if(restart) this.asr.begin();
  }
}

port.on('message', ({data:msg}) => {
  const started = performance.now();
  try {
    if(msg.action === 'start') {
      engine = load(msg);
      channels = [new Channel(engine.session(), 0), new Channel(engine.session(), 1)];
      post({event:'ready'}); return;
    }
    if(msg.action === 'begin') {
      if(recording) throw Error('Запись уже идёт');
      recording = msg.recording; turns = []; computeMs = 0;
      for(const channel of channels) channel.begin();
      post({id:msg.id, result:true}); return;
    }
    if(msg.action === 'audio') {
      if(!recording || msg.recording !== recording) throw Error('Запись уже завершена');
      const bytes = Buffer.from(msg.pcm);
      if(bytes.length % 4) throw Error('Некорректный размер аудиоблока');
      const frames = bytes.length / 4;
      for(let offset = 0; offset < frames; offset += BLOCK) {
        const count = Math.min(BLOCK, frames - offset);
        const left = new Float32Array(count), right = new Float32Array(count);
        for(let i = 0; i < count; i++) {
          left[i] = bytes.readInt16LE((offset + i) * 4) / 32768;
          right[i] = bytes.readInt16LE((offset + i) * 4 + 2) / 32768;
        }
        channels[0].push(left); channels[1].push(right);
      }
      computeMs += performance.now() - started;
      post({id:msg.id, result:{audioMs:channels[0].audioMs}}); return;
    }
    if(msg.action === 'end') {
      if(!recording || msg.recording !== recording) throw Error('Нет активной записи');
      const completedBeforeStop = turns.length;
      for(const channel of channels) channel.finish();
      turns.sort((a,b) => a.startMs - b.startMs || a.channel - b.channel);
      const both = new Set(turns.map(t => t.channel)).size > 1;
      const text = turns.map(t => (both ? (t.channel ? 'Звук компьютера: ' : 'Микрофон: ') : '') + t.text).join('\n');
      const ms = performance.now() - started;
      const result = {text, ms:Math.round(ms), computeMs:Math.round(computeMs + ms),
        audioMs:Math.round(channels[0].audioMs), completedBeforeStop, utterances:turns.length,
        channels:[...new Set(turns.map(t => t.channel))]};
      recording = null; turns = [];
      post({id:msg.id, result}); return;
    }
    if(msg.action === 'abort') {
      for(const channel of channels) channel.asr.reset();
      recording = null; turns = []; post({id:msg.id, result:true}); return;
    }
    throw Error('Неизвестная команда распознавания');
  } catch(error) {
    if(msg.id !== undefined) post({id:msg.id, error:error.message});
    else post({event:'error', error:error.message});
  }
});

process.on('exit', () => {try {engine?.close();} catch {}});
