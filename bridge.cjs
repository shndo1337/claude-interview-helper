const {app, BrowserWindow, clipboard, ClipboardItem, utilityProcess, screen} = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const readline = require('node:readline');
const root = __dirname;
const shared = path.resolve(root, '../meetingly-review/app');
const runtime = process.env.CLAUDE_HELPER_RUNTIME || path.join(root, 'runtime');
fs.mkdirSync(runtime, {recursive: true, mode: 0o700});
app.setPath('userData', path.join(runtime, 'bridge-profile'));
const write = value => process.stdout.write(JSON.stringify(value) + '\n');
let worker, badge, ready = false;
const pending = new Map();
let clipboardBackup = null;
function speech(req, action, extra = {}) {
  if(!ready) throw Error('Распознавание ещё готовится');
  pending.set(req.id, true);
  worker.postMessage({id:req.id, action, recording:req.recording, ...extra});
}
async function rememberClipboard() {
  clipboardBackup=[];
  for(const item of await clipboard.read()){
    const data={};
    for(const type of item.types){
      try{const value=await item.getType(type);if(!value.size||value.size<=16*1024*1024)data[type]=value;}catch{}
    }
    if(Object.keys(data).length)clipboardBackup.push(new ClipboardItem(data));
  }
}
app.whenReady().then(() => {
  const area=screen.getPrimaryDisplay().workArea;
  badge=new BrowserWindow({x:area.x+area.width-225,y:area.y+12,width:210,height:30,
    title:'Claude Interview Status',frame:false,transparent:true,resizable:false,focusable:true,
    skipTaskbar:true,alwaysOnTop:true,show:false,
    webPreferences:{sandbox:true,contextIsolation:true,nodeIntegration:false}});
  badge.setMenu(null);
  badge.setIgnoreMouseEvents(true);
  badge.loadURL('data:text/html;charset=utf-8,'+encodeURIComponent(`<!doctype html><meta charset="utf-8">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
    <style>body{margin:0;font:13px system-ui;color:#eee;background:#222e;border-radius:9px;padding:6px 10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}</style>
    <body>● Подготовка…</body>`));
  badge.once('ready-to-show',()=>badge.showInactive());
  worker=utilityProcess.fork(path.join(root,'speech-worker.cjs'),[],{stdio:'ignore',serviceName:'Claude Interview speech'});
  worker.on('message',msg=>{
    if(msg.event==='ready'){ready=true;write(msg);}
    else if(pending.has(msg.id)){
      pending.delete(msg.id);write(msg);
    } else if(msg.event)write(msg);
  });
  worker.on('exit',code=>{
    ready=false;
    const error=`Речевой процесс завершился: ${code}`;
    for(const id of pending.keys())write({id,error});
    pending.clear();write({event:'error',error});
  });
  worker.postMessage({action:'start',libDir:path.join(shared,'vendor/transcribe/linux-x64'),
    modelPath:path.resolve(root,'../meetingly-local/models/nemotron-3.5-asr-streaming-0.6b-Q5_K_M.gguf'),threads:4});
  readline.createInterface({input:process.stdin}).on('line',async line=>{
    let req;
    try{
      req=JSON.parse(line);
      let result=true;
      switch(req.action){
        case 'stream-begin':speech(req,'begin');return;
        case 'stream-audio':{
          const pcm=Buffer.from(req.pcm,'base64');
          if(pcm.length>128000||pcm.length%4)throw Error('Некорректный аудиоблок');
          speech(req,'audio',{pcm});return;
        }
        case 'stream-end':speech(req,'end');return;
        case 'stream-abort':speech(req,'abort');return;
        case 'clipboard-text':await rememberClipboard();await clipboard.writeText(req.text);break;
        case 'clipboard-image':{
          const image=fs.readFileSync(req.file);
          if(!image.length)throw Error('Пустой скриншот');
          await rememberClipboard();
          await clipboard.write([new ClipboardItem({'image/png':new Blob([image],{type:'image/png'})})]);break;
        }
        case 'clipboard-restore':
          if(clipboardBackup){if(clipboardBackup.length)await clipboard.write(clipboardBackup);else clipboard.clear();clipboardBackup=null;}
          break;
        case 'status':
          badge.webContents.executeJavaScript(`document.body.textContent=${JSON.stringify(req.text)}`);
          // twm does not implement EWMH always-on-top; raise the status badge
          // within the private display without activating the host viewer.
          if(process.env.CLAUDE_HELPER_RUNTIME)badge.moveTop();
          break;
        case 'exit':app.quit();return;
        default:throw Error('Неизвестная команда');
      }
      write({id:req.id,result});
    }catch(error){write({id:req?.id,error:error.message});}
  }).on('close',()=>app.quit());
});
app.on('before-quit',()=>{if(worker)worker.kill();});
