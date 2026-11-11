const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('fixture',{state:data=>ipcRenderer.send('fixture:state',data)});
