// Apply the appearance change to the running mode without restarting it or showing hidden windows.
const BUS='org.interviewhelper.ClaudeInterview',PATH='/org/interviewhelper/ClaudeInterview';
callDBus(BUS,PATH,BUS,'Pids',function(value){
  const pids=JSON.parse(value);
  callDBus(BUS,PATH,BUS,'Status',function(){
    const rows=[];
    workspace.windowList().forEach(w=>{
      if(w.pid!==pids.claude&&w.pid!==pids.bridge)return;
      const aux=w.pid===pids.bridge;
      if(!aux)w.opacity=.90;
      rows.push({id:String(w.internalId),pid:w.pid,aux,minimized:w.minimized,
        keepAbove:w.keepAbove,excluded:w.excludeFromCapture,skipTaskbar:w.skipTaskbar,
        allDesktops:w.onAllDesktops,opacity:w.opacity});
    });
    const hidden=rows.length>0&&rows.every(w=>w.minimized);
    callDBus(BUS,PATH,BUS,'WindowState',JSON.stringify({hidden,capture:false,windows:rows}));
  });
});
