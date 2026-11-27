#!/usr/bin/env python3
"""A private local X display, viewed in a window on the existing Plasma session.

The VNC transport is a mode-0600 Unix socket in a mode-0700 directory. Neither
the X server nor VNC listens on TCP. Clipboard sharing with the viewer is off.
"""
import ctypes as C
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import time

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
import gi
gi.require_version('Atspi','2.0')
from gi.repository import Atspi, GLib

from . import controller as core
from .clients import codex_environment, codex_window_command
from .desktop.xkeys import XKeys
from .launch_modes import prepare_launch, running_helper
from .paths import ELECTRON, FIXTURE, KWIN_SCRIPT, ROOT
from .speech.bridge import SpeechBridge

STATE = ROOT/'runtime/workspace'
IPC = Path(os.environ.get('XDG_RUNTIME_DIR',f'/run/user/{os.getuid()}'))/'claude-workspace'
HOST = os.environ.copy()
HOST.pop('ELECTRON_RUN_AS_NODE', None)


class LocalKeyboard:
    """XTest affects only the private X server, never the host keyboard focus."""
    def __init__(self, _bus, _runtime):
        self.keys = XKeys()
        self.session = True

    def ensure(self): pass
    def paste(self): self.keys.paste()
    def close(self): pass

    def clear_test(self):
        self.keys.released()
        for sym, pressed in [(0xffe3,1),(ord('a'),1),(ord('a'),0),(0xffe3,0),(0xff08,1),(0xff08,0)]:
            self.keys.xt.XTestFakeKeyEvent(self.keys.display,self.keys.code(sym),pressed,0)
        self.keys.x.XFlush(self.keys.display)


