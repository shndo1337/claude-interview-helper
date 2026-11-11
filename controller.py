#!/usr/bin/env python3
"""A separate Plasma launch mode for the installed Claude Desktop client."""
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import uuid

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
import gi
gi.require_version('Atspi','2.0')
from gi.repository import Atspi, GLib

from xkeys import XKeys
from portal_keys import PortalKeys
from live_audio import LiveAudio

ROOT=Path(__file__).resolve().parent
RUNTIME=ROOT/'runtime'
ELECTRON=ROOT.parent/'meetingly-review/app/node_modules/electron/dist/electron'
BUS='org.interviewhelper.ClaudeInterview'
OBJECT='/org/interviewhelper/ClaudeInterview'
IFACE=BUS
SCRIPT='claude-interview-mode'
TEST_MARKER='Проверка режима (не отправлять).'
SHORTCUTS={
    'focus':'Claude Interview Focus','capture-begin':'Claude Interview Capture Begin',
    'capture-end':'Claude Interview Capture End','restore':'Claude Interview Restore',
    'toggle':'Claude Interview Toggle',
}


class ExistingClaude:
    def __init__(self,pid):self.pid=pid
    def poll(self):
        try:return None if str(Path(f'/proc/{self.pid}/exe').resolve())=='/usr/lib/claude-desktop/claude-desktop' else 0
        except (OSError,ProcessLookupError):return 0


class SpeechBridge:
    def __init__(self,log,platform='x11'):
        env=os.environ.copy();env.pop('ELECTRON_RUN_AS_NODE',None)
        self.process=subprocess.Popen([str(ELECTRON),f'--ozone-platform={platform}','--class=claude-interview-status',str(ROOT/'bridge.cjs')],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True,bufsize=1,env=env)
        self.lock=threading.Lock();self.pending={};self.ready=threading.Event();self.failure=None
        threading.Thread(target=self.reader,daemon=True).start()

    def reader(self):
        for line in self.process.stdout:
            try:msg=json.loads(line)
            except ValueError:continue
            if msg.get('event')=='ready':self.ready.set()
            elif msg.get('event')=='error':self.failure=msg.get('error');self.ready.set()
            elif msg.get('id') in self.pending:self.pending[msg['id']].put(msg)
        self.failure='Речевой помощник остановился';self.ready.set()

    def request(self,action,timeout=30,**args):
        token=uuid.uuid4().hex;q=queue.Queue()
        with self.lock:
            if self.process.poll() is not None:raise RuntimeError(self.failure or 'Помощник не запущен')
            self.pending[token]=q
            self.process.stdin.write(json.dumps({'id':token,'action':action,**args},ensure_ascii=False)+'\n')
            self.process.stdin.flush()
        try:
            msg=q.get(timeout=timeout)
            if msg.get('error'):raise RuntimeError(msg['error'])
            return msg.get('result')
        finally:self.pending.pop(token,None)

    def status(self,text):
        try:self.request('status',text=text,timeout=3)
        except Exception:pass

    def close(self):
        try:self.process.stdin.write('{"action":"exit"}\n');self.process.stdin.flush();self.process.wait(timeout=4)
        except Exception:self.process.terminate()


