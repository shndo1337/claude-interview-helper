#!/usr/bin/env python3
"""Verify local fixture actions while an independent host test window is focused.

Refuses to run against a real Claude session. No messages leave the local fixture.
"""
import json
import os
from pathlib import Path
import subprocess
import time
import dbus

from interview_helper.paths import ROOT
STATE=ROOT/'runtime/workspace'
bus=dbus.SessionBus()
api=dbus.Interface(bus.get_object('org.interviewhelper.ClaudeInterview','/org/interviewhelper/ClaudeInterview'),
                   'org.interviewhelper.ClaudeInterview')
info=json.loads(api.WorkspaceInfo())
assert info['fixture'], 'Only run this check with workspace.py --fixture'


def wait(test, timeout=20):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        try:
            value=test()
            if value:return value
        except (FileNotFoundError,json.JSONDecodeError):pass
        time.sleep(.1)
    raise RuntimeError('Timeout: '+api.Status())


def state():return json.loads((STATE/'fixture-state.json').read_text())
def host():return json.loads((STATE/'host-focus.json').read_text())


probe=STATE/'host-focus-probe.cjs'
probe.write_text('''const {app,BrowserWindow}=require('electron');
const fs=require('node:fs'),path=require('node:path');
app.setPath('userData',path.join(__dirname,'host-probe-profile'));
app.whenReady().then(async()=>{
 const w=new BrowserWindow({width:520,height:200,title:'Проверка фокуса — локальный тест',
   webPreferences:{sandbox:true,contextIsolation:true}});
 await w.loadURL('data:text/html;charset=utf-8,'+encodeURIComponent('<body style="font:20px system-ui;padding:20px">Локальная проверка отдельной сессии Claude. Это окно должно оставаться активным во время действий помощника.</body>'));
 await w.webContents.executeJavaScript(`window.events=[];for(const name of ['focus','blur','visibilitychange'])addEventListener(name,()=>events.push({name,at:Date.now()}));`);
 let busy=false;
 setInterval(async()=>{if(busy)return;busy=true;try{
  const result=await w.webContents.executeJavaScript('({focused:document.hasFocus(),events:window.events||[]})');
  fs.writeFileSync(path.join(__dirname,'host-focus.json'),JSON.stringify(result));
 }finally{busy=false;}},100);
 w.show();w.focus();
});
app.on('window-all-closed',()=>app.quit());
''')
electron=ROOT.parent/'meetingly-review/app/node_modules/electron/dist/electron'
env=os.environ.copy();env.pop('ELECTRON_RUN_AS_NODE',None)
report={}
(STATE/'host-focus.json').unlink(missing_ok=True)
with (STATE/'host-probe.log').open('w') as log:
    process=subprocess.Popen([str(electron),'--ozone-platform=x11',str(probe)],env=env,stdout=log,stderr=log)
    try:
        wait(lambda:bool(api.EditorReady()),30)
        wait(lambda:host()['focused'])
        initial=len(host()['events'])
        marker='Текст внутри отдельного экрана '+str(time.monotonic_ns())
        previous=state()['text']
        api.TestText(marker)
        wait(lambda:marker in state()['text'])
        wait(lambda:json.loads(api.Status())['state']=='Test text inserted')
        report['textInserted']=True
        component=dbus.Interface(bus.get_object('org.kde.kglobalaccel','/component/kwin'),
                                 'org.kde.kglobalaccel.Component')
        component.invokeShortcut('Claude Interview Scroll Down')
        wait(lambda:state().get('historyScrollTop',0)>0)
        down=state()['historyScrollTop']
        component.invokeShortcut('Claude Interview Scroll Up')
        wait(lambda:state().get('historyScrollTop',0)<down)
        report['scrollDownAndUp']=True
        api.Action('screenshot')
        wait(lambda:state()['attachments']>=1,25)
        wait(lambda:json.loads(api.Status())['state']=='✓ Скрин в сообщении')
        report['hostScreenshotAttached']=True
        before=len(state()['messages'])
        api.Action('send')
        wait(lambda:len(state()['messages'])==before+1)
        report['localFixtureSend']=True
        time.sleep(.4)
        focus=host()
        report['hostWindowStayedFocused']=focus['focused'] and not any(e['name']=='blur' for e in focus['events'][initial:])
        assert report['hostWindowStayedFocused'], focus
        windows=json.loads((STATE/'window-state.json').read_text())['windows']
        assert windows and all(w['excluded'] and w['skipTaskbar'] for w in windows)
        report['viewerWindowRulesApplied']=True
        import stat
        socket=Path(os.environ['XDG_RUNTIME_DIR'])/'claude-workspace/display.sock'
        assert stat.S_IMODE(socket.stat().st_mode)==0o600
        assert stat.S_IMODE(socket.parent.stat().st_mode)==0o700
        listeners=subprocess.check_output(['ss','-ltnp'],text=True)
        assert 'Xtigervnc' not in listeners
        report['unixSocketOnly']=True
        report['ok']=True
        print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
        (ROOT/'workspace-verification.json').write_text(json.dumps(report,indent=2))
    finally:
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait()