class WorkspaceController(core.Controller):
    runtime = STATE
    script_name = 'claude-workspace-mode'
    keyboard_type = LocalKeyboard

    def __init__(self, bus, loop, processes, fixture=False):
        super().__init__(bus,loop,fixture=fixture)
        self.owned = processes
        self.viewer = None
        self.private = os.environ.copy()
        self.scroll_count = 0
        self.fitted_size = None

    @dbus.service.method(core.IFACE,in_signature='',out_signature='s')
    def Pids(self):
        # Outer KWin manages only the viewer. Claude and its status badge live
        # on the private X server and have no top-level host windows.
        return json.dumps({'claude':self.viewer.pid if self.viewer and self.viewer.poll() is None else 0,
                           'bridge':0,'permission':False,'workspace':True})

    @dbus.service.method(core.IFACE,in_signature='',out_signature='s')
    def WorkspaceInfo(self):
        return json.dumps({'fixture':self.fixture,'backend':self.backend,'claudePid':self.claude.pid if self.claude else 0,
                           'viewerPid':self.viewer.pid if self.viewer and self.viewer.poll() is None else 0,'display':os.environ['DISPLAY'],
                           'fittedSize':self.fitted_size})

    def open_viewer(self):
        self.viewer = subprocess.Popen([
            'vncviewer','-Shared','-AcceptClipboard=0','-SendClipboard=0',
            '-SendPrimary=0','-SetPrimary=0','-FullscreenSystemKeys=0',
            '-RemoteResize=1','-geometry','1080x760',str(IPC/'display.sock'),
        ],env=HOST,stdout=self.log,stderr=self.log)
        self.owned.append(self.viewer)

    def start(self):
        self.bridge = SpeechBridge(self.log)
        if self.backend=='codex':
            profile=STATE/'codex-profile'
            profile.mkdir(mode=0o700,exist_ok=True)
            self.private=codex_environment(profile)
            command=codex_window_command(profile)
        elif self.fixture:
            command=[str(ELECTRON),'--ozone-platform=x11','--class=claude-workspace-test',
                     '--force-renderer-accessibility',str(FIXTURE)]
        else:
            profile=STATE/'claude-profile'
            self.private['CLAUDE_USER_DATA_DIR']=str(profile)
            command=['/usr/bin/claude-desktop','--ozone-platform=x11','--class=claude-workspace',
                     '--force-renderer-accessibility','--user-data-dir='+str(profile),
                     'claude://claude.ai/new?surface=chat&source=desktop_action']
        self.claude=subprocess.Popen(command,env=self.private,stdout=self.log,stderr=self.log)
        self.owned.append(self.claude)
        self.open_viewer()
        scripting=dbus.Interface(self.bus.get_object('org.kde.KWin','/Scripting'),'org.kde.kwin.Scripting')
        if scripting.isScriptLoaded(self.script_name): scripting.unloadScript(self.script_name)
        self.script=int(scripting.loadScript(str(KWIN_SCRIPT),self.script_name,signature='ss'))
        scripting.start()
        GLib.timeout_add_seconds(1,self.watch)
        import threading
        threading.Thread(target=self.wait_ready,daemon=True).start()

    def find_private_window(self):
        # Find the actual Claude toplevel by PID on the private display.
        tree=subprocess.check_output(['xwininfo','-root','-tree'],env=self.private,text=True)
        candidates=[]
        for window in re.findall(r'^\s*(0x[0-9a-f]+)\s',tree,re.M):
            prop=subprocess.run(['xprop','-id',window,'_NET_WM_PID'],env=self.private,
                                text=True,capture_output=True).stdout
            if not re.search(r'=\s*'+str(self.claude.pid)+r'\s*$',prop): continue
            details=subprocess.run(['xwininfo','-id',window],env=self.private,text=True,capture_output=True).stdout
            if not re.search(r'Map State:\s*IsViewable',details):continue
            width=re.search(r'Width:\s*(\d+)',details);height=re.search(r'Height:\s*(\d+)',details)
            if width and height:candidates.append((int(width[1])*int(height[1]),int(window,16)))
        if not candidates:raise RuntimeError('Окно Claude в отдельной сессии ещё загружается')
        return max(candidates)[1]

    def activate_private(self):
        window=self.find_private_window()
        x=self.xkeys.x
        x.XRaiseWindow.argtypes=[C.c_void_p,C.c_ulong]
        x.XSetInputFocus.argtypes=[C.c_void_p,C.c_ulong,C.c_int,C.c_ulong]
        x.XRaiseWindow(self.xkeys.display,window)
        x.XSetInputFocus(self.xkeys.display,window,2,0)
        x.XFlush(self.xkeys.display)
        self.private_window = window

    def fit_private_screen(self):
        x=self.xkeys.x;display=self.xkeys.display
        x.XDefaultRootWindow.argtypes=[C.c_void_p];x.XDefaultRootWindow.restype=C.c_ulong
        x.XGetGeometry.argtypes=[C.c_void_p,C.c_ulong,C.POINTER(C.c_ulong),C.POINTER(C.c_int),
            C.POINTER(C.c_int),C.POINTER(C.c_uint),C.POINTER(C.c_uint),C.POINTER(C.c_uint),C.POINTER(C.c_uint)]
        root=C.c_ulong();px=C.c_int();py=C.c_int();width=C.c_uint();height=C.c_uint();border=C.c_uint();depth=C.c_uint()
        if not x.XGetGeometry(display,x.XDefaultRootWindow(display),C.byref(root),C.byref(px),C.byref(py),
                             C.byref(width),C.byref(height),C.byref(border),C.byref(depth)):return
        size=(width.value,height.value)
        if self.fitted_size==size:return
        window=self.find_private_window()
        x.XMoveResizeWindow.argtypes=[C.c_void_p,C.c_ulong,C.c_int,C.c_int,C.c_uint,C.c_uint]
        x.XMoveResizeWindow(display,window,0,0,max(100,width.value-4),max(100,height.value-4))
        x.XFlush(display)
        self.fitted_size=size

    def watch(self):
        if self.viewer:self.viewer.poll()
        if not self.stopping:
            try:self.fit_private_screen()
            except (RuntimeError,subprocess.SubprocessError):pass
        return super().watch()

    def scroll_chat(self, down):
        self.activate_private()
        self.xkeys.released()
        details=subprocess.check_output(['xwininfo','-id',hex(self.private_window)],env=self.private,text=True)
        def value(label):
            match=re.search(re.escape(label)+r':\s*(-?\d+)',details)
            if not match:raise RuntimeError('Не удалось определить область чата')
            return int(match[1])
        px=value('Absolute upper-left X')+int(value('Width')*.65)
        py=value('Absolute upper-left Y')+int(value('Height')*.45)
        x=self.xkeys.x;xt=self.xkeys.xt;display=self.xkeys.display
        x.XDefaultRootWindow.argtypes=[C.c_void_p];x.XDefaultRootWindow.restype=C.c_ulong
        x.XQueryPointer.argtypes=[C.c_void_p,C.c_ulong,C.POINTER(C.c_ulong),C.POINTER(C.c_ulong),
                                 C.POINTER(C.c_int),C.POINTER(C.c_int),C.POINTER(C.c_int),C.POINTER(C.c_int),C.POINTER(C.c_uint)]
        root=C.c_ulong();child=C.c_ulong();rx=C.c_int();ry=C.c_int();wx=C.c_int();wy=C.c_int();mask=C.c_uint()
        have_pointer=x.XQueryPointer(display,x.XDefaultRootWindow(display),C.byref(root),C.byref(child),
                                    C.byref(rx),C.byref(ry),C.byref(wx),C.byref(wy),C.byref(mask))
        xt.XTestFakeMotionEvent.argtypes=[C.c_void_p,C.c_int,C.c_int,C.c_int,C.c_ulong]
        xt.XTestFakeButtonEvent.argtypes=[C.c_void_p,C.c_uint,C.c_int,C.c_ulong]
        # Both pointer motion and wheel events are confined to the private X server.
        try:
            xt.XTestFakeMotionEvent(display,0,px,py,0)
            for _ in range(1):
                xt.XTestFakeButtonEvent(display,5 if down else 4,1,0)
                xt.XTestFakeButtonEvent(display,5 if down else 4,0,0)
        finally:
            if have_pointer:xt.XTestFakeMotionEvent(display,0,rx.value,ry.value,0)
            x.XFlush(display)
        self.scroll_count+=1
        self.state('↓ Чат' if down else '↑ Чат',scrollCount=self.scroll_count,
                   scrollDirection='down' if down else 'up')

    def dispatch(self, action):
        if action=='show':
            # Explicitly opening the launcher may focus the viewer; background
            # insertion calls activate_private instead.
            if not self.viewer or self.viewer.poll() is not None:
                self.open_viewer()
                GLib.timeout_add(500,self.focus_viewer)
            else:core.Controller.invoke_kwin(self,'focus')
        else:super().dispatch(action)

    def invoke_kwin(self, action):
        if action=='focus': self.activate_private()
        else: super().invoke_kwin(action)

    def wake(self):
        # Closing the viewer is intentional; background actions keep running.
        # Only the launcher or explicit show hotkey opens it again.
        return

    def focus_viewer(self):
        if not self.stopping:core.Controller.invoke_kwin(self,'focus')
        return False

    def screenshot(self):
        image=STATE/('screen-'+secrets.token_hex(8)+'.png')
        self.state('◌ Скриншот…')
        try:
            self.invoke_kwin('capture-begin');time.sleep(.22)
            try:
                subprocess.run(['spectacle','--new-instance','--background','--nonotify','--fullscreen',
                                '--output',str(image)],env=HOST,check=True,
                               stdout=self.log,stderr=self.log,timeout=20)
            finally:self.invoke_kwin('capture-end')
            if not image.exists() or image.stat().st_size<100:raise RuntimeError('Не получен скриншот')
            self.insert(image=image);self.state('✓ Скрин в сообщении')
        finally:image.unlink(missing_ok=True)

    def shutdown(self):
        if self.stopping:return False
        # The base shutdown restores the viewer's outer KWin properties and
        # frees the shortcuts. The ordinary Claude process is never terminated.
        super().shutdown()
        for process in reversed(self.owned):
            if process.poll() is None:process.terminate()
        return False


