// Loaded only while the separate interview controller runs.
const BUS='org.interviewhelper.ClaudeInterview', PATH='/org/interviewhelper/ClaudeInterview', IFACE=BUS;
const windows=new Map();
const CHAT_OPACITY=0.90;
let pids={claude:0,bridge:0}, hidden=false, capture=false, restoring=false,lastMain=null;
function report(){
  const rows=[];
  windows.forEach(r=>rows.push({id:String(r.w.internalId),pid:r.w.pid,aux:r.aux,
    minimized:r.w.minimized,keepAbove:r.w.keepAbove,excluded:r.w.excludeFromCapture,
    skipTaskbar:r.w.skipTaskbar,allDesktops:r.w.onAllDesktops,opacity:r.w.opacity}));
  callDBus(BUS,PATH,IFACE,'WindowState',JSON.stringify({hidden,capture,windows:rows}));
}
function mainWindow(){
  const active=workspace.activeWindow;
  if(windows.has(active)&&!windows.get(active).aux){lastMain=active;return active;}
  if(lastMain&&windows.has(lastMain))return lastMain;
  let main=null,area=-1;windows.forEach(r=>{const g=r.w.frameGeometry;if(!r.aux&&g.width*g.height>area){area=g.width*g.height;main=r.w;}});return main;
}
function rememberActive(){const w=workspace.activeWindow;if(windows.has(w)&&!windows.get(w).aux)lastMain=w;}
if(workspace.windowActivated)workspace.windowActivated.connect(rememberActive);
else if(workspace.activeWindowChanged)workspace.activeWindowChanged.connect(rememberActive);
function enforce(r){
  if(restoring)return;
  const w=r.w;
  if(!w.keepAbove)w.keepAbove=true;
  if(!w.onAllDesktops)w.onAllDesktops=true;
  if(!w.excludeFromCapture)w.excludeFromCapture=true;
  if(!w.skipTaskbar)w.skipTaskbar=true;
  if(w.minimized!==hidden)w.minimized=hidden;
  if(capture&&w.opacity!==0)w.opacity=0;
  else if(!capture&&!r.aux&&w.opacity!==CHAT_OPACITY)w.opacity=CHAT_OPACITY;
}
function attach(w){
  if(!w||!w.managed||w.dock||w.desktopWindow||windows.has(w))return;
  const cls=String(w.resourceClass).toLowerCase();
  const aux=w.pid===pids.bridge||cls==='claude-interview-status';
  if(!aux&&w.pid!==pids.claude)return;
  const r={w,aux,old:{keepAbove:w.keepAbove,onAllDesktops:w.onAllDesktops,
    excludeFromCapture:w.excludeFromCapture,skipTaskbar:w.skipTaskbar,minimized:w.minimized,opacity:w.opacity}};
  windows.set(w,r);
  ['minimizedChanged','keepAboveChanged','desktopChanged','excludeFromCaptureChanged','skipTaskbarChanged'].forEach(name=>{
    if(w[name]&&w[name].connect)w[name].connect(()=>{enforce(r);report();});
  });
  enforce(r);report();
}
function refresh(){
  callDBus(BUS,PATH,IFACE,'Pids',function(value){
    try{
      pids=JSON.parse(value);
      workspace.windowList().forEach(w=>{
        attach(w);
        if(pids.permission&&String(w.resourceClass).indexOf('portal')>=0){w.keepAbove=true;workspace.activeWindow=w;}
      });
    }catch(e){print('Claude Interview:',String(e));}
  });
}
function show(){
  hidden=false;
  if(workspace.showingDesktop)workspace.showingDesktop=false;
  windows.forEach(enforce);
  const w=mainWindow();
  if(w)workspace.activeWindow=w;
  report();
}
workspace.windowAdded.connect(()=>refresh());
workspace.windowRemoved.connect(w=>{windows.delete(w);report();});
if(workspace.showingDesktopChanged)workspace.showingDesktopChanged.connect(()=>{
  if(!hidden&&!restoring&&mainWindow()&&workspace.showingDesktop)workspace.showingDesktop=false;
});
registerShortcut('Claude Interview Toggle','Claude Interview — скрыть / показать','Ctrl+Alt+H',()=>{
  hidden=!hidden;
  windows.forEach(enforce);
  if(!hidden)show();
  report();
});
registerShortcut('Claude Interview Voice','Claude Interview — запись / вставить текст','Ctrl+Alt+V',()=>callDBus(BUS,PATH,IFACE,'Action','voice'));
registerShortcut('Claude Interview Screenshot','Claude Interview — скрин в чат','Ctrl+Shift+S',()=>callDBus(BUS,PATH,IFACE,'Action','screenshot'));
registerShortcut('Claude Interview Scroll Up','Claude Interview — чат вверх','Ctrl+Alt+W',()=>callDBus(BUS,PATH,IFACE,'Action','scroll-up'));
registerShortcut('Claude Interview Scroll Down','Claude Interview — чат вниз','Ctrl+Alt+S',()=>callDBus(BUS,PATH,IFACE,'Action','scroll-down'));
registerShortcut('Claude Interview Send','Claude Interview — отправить сообщение','Ctrl+Alt+Return',()=>callDBus(BUS,PATH,IFACE,'Action','send'));
registerShortcut('Claude Interview Focus','Claude Interview — фокус для вставки','',show);
registerShortcut('Claude Interview Capture Begin','Claude Interview — начало снимка','',()=>{
  capture=true;windows.forEach(r=>{r.captureOpacity=r.w.opacity;r.w.opacity=0;});report();
});
registerShortcut('Claude Interview Capture End','Claude Interview — конец снимка','',()=>{
  capture=false;windows.forEach(r=>{r.w.opacity=r.captureOpacity??(r.aux?r.old.opacity:CHAT_OPACITY);});report();
});
registerShortcut('Claude Interview Restore','Claude Interview — восстановить обычные окна','',()=>{
  restoring=true;
  windows.forEach(r=>{Object.keys(r.old).forEach(k=>{r.w[k]=r.old[k];});});report();
});
refresh();