class Controller(dbus.service.Object):
    def __init__(self,bus,fixture=False):
        self.bus=bus;self.name=dbus.service.BusName(BUS,bus=bus)
        super().__init__(self.name,OBJECT)
        self.log=(RUNTIME/'controller.log').open('a',buffering=1)
        self.fixture=fixture;self.claude=None;self.bridge=None;self.script=None
        self.capture=None;self.capture_dir=None;self.capture_timer=None
        self.live_audio=None;self.recording_id=None
        self.pool=ThreadPoolExecutor(max_workers=1);self.stopping=False
        self.xkeys=XKeys();self.editor_cache=None
        self.keyboard=PortalKeys(bus,RUNTIME)
        self.latest={'state':'starting'}
        self.permission_pending=False
        self.window_seen=False;self.window_state={};self.last_wake=0;self.activation=None
        self.bus.add_signal_receiver(self.show_desktop_changed,signal_name='showingDesktopChanged',
            dbus_interface='org.kde.KWin',path='/KWin')

    def show_desktop_changed(self,showing):
        if not showing or self.stopping:return
        try:
            state=json.loads((RUNTIME/'window-state.json').read_text())
            if state.get('windows') and not state.get('hidden'):
                dbus.Interface(self.bus.get_object('org.kde.KWin','/KWin'),'org.kde.KWin').showDesktop(False)
        except Exception:pass

    @dbus.service.method(IFACE,in_signature='',out_signature='s')
    def Pids(self):
        return json.dumps({'claude':self.claude.pid if self.claude else 0,'bridge':self.bridge.process.pid if self.bridge else 0,'permission':self.permission_pending})

    @dbus.service.method(IFACE,in_signature='s',out_signature='')
    def WindowState(self,value):
        (RUNTIME/'window-state.json').write_text(str(value))
        try:
            self.window_state=json.loads(str(value))
            if any(not w['aux'] for w in self.window_state.get('windows',[])):self.window_seen=True
        except (ValueError,KeyError):pass

    @dbus.service.method(IFACE,in_signature='',out_signature='s')
    def Status(self):return json.dumps(self.latest,ensure_ascii=False)

    @dbus.service.method(IFACE,in_signature='',out_signature='b')
    def CanSend(self):
        try:return self.send_button() is not None
        except Exception:return False

    @dbus.service.method(IFACE,in_signature='s',out_signature='s')
    def TestText(self,value):
        if not self.fixture and str(value)!=TEST_MARKER:return 'disabled'
        def run():
            try:
                if not self.fixture and self.editor_text(self.editor()).strip():raise RuntimeError('В сообщении есть текст; тестовая вставка отменена')
                self.insert(text=str(value));self.state('Test text inserted')
            except Exception as error:self.state('Test failed',error=str(error))
        self.pool.submit(run)
        return 'queued'

    @dbus.service.method(IFACE,in_signature='',out_signature='s')
    def TestClear(self):
        def run():
            try:
                editor=self.focus_editor()
                if self.editor_text(editor).strip()!=TEST_MARKER:raise RuntimeError('Текст изменился; очистка отменена')
                self.keyboard.clear_test();self.state('● Готово · Ctrl+Alt+V')
            except Exception as error:self.state('Test failed',error=str(error))
        self.pool.submit(run);return 'queued'

    @dbus.service.method(IFACE,in_signature='s',out_signature='s')
    def Action(self,action):
        action=str(action)
        if action not in ['voice','screenshot','send','show','stop','keyboard','validate']:return 'unknown'
        self.pool.submit(self.dispatch,action)
        return 'queued'

    def state(self,state,**extra):
        self.latest={'state':state,'recording':bool(self.capture),**extra}
        (RUNTIME/'status.json').write_text(json.dumps(self.latest,ensure_ascii=False,indent=2))
        if self.bridge:self.bridge.status(state)

    def invoke_kwin(self,action):
        component=self.bus.get_object('org.kde.kglobalaccel','/component/kwin')
        dbus.Interface(component,'org.kde.kglobalaccel.Component').invokeShortcut(SHORTCUTS[action])

    def start(self):
        if not self.fixture:
            for process in Path('/proc').glob('[0-9]*'):
                try:
                    if str((process/'exe').resolve())!='/usr/lib/claude-desktop/claude-desktop':continue
                    args=(process/'cmdline').read_bytes().replace(b'\0',b' ')
                    if b'--type=' not in args:
                        if b'--force-renderer-accessibility' not in args:
                            raise RuntimeError('Полностью выйди из обычного Claude через «Выход» и открой «Claude — собеседование»')
                        self.claude=ExistingClaude(int(process.name));break
                except (FileNotFoundError,PermissionError,ProcessLookupError):pass
        scripting=dbus.Interface(self.bus.get_object('org.kde.KWin','/Scripting'),'org.kde.kwin.Scripting')
        if scripting.isScriptLoaded(SCRIPT):scripting.unloadScript(SCRIPT)
        self.script=int(scripting.loadScript(str(ROOT/'kwin.js'),SCRIPT,signature='ss'))
        scripting.start()
        self.bridge=SpeechBridge(self.log)
        command=[str(ELECTRON),'--ozone-platform=x11','--class=claude-interview','--force-renderer-accessibility',str(ROOT/'fixture.cjs')] if self.fixture else [
            '/usr/bin/claude-desktop','--ozone-platform=x11','--class=claude-interview','--force-renderer-accessibility',
            'claude://claude.ai/new?surface=chat&source=desktop_action']
        env=os.environ.copy();env.pop('ELECTRON_RUN_AS_NODE',None)
        if self.claude is None:
            if self.fixture:self.claude=subprocess.Popen(command,stdout=self.log,stderr=self.log,env=env)
            else:
                # Keep Claude in its own unit: stopping/reloading the helper must preserve the app.
                subprocess.run(['systemd-run','--user','--unit=claude-interview-client','--collect','--service-type=exec',*command],
                    stdout=self.log,stderr=self.log,check=True,env=env)
                pid=int(subprocess.check_output(['systemctl','--user','show','--property=MainPID','--value','claude-interview-client'],text=True))
                if not pid:raise RuntimeError('Claude не запустился')
                self.claude=ExistingClaude(pid)
        else:self.wake()
        GLib.timeout_add_seconds(1,self.watch)
        threading.Thread(target=self.wait_ready,daemon=True).start()

    def wait_ready(self):
        if not self.bridge.ready.wait(45):
            self.state('Ошибка распознавания',error='Истекло время загрузки модели');return
        if self.bridge.failure:self.state('Ошибка распознавания',error=self.bridge.failure)
        else:self.state('● Готово · Ctrl+Alt+V')

    def watch(self):
        if self.claude and self.claude.poll() is not None and not self.stopping:
            self.state('Claude закрыт');self.shutdown();return False
        if self.window_seen and not self.stopping and not self.window_state.get('hidden') and not any(not w['aux'] for w in self.window_state.get('windows',[])):
            self.wake()
        return not self.stopping

    def wake(self):
        if self.fixture or self.stopping or time.monotonic()-self.last_wake<3:return
        if self.activation and self.activation.poll() is None:return
        self.last_wake=time.monotonic()
        self.activation=subprocess.Popen(['/usr/bin/claude-desktop'],stdout=self.log,stderr=self.log)

    def dispatch(self,action):
        try:
            if action=='voice':self.voice()
            elif action=='screenshot':self.screenshot()
            elif action=='send':self.send()
            elif action=='show':self.invoke_kwin('focus')
            elif action=='keyboard':
                self.permission_pending=True
                try:self.keyboard.ensure();self.state('● Готово · Ctrl+Alt+V')
                finally:self.permission_pending=False
            elif action=='validate':self.editor();self.state('● Готово · Ctrl+Alt+V')
            elif action=='stop':GLib.idle_add(self.shutdown)
        except Exception as error:
            self.log.write(f'{action}: {type(error).__name__}: {error}\n')
            self.state('⚠ '+str(error)[:90],error=str(error))

    def audio_node(self,kind):
        info=subprocess.check_output(['wpctl','inspect',f'@DEFAULT_AUDIO_{kind}@'],text=True)
        match=re.search(r'node\.name\s*=\s*"([^"]+)"',info)
        if not match:raise RuntimeError('Нет устройства звука '+kind)
        return match.group(1)

    def start_capture(self):
        self.capture_dir=RUNTIME/('record-'+uuid.uuid4().hex)
        self.capture_dir.mkdir(mode=0o700)
        streams=[]
        try:
            self.recording_id=uuid.uuid4().hex
            self.bridge.request('stream-begin',recording=self.recording_id)
            for name,kind in [('microphone','SOURCE'),('system','SINK')]:
                output=(self.capture_dir/(name+'.raw')).open('wb')
                command=['pw-record','--target',self.audio_node(kind),'--rate=16000','--channels=1','--format=s16','--raw','--latency=50ms']
                if kind=='SINK':command+=['--properties={"stream.capture.sink":true}']
                command+=['-']
                process=subprocess.Popen(command,stdout=output,stderr=self.log)
                streams.append((process,output))
            self.capture=streams
            self.live_audio=LiveAudio(self.bridge,self.capture_dir,self.recording_id,[p for p,_ in streams])
            self.live_audio.start()
            token=self.capture_dir
            def auto_stop():
                if self.capture and self.capture_dir==token:self.dispatch('voice')
            self.capture_timer=threading.Timer(90,lambda:self.pool.submit(auto_stop))
            self.capture_timer.daemon=True;self.capture_timer.start()
            self.state('● Запись и распознавание',recognizer='Nemotron 3.5 Streaming')
        except Exception:
            self.capture=streams
            self.stop_capture()
            if self.live_audio:self.live_audio.cancel();self.live_audio=None
            try:self.bridge.request('stream-abort',recording=self.recording_id,timeout=3)
            except Exception:pass
            self.erase_capture();raise

    def stop_capture(self):
        if self.capture_timer:self.capture_timer.cancel();self.capture_timer=None
        if self.live_audio:self.live_audio.stopping.set()
        streams=self.capture or [];self.capture=None
        for process,_output in streams:
            if process.poll() is None:process.send_signal(signal.SIGINT)
        for process,output in streams:
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:process.kill();process.wait()
            output.close()
        if self.live_audio:self.live_audio.finished.set()

    def erase_capture(self):
        if self.capture_dir:
            for p in self.capture_dir.glob('*'):p.unlink(missing_ok=True)
            self.capture_dir.rmdir();self.capture_dir=None

    def voice(self):
        if not self.capture:
            if not self.bridge.ready.is_set():raise RuntimeError('Подожди загрузки распознавания')
            if self.bridge.failure:raise RuntimeError(self.bridge.failure)
            self.start_capture();return
        started=time.monotonic()
        self.stop_capture();self.state('◌ Завершаю фразу…')
        try:
            files=[self.capture_dir/n for n in ['microphone.raw','system.raw']]
            if min(p.stat().st_size for p in files)<3200:raise RuntimeError('Слишком короткая запись')
            self.live_audio.finish()
            result=self.bridge.request('stream-end',recording=self.recording_id,timeout=60)
            recognition_ms=round((time.monotonic()-started)*1000)
            text=result.get('text','').strip()
            if not text:raise RuntimeError('Речь не распознана — проверь звук и микрофон')
            self.insert(text=text)
            self.state('✓ Текст в сообщении',recognitionMs=recognition_ms,characters=len(text),
                       recognizer='Nemotron 3.5 Streaming',finalizeMs=result.get('ms'),
                       completedWhileRecording=result.get('completedBeforeStop'),audioMs=result.get('audioMs'))
        finally:
            if self.live_audio:self.live_audio.cancel();self.live_audio=None
            try:self.bridge.request('stream-abort',recording=self.recording_id,timeout=3)
            except Exception:pass
            self.recording_id=None;self.erase_capture()

    def application(self):
        desktop=Atspi.get_desktop(0)
        for i in range(desktop.get_child_count()):
            app=desktop.get_child_at_index(i)
            try:
                if app.get_process_id()==self.claude.pid:return app
            except Exception:continue
        raise RuntimeError('Claude ещё загружается или недоступен для ввода')

    def nodes(self):
        application=self.application();application.clear_cache()
        todo=[application];seen=0
        while todo and seen<3000:
            node=todo.pop(0);seen+=1
            try:
                role=node.get_role();yield node,role
                if role in [Atspi.Role.STATIC,Atspi.Role.TEXT,Atspi.Role.PARAGRAPH]:continue
                todo.extend(node.get_child_at_index(i) for i in range(node.get_child_count()))
            except Exception:continue

    def editor(self):
        if self.editor_cache:
            try:
                state=self.editor_cache.get_state_set()
                if state.contains(Atspi.StateType.SHOWING) and not state.contains(Atspi.StateType.DEFUNCT):return self.editor_cache
            except Exception:pass
        candidates=[]
        for node,role in self.nodes():
            if role not in [Atspi.Role.ENTRY,Atspi.Role.TEXT,Atspi.Role.PARAGRAPH]:continue
            state=node.get_state_set()
            if not all(state.contains(x) for x in [Atspi.StateType.EDITABLE,Atspi.StateType.SHOWING,Atspi.StateType.ENABLED]):continue
            # Never paste a recording into login, password, search or other one-line fields.
            if role==Atspi.Role.PASSWORD_TEXT:continue
            if not state.contains(Atspi.StateType.MULTI_LINE):
                attributes=' '.join(node.get_attributes()).lower()
                if not any(hint in attributes for hint in ['prosemirror','chat-input','prompt-textarea']):continue
            rect=node.get_component_iface().get_extents(Atspi.CoordType.SCREEN)
            score=rect.width*min(rect.height,400)
            if state.contains(Atspi.StateType.MULTI_LINE):score+=100000
            candidates.append((score,node))
        if not candidates:raise RuntimeError('Открой чат Claude и войди в аккаунт')
        self.editor_cache=max(candidates,key=lambda x:x[0])[1]
        return self.editor_cache

    def focus_editor(self):
        self.invoke_kwin('focus');time.sleep(.15)
        editor=self.editor()
        # Claude hosts chat in a separate WebContentsView. Its own activation action
        # moves keyboard focus into that view; grab_focus alone only moves the DOM caret.
        action=editor.get_action_iface()
        if action:
            for i in range(Atspi.Action.get_n_actions(action)):
                if Atspi.Action.get_action_name(action,i) in ['activate','click']:
                    Atspi.Action.do_action(action,i);break
        if not editor.get_component_iface().grab_focus():raise RuntimeError('Не удалось поставить курсор в сообщение Claude')
        try:
            Atspi.Text.set_caret_offset(editor,Atspi.Text.get_character_count(editor))
        except Exception:pass
        time.sleep(.1)
        return editor

    def editor_text(self,node,depth=0):
        if depth>15:return ''
        node.clear_cache()
        try:text=Atspi.Text.get_text(node,0,-1)
        except Exception:text='\ufffc'
        if '\ufffc' not in text:return text
        values=[self.editor_text(node.get_child_at_index(i),depth+1) for i in range(node.get_child_count())]
        return ''.join(values) if values else text.replace('\ufffc','')

    def insert(self,text=None,image=None):
        if not self.keyboard.session:
            self.state('Разреши вставку в системном окне');self.permission_pending=True
            try:self.keyboard.ensure()
            finally:self.permission_pending=False
        self.focus_editor()
        if image:self.bridge.request('clipboard-image',file=str(image))
        else:
            editor=self.editor();current=self.editor_text(editor)
            self.bridge.request('clipboard-text',text=('\n' if current.strip() else '')+text)
        try:self.xkeys.released();self.keyboard.paste();time.sleep(1.0 if image else .4)
        finally:self.bridge.request('clipboard-restore')

    def screenshot(self):
        image=RUNTIME/('screen-'+uuid.uuid4().hex+'.png')
        self.state('◌ Скриншот…')
        try:
            self.invoke_kwin('capture-begin');time.sleep(.22)
            try:subprocess.run(['spectacle','--new-instance','--background','--nonotify','--fullscreen','--output',str(image)],check=True,stdout=self.log,stderr=self.log,timeout=20)
            finally:self.invoke_kwin('capture-end')
            if not image.exists() or image.stat().st_size<100:raise RuntimeError('Не получен скриншот')
            self.insert(image=image);self.state('✓ Скрин в сообщении')
        finally:image.unlink(missing_ok=True)

    def send(self):
        self.focus_editor();self.xkeys.released()
        button=self.send_button()
        if not button:raise RuntimeError('Сообщение пустое, вложение загружается или кнопка отправки недоступна')
        action=button.get_action_iface()
        if action and Atspi.Action.do_action(action,0):self.state('✓ Отправлено');return
        raise RuntimeError('Не удалось нажать кнопку отправки')

    def send_button(self):
        names={'send','send message','send prompt','отправить','отправить сообщение'}
        for node,role in self.nodes():
            if role!=Atspi.Role.PUSH_BUTTON:continue
            if node.get_name().strip().lower() not in names:continue
            state=node.get_state_set()
            if not state.contains(Atspi.StateType.SHOWING):continue
            if not state.contains(Atspi.StateType.ENABLED):continue
            return node
        return None

    def shutdown(self):
        if self.stopping:return False
        self.stopping=True
        if self.capture:self.stop_capture()
        if self.live_audio:self.live_audio.cancel();self.live_audio=None
        self.erase_capture()
        try:self.invoke_kwin('restore')
        except Exception:pass
        try:
            scripting=dbus.Interface(self.bus.get_object('org.kde.KWin','/Scripting'),'org.kde.kwin.Scripting')
            scripting.unloadScript(SCRIPT)
        except Exception:pass
        if self.bridge:self.bridge.close()
        self.keyboard.close()
        if self.fixture and self.claude and self.claude.poll() is None:self.claude.terminate()
        GLib.idle_add(lambda:loop.quit() or False)
        return False