def main():
    command=sys.argv[1] if len(sys.argv)>1 else 'launch'
    codex_backend='--codex' in sys.argv
    mode_name='codex-workspace' if codex_backend else 'workspace'
    backend_args=['--codex'] if codex_backend else []
    if command in ['launch','stop','status']:
        bus=dbus.SessionBus()
        if command=='launch':
            if not bus.name_has_owner('org.kde.KWin'):raise RuntimeError('Этот режим нужно запускать в Plasma')
            api=prepare_launch(bus,mode_name)
            if api:print(api.Action('show'));return
        else:
            api,mode=running_helper(bus)
            if not api or mode!=mode_name:print('Этот режим TigerVNC не запущен');return
            print(api.Status() if command=='status' else api.Action('stop'));return
        subprocess.run(['systemd-run','--user','--unit=claude-workspace','--collect','--service-type=exec',
                        '/usr/bin/python3',str(ROOT/'workspace.py'),'run',*backend_args],check=True)
        return
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700);STATE.chmod(0o700)
    lock=(STATE/'session.lock').open('w')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    DBusGMainLoop(set_as_default=True);dbus.mainloop.glib.threads_init()
    bus=dbus.SessionBus()
    if bus.name_has_owner(core.BUS):
        api=dbus.Interface(bus.get_object(core.BUS,core.OBJECT),core.IFACE)
        if json.loads(api.Status()).get('recording'):raise RuntimeError('Сначала заверши текущую запись голоса')
        api.Action('stop')
        until=time.monotonic()+10
        while bus.name_has_owner(core.BUS) and time.monotonic()<until:time.sleep(.1)
        if bus.name_has_owner(core.BUS):raise RuntimeError('Предыдущий помощник ещё завершает работу')
    display=next((f':{n}' for n in range(91,121)
                  if not Path(f'/tmp/.X11-unix/X{n}').exists() and not Path(f'/tmp/.X{n}-lock').exists()),None)
    if not display:raise RuntimeError('Нет свободного виртуального экрана')
    authority=STATE/'Xauthority'
    authority.touch(mode=0o600,exist_ok=True);authority.chmod(0o600)
    subprocess.run(['xauth','-f',str(authority),'add',display,'.',secrets.token_hex(16)],check=True)
    IPC.mkdir(parents=True,exist_ok=True,mode=0o700);IPC.chmod(0o700)
    socket=IPC/'display.sock'
    socket.unlink(missing_ok=True)
    processes=[]
    controller=None
    with (STATE/'session.log').open('a',buffering=1) as log:
        try:
            server=subprocess.Popen(['Xtigervnc',display,'-geometry','1080x760','-depth','24',
                                     '-auth',str(authority),'-nolisten','tcp','-rfbport','-1',
                                     '-rfbunixpath',str(socket),'-rfbunixmode','0600',
                                     '-SecurityTypes','None','-AcceptCutText=0','-SendCutText=0',
                                     '-FrameRate','20','-desktop','Claude — отдельная сессия'],
                                    stdout=log,stderr=log,env=HOST)
            processes.append(server)
            until=time.monotonic()+10
            while not socket.exists() and time.monotonic()<until:
                if server.poll() is not None:raise RuntimeError('Виртуальный экран не запустился; см. session.log')
                time.sleep(.1)
            if not socket.exists():raise RuntimeError('Виртуальный экран не готов')
            os.environ['DISPLAY']=display
            os.environ['XAUTHORITY']=str(authority)
            os.environ.pop('WAYLAND_DISPLAY',None)
            os.environ['QT_QPA_PLATFORM']='xcb'
            os.environ['CLAUDE_HELPER_RUNTIME']=str(STATE)
            twm=STATE/'twmrc'
            twm.write_text('NoGrabServer\nRandomPlacement\nNoTitle\nUsePPosition "on"\n')
            processes.append(subprocess.Popen(['twm','-f',str(twm)],stdout=log,stderr=log))
            loop=GLib.MainLoop();Atspi.init();Atspi.set_timeout(1500,4000)
            controller=WorkspaceController(bus,loop,processes,fixture='--fixture' in sys.argv)
            signal.signal(signal.SIGTERM,lambda *_:GLib.idle_add(controller.shutdown))
            signal.signal(signal.SIGINT,lambda *_:GLib.idle_add(controller.shutdown))
            controller.start();loop.run()
        finally:
            if controller:controller.shutdown()
            for process in reversed(processes):
                if process.poll() is None:process.terminate()
                try:process.wait(timeout=4)
                except subprocess.TimeoutExpired:process.kill();process.wait()
            socket.unlink(missing_ok=True)


def cli():
    try:main()
    except Exception as error:
        print(str(error),file=sys.stderr);core.notify(str(error));sys.exit(1)
