const {app,BrowserWindow,ipcMain}=require('electron');
const fs=require('node:fs'),path=require('node:path');
const runtime=process.env.CLAUDE_HELPER_RUNTIME||path.resolve(__dirname,'../../runtime');
app.setPath('userData',path.join(runtime,'fixture-profile'));
app.whenReady().then(()=>{
  ipcMain.on('fixture:state',(_e,state)=>fs.writeFileSync(path.join(runtime,'fixture-state.json'),JSON.stringify(state,null,2)));
  const win=new BrowserWindow({width:1000,height:740,title:'Claude Interview — local test',
    webPreferences:{preload:path.join(__dirname,'fixture-preload.cjs'),sandbox:true,contextIsolation:true}});
  win.webContents.on('before-input-event',(_event,input)=>fs.appendFileSync(path.join(runtime,'fixture-keys.log'),JSON.stringify({type:input.type,key:input.key,control:input.control,alt:input.alt})+'\n'));
  win.loadFile(path.join(__dirname,'fixture.html'));
});
app.on('window-all-closed',()=>app.quit());