def notify(message):
    subprocess.run(['notify-send','Claude — собеседование',message],check=False)


def main():
    global loop
    RUNTIME.mkdir(parents=True,exist_ok=True,mode=0o700);RUNTIME.chmod(0o700)
    dbus.mainloop.glib.threads_init();DBusGMainLoop(set_as_default=True);bus=dbus.SessionBus()
    command=sys.argv[1] if len(sys.argv)>1 else 'launch'
    if command in ['launch','stop','voice','screenshot','send','status']:
        if bus.name_has_owner(BUS):
            obj=bus.get_object(BUS,OBJECT);iface=dbus.Interface(obj,IFACE)
            print(iface.Status() if command=='status' else iface.Action('show' if command=='launch' else command));return
        if command!='launch':raise RuntimeError('Режим собеседования не запущен')
        if 'KDE' not in os.environ.get('XDG_CURRENT_DESKTOP',''):raise RuntimeError('Этот режим нужно запускать в Plasma')
        subprocess.run(['systemd-run','--user','--unit=claude-interview','--collect','--service-type=exec',
            '/usr/bin/python3',str(ROOT/'controller.py'),'run'],check=True)
        return
    fixture=command=='fixture'
    lock=(RUNTIME/'controller.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    loop=GLib.MainLoop();Atspi.init();Atspi.set_timeout(1500,4000)
    controller=Controller(bus,fixture=fixture)
    signal.signal(signal.SIGTERM,lambda *_:GLib.idle_add(controller.shutdown))
    signal.signal(signal.SIGINT,lambda *_:GLib.idle_add(controller.shutdown))
    try:
        controller.start();loop.run()
    finally:controller.shutdown()


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(str(error),file=sys.stderr);notify(str(error));sys.exit(1)
