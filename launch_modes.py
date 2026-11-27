"""Select a helper mode without silently reopening the other mode."""
import json
import time
import dbus

NAME='org.interviewhelper.ClaudeInterview'
PATH='/org/interviewhelper/ClaudeInterview'

def running_helper(bus):
    if not bus.name_has_owner(NAME):return None,None
    api=dbus.Interface(bus.get_object(NAME,PATH),NAME)
    try:
        info=json.loads(api.WorkspaceInfo())
        return api,'codex-workspace' if info.get('backend')=='codex' else 'workspace'
    except dbus.DBusException as error:
        if error.get_dbus_name() not in ['org.freedesktop.DBus.Error.UnknownMethod',
                                         'org.freedesktop.DBus.Error.UnknownInterface']:raise
        try:
            return api,'codex-classic' if api.Backend()=='codex' else 'classic'
        except dbus.DBusException as backend_error:
            if backend_error.get_dbus_name()!='org.freedesktop.DBus.Error.UnknownMethod':raise
            return api,'classic'

def prepare_launch(bus,wanted):
    api,mode=running_helper(bus)
    if api is None or mode==wanted:return api
    if json.loads(api.Status()).get('recording'):
        raise RuntimeError('Сначала заверши запись голоса, затем переключи режим')
    api.Action('stop')
    until=time.monotonic()+10
    while bus.name_has_owner(NAME) and time.monotonic()<until:time.sleep(.1)
    if bus.name_has_owner(NAME):raise RuntimeError('Предыдущий режим ещё завершает работу')
    return None
