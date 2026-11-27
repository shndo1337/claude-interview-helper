import json
import unittest
from unittest.mock import Mock,patch
import dbus
from interview_helper.launch_modes import prepare_launch,running_helper


class ModeTests(unittest.TestCase):
    def api(self,workspace=False,recording=False,backend='claude'):
        api=Mock()
        api.Backend.return_value=backend
        if workspace:
            api.WorkspaceInfo.return_value=json.dumps({'backend':backend})
        else:
            api.WorkspaceInfo.side_effect=dbus.DBusException('unknown',name='org.freedesktop.DBus.Error.UnknownMethod')
        api.Status.return_value=json.dumps({'recording':recording})
        return api

    def test_same_mode_reuses_existing_helper(self):
        for workspace,wanted,backend in [(False,'classic','claude'),(True,'workspace','claude'),
                                        (False,'codex-classic','codex'),(True,'codex-workspace','codex')]:
            bus=Mock();bus.name_has_owner.return_value=True
            api=self.api(workspace,backend=backend)
            with patch('interview_helper.launch_modes.dbus.Interface',return_value=api):
                self.assertIs(prepare_launch(bus,wanted),api)
            api.Action.assert_not_called()

    def test_switches_both_directions_after_old_mode_stops(self):
        for workspace,wanted in [(False,'workspace'),(True,'classic')]:
            bus=Mock();bus.name_has_owner.side_effect=[True,False,False]
            api=self.api(workspace)
            with patch('interview_helper.launch_modes.dbus.Interface',return_value=api):
                self.assertIsNone(prepare_launch(bus,wanted))
            api.Action.assert_called_once_with('stop')

    def test_switch_does_not_discard_an_active_recording(self):
        bus=Mock();bus.name_has_owner.return_value=True
        api=self.api(recording=True)
        with patch('interview_helper.launch_modes.dbus.Interface',return_value=api):
            with self.assertRaisesRegex(RuntimeError,'запись'):prepare_launch(bus,'workspace')
        api.Action.assert_not_called()

    def test_transport_errors_are_not_treated_as_classic_mode(self):
        bus=Mock();bus.name_has_owner.return_value=True
        api=self.api();api.WorkspaceInfo.side_effect=dbus.DBusException('denied',name='org.freedesktop.DBus.Error.AccessDenied')
        with patch('interview_helper.launch_modes.dbus.Interface',return_value=api):
            with self.assertRaises(dbus.DBusException):running_helper(bus)
        api.Action.assert_not_called()


if __name__=='__main__':unittest.main()
