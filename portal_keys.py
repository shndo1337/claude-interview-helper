"""Keyboard-only XDG portal session. No network listener or privileged input daemon."""
import json
from pathlib import Path
import threading
import uuid
import dbus


class PortalKeys:
    def __init__(self,bus,runtime):
        self.bus=bus;self.file=Path(runtime)/'keyboard-portal.json';self.session=None
        self.portal=dbus.Interface(bus.get_object('org.freedesktop.portal.Desktop','/org/freedesktop/portal/desktop'),
                                   'org.freedesktop.portal.RemoteDesktop')

    def request(self,method,*args,options=None):
        token='ci'+uuid.uuid4().hex
        sender=self.bus.get_unique_name().lstrip(':').replace('.','_')
        path='/org/freedesktop/portal/desktop/request/'+sender+'/'+token
        result={};event=threading.Event()
        def response(code,values):result.update(code=int(code),values=dict(values));event.set()
        match=self.bus.add_signal_receiver(response,signal_name='Response',dbus_interface='org.freedesktop.portal.Request',path=path)
        try:
            opts=dict(options or {},handle_token=token)
            getattr(self.portal,method)(*args,dbus.Dictionary(opts,signature='sv'))
            if not event.wait(600):
                try:dbus.Interface(self.bus.get_object('org.freedesktop.portal.Desktop',path),'org.freedesktop.portal.Request').Close()
                except dbus.DBusException:pass
                raise RuntimeError('Ожидается разрешение клавиатуры в системном окне')
            if result['code']!=0:raise RuntimeError('Управление клавиатурой не разрешено')
            return result['values']
        finally:match.remove()

    def ensure(self):
        if self.session:return
        try:
            registry=dbus.Interface(self.bus.get_object('org.freedesktop.portal.Desktop','/org/freedesktop/portal/desktop'),
                                    'org.freedesktop.portal.Registry')
            registry.Register('org.interviewhelper.ClaudeInterview',dbus.Dictionary({},signature='sv'))
        except dbus.DBusException:pass
        data=self.request('CreateSession',options={'session_handle_token':'ci'+uuid.uuid4().hex})
        session=dbus.ObjectPath(data['session_handle'])
        options={'types':dbus.UInt32(1),'persist_mode':dbus.UInt32(2)}
        try:options['restore_token']=json.loads(self.file.read_text())['restore_token']
        except (FileNotFoundError,KeyError,ValueError):pass
        self.request('SelectDevices',session,options=options)
        data=self.request('Start',session,'')
        if not int(data.get('devices',0))&1:raise RuntimeError('Не разрешена вставка с клавиатуры')
        self.session=session
        if data.get('restore_token'):
            self.file.write_text(json.dumps({'restore_token':str(data['restore_token'])}));self.file.chmod(0o600)

    def paste(self):
        self.ensure()
        opts=dbus.Dictionary({},signature='sv')
        try:
            for key,state in [(29,1),(47,1),(47,0),(29,0)]:
                self.portal.NotifyKeyboardKeycode(self.session,opts,dbus.Int32(key),dbus.UInt32(state))
        finally:
            for key in [47,29]:
                try:self.portal.NotifyKeyboardKeycode(self.session,opts,dbus.Int32(key),dbus.UInt32(0))
                except dbus.DBusException:pass

    def clear_test(self):
        self.ensure();opts=dbus.Dictionary({},signature='sv')
        for key,state in [(29,1),(30,1),(30,0),(29,0),(14,1),(14,0)]:
            self.portal.NotifyKeyboardKeycode(self.session,opts,dbus.Int32(key),dbus.UInt32(state))

    def close(self):
        if self.session:
            try:dbus.Interface(self.bus.get_object('org.freedesktop.portal.Desktop',self.session),'org.freedesktop.portal.Session').Close()
            except dbus.DBusException:pass
            self.session=None
