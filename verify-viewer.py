#!/usr/bin/env python3
"""Exercise resize, close and explicit reopen only against the local fixture."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time
import uuid
import dbus

ROOT=Path(__file__).resolve().parent
bus=dbus.SessionBus()
api=dbus.Interface(bus.get_object('org.interviewhelper.ClaudeInterview','/org/interviewhelper/ClaudeInterview'),
                   'org.interviewhelper.ClaudeInterview')
def info():return json.loads(api.WorkspaceInfo())
assert info()['fixture'], 'Only test resize/close with workspace.py --fixture'
env=os.environ.copy();env['DISPLAY']=info()['display'];env['XAUTHORITY']=str(ROOT/'runtime/workspace/Xauthority')

def wait(test,timeout=15):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        result=test()
        if result:return result
        time.sleep(.15)
    raise RuntimeError('Viewer test timeout; '+str(info()))

def dimensions(window):
    data=subprocess.check_output(['xwininfo',*window],env=env,text=True)
    return tuple(int(re.search(label+r':\s*(\d+)',data)[1]) for label in ['Width','Height'])

def client_dimensions(pid):
    tree=subprocess.check_output(['xwininfo','-root','-tree'],env=env,text=True)
    candidates=[]
    for window in re.findall(r'^\s*(0x[0-9a-f]+)\s',tree,re.M):
        data=subprocess.run(['xprop','-id',window,'_NET_WM_PID'],env=env,text=True,capture_output=True).stdout
        if re.search(r'=\s*'+str(pid)+r'\s*$',data):
            width,height=dimensions(['-id',window]);candidates.append((width*height,width,height))
    return max(candidates)[1:] if candidates else (0,0)

wait(lambda:info()['fittedSize'])
initial=dimensions(['-root'])
original=info();pid=original['viewerPid']
scripting=dbus.Interface(bus.get_object('org.kde.KWin','/Scripting'),'org.kde.kwin.Scripting')
tag='claude-viewer-resize-'+uuid.uuid4().hex
script=ROOT/'runtime/workspace/viewer-resize-check.js'
script.write_text('workspace.windowList().forEach(function(w){if(w.pid==='+str(pid)+'){'
                  'const g=w.frameGeometry;g.width=Math.max(640,Math.round(g.width*.75));'
                  'g.height=Math.max(480,Math.round(g.height*.75));w.frameGeometry=g;}});')
scripting.loadScript(str(script),tag,signature='ss');scripting.start()
try:
    wait(lambda:dimensions(['-root'])[0]<initial[0] and dimensions(['-root'])[1]<initial[1])
    resized=dimensions(['-root'])
    wait(lambda:tuple(info()['fittedSize'])==resized)
    wait(lambda:all(abs(a-(b-4))<=8 for a,b in zip(client_dimensions(original['claudePid']),resized)))
    os.kill(pid,signal.SIGTERM)
    wait(lambda:info()['viewerPid']==0)
    time.sleep(2.2)
    assert info()['viewerPid']==0, 'Viewer reappeared after close'
    assert info()['claudePid']==original['claudePid'], 'Closing viewer restarted Claude'
    component=dbus.Interface(bus.get_object('org.kde.kglobalaccel','/component/kwin'),
                             'org.kde.kglobalaccel.Component')
    component.invokeShortcut('Claude Interview Toggle')
    wait(lambda:info()['viewerPid'] not in [0,pid])
    assert info()['claudePid']==original['claudePid']
    report={'remoteDesktopResized':True,'claudeWindowFitted':True,'closeDoesNotRespawn':True,
            'hotkeyReopensViewer':True,'claudeProcessPreserved':True,'ok':True}
    (ROOT/'viewer-verification.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
finally:scripting.unloadScript(tag)
