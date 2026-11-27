#!/usr/bin/env python3
"""Integration checks use the local fixture, never the real Claude account."""
import json
from pathlib import Path
import subprocess
import time
import dbus

from interview_helper.paths import ROOT
R=ROOT/'runtime'
bus=dbus.SessionBus()
api=dbus.Interface(bus.get_object('org.interviewhelper.ClaudeInterview','/org/interviewhelper/ClaudeInterview'),'org.interviewhelper.ClaudeInterview')
component=dbus.Interface(bus.get_object('org.kde.kglobalaccel','/component/kwin'),'org.kde.kglobalaccel.Component')
checks={}


def read(name):return json.loads((R/name).read_text())
def wait(test,timeout=15):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        try:
            value=test()
            if value:return value
        except (FileNotFoundError,json.JSONDecodeError):pass
        time.sleep(.1)
    raise RuntimeError('Timeout; status='+str(read('status.json')))
def key(name):component.invokeShortcut('Claude Interview '+name)
def record(name,value=True):checks[name]=value;print(name,':',value,flush=True)


try:
    wait(lambda:len(read('window-state.json')['windows'])==2)
    initial=read('window-state.json')
    assert all(w['keepAbove'] and w['excluded'] and w['allDesktops'] and w['skipTaskbar'] for w in initial['windows'])
    record('both_windows_protected')
    key('Toggle');wait(lambda:read('window-state.json')['hidden'])
    assert all(w['minimized'] for w in read('window-state.json')['windows'])
    key('Toggle');wait(lambda:not read('window-state.json')['hidden'])
    assert all(not w['minimized'] for w in read('window-state.json')['windows'])
    record('toggle_shortcut')
    before=read('fixture-state.json');base=len(before['messages'])
    marker='Проверка вставки '+str(time.monotonic_ns())
    expected=before['text']+('\n' if before['text'].strip() else '')+marker
    api.TestText(marker)
    wait(lambda:read('fixture-state.json')['text']==expected)
    record('text_inserted')
    key('Send');wait(lambda:len(read('fixture-state.json')['messages'])==base+1)
    record('send_shortcut')
    key('Screenshot');wait(lambda:read('fixture-state.json')['attachments']==1,25)
    assert all(abs(w['opacity']-(1 if w['aux'] else .90))<.001 for w in read('window-state.json')['windows'])
    record('screenshot_attached_without_minimizing')
    key('Send');wait(lambda:len(read('fixture-state.json')['messages'])==base+2)
    record('image_message_sent')
    key('Voice');wait(lambda:read('status.json').get('recording'))
    record('both_audio_recorders_started')
    sample=ROOT.parent/'meetingly-local/fleurs-russian.wav'
    subprocess.run(['pw-play',str(sample)],check=True,timeout=25)
    key('Voice')
    wait(lambda:bool(read('fixture-state.json')['text']),60)
    wait(lambda:read('status.json').get('recognitionMs') is not None and not list(R.glob('record-*')),10)
    status=read('status.json')
    record('voice_inserted',{'characters':len(read('fixture-state.json')['text']),'recognitionMs':status.get('recognitionMs')})
    assert not list(R.glob('record-*'))
    record('temporary_recordings_deleted')
    key('Send');wait(lambda:len(read('fixture-state.json')['messages'])==base+3)
    record('voice_message_sent')
    pids=json.loads(api.Pids())
    probe=Path(__file__).with_name('verify-window.js')
    probe.write_text('workspace.windowList().forEach(function(w){if(w.pid==='+str(pids['claude'])+'){w.keepAbove=false;w.minimized=true;}});')
    scripting=dbus.Interface(bus.get_object('org.kde.KWin','/Scripting'),'org.kde.kwin.Scripting')
    scripting.loadScript(str(probe),'claude-interview-check',signature='ss');scripting.start()
    time.sleep(.3)
    assert all(w['keepAbove'] and not w['minimized'] for w in read('window-state.json')['windows'])
    scripting.unloadScript('claude-interview-check')
    record('unrequested_minimize_and_lower_reverted')
    kwin=dbus.Interface(bus.get_object('org.kde.KWin','/KWin'),'org.kde.KWin')
    kwin.showDesktop(True);time.sleep(.3)
    prop=dbus.Interface(bus.get_object('org.kde.KWin','/KWin'),'org.freedesktop.DBus.Properties')
    assert not bool(prop.Get('org.kde.KWin','showingDesktop'))
    record('show_desktop_does_not_hide_claude')
    record('ok')
finally:
    (ROOT/'verification.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2))
