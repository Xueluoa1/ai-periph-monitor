"""Merged tray selection, native event routing, and popup lifecycle regressions."""
import copy
import ctypes
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import tray_common as tc
import tray_app as tray
from tray_merge import taskbar_rows, primary_row, popup_position


def reading(key, title, level):
    return SimpleNamespace(key=key, title=title, level=level, ok=level is not None,
                           status='已连接', error='', is_headset=key.startswith('BT:'), voltage_mv=None)


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(tc.DEFAULT_CONFIG)
        self.cfg.update(taskbar_merge=True, taskbar_primary='HID:mouse')
        self.readings = [reading('HID:mouse', '鼠标', 48), reading('BT:headset', '耳机', 100)]
        self.cfg['device_names'] = {r.key: r.title for r in self.readings}
        self.app = tray.TrayApp.__new__(tray.TrayApp)
        self.app.cfg, self.app.readings = self.cfg, self.readings
        self.app.hwnd, self.app.active_uids, self.app.hover = 1, set(), Mock()
        self.app.log = Mock()

    def test_one_icon_tracks_selected_device_and_keeps_identity_after_disconnect(self):
        self.assertEqual(self.app._current_slots()[0][1:3], ('鼠标', 48))
        self.app.readings = self.readings[1:]
        self.assertEqual(self.app._current_slots()[0][1:4], ('鼠标', None, '未连接'))
        self.app.readings = list(reversed(self.readings))
        self.assertEqual(self.app._current_slots()[0][1:3], ('鼠标', 48))
        self.cfg['taskbar_primary'] = 'BT:headset'
        self.assertEqual(self.app._current_slots()[0][1:3], ('耳机', 100))

    def test_filter_hidden_empty_off_and_disconnected_selection(self):
        self.cfg['taskbar_devices'] = ['BT:headset']
        self.assertEqual([r.title for r in taskbar_rows([], self.cfg)], ['耳机'])
        self.assertEqual(self.app._current_slots()[0][1], '耳机')
        self.cfg['hidden_devices'] = ['BT:headset']
        self.assertEqual(self.app._current_slots(), [])
        self.cfg.update(hidden_devices=[], taskbar_devices=[])
        self.assertEqual(self.app._current_slots(), [])
        self.cfg.update(taskbar_devices=None, taskbar_show=False)
        self.assertEqual(self.app._current_slots(), [])

    def test_names_zero_and_unknown_are_preserved(self):
        self.cfg['custom_models'] = {'HID:mouse': '我的鼠标'}
        self.readings[0].level = 0
        rows = taskbar_rows(self.readings, self.cfg)
        self.assertEqual((rows[0].title, rows[0].level), ('我的鼠标', 0))
        self.readings[0].ok = False
        self.assertIsNone(taskbar_rows(self.readings, self.cfg)[0].level)
        self.assertEqual(primary_row(rows, self.cfg), rows[0])

    def test_toggle_removes_old_icons_and_restores_independent_icons(self):
        self.app._apply_icon = Mock(return_value=True)
        with patch.object(tray.SHELL32, 'Shell_NotifyIconW', return_value=True), \
             patch.object(tc, 'promote_tray_icons_to_taskbar'):
            self.cfg['taskbar_merge'] = False
            self.app.update_icons()
            separate = self.app.active_uids.copy()
            self.assertEqual(len(separate), 2)
            self.cfg['taskbar_merge'] = True
            self.app.update_icons()
            self.assertEqual(self.app.active_uids, {1})
            self.cfg['taskbar_merge'] = False
            self.app.update_icons()
            self.assertEqual(self.app.active_uids, separate)
            self.app.hover.hide.assert_called()

    def test_native_version_and_events(self):
        calls = []
        def notify(action, pointer):
            nid = ctypes.cast(pointer, ctypes.POINTER(tray.NOTIFYICONDATAW)).contents
            calls.append((action, nid.uVersion))
            return True
        with patch.object(tray.win_icon, 'make_hicon', return_value=123), \
             patch.object(tray.SHELL32, 'Shell_NotifyIconW', side_effect=notify):
            self.app._apply_icon(tray.NIM_ADD, 1, '鼠标', 48, '', False)
            self.assertEqual(calls[-1], (tray.NIM_SETVERSION, 4))
            self.cfg['taskbar_merge'] = False
            self.app._apply_icon(tray.NIM_ADD, 2, '鼠标', 48, '', False)
            self.assertEqual(calls[-1], (tray.NIM_SETVERSION, 0))
        self.cfg['taskbar_merge'] = True
        self.app.show_hover, self.app.refresh_now, self.app.show_context_menu = Mock(), Mock(), Mock()
        for event in (tray.NIN_POPUPOPEN, tray.NIN_KEYSELECT, tray.NIN_SELECT, tray.WM_CONTEXTMENU):
            self.app.wnd_proc(1, tray.MSG_TRAY_CALLBACK, 0, (1 << 16) | event)
        self.assertEqual(self.app.show_hover.call_count, 2)
        self.app.refresh_now.assert_called_once()
        self.app.show_context_menu.assert_called_once()

    def test_monitor_position_does_not_escape_work_area(self):
        anchor = (900, 1040, 924, 1080)
        x, _ = popup_position(anchor, (0, 0, 1920, 1040), 270, 100)
        self.assertEqual(x + 135, (anchor[0] + anchor[2]) // 2)
        for work, anchor in [((0, 0, 1920, 1040), (1800, 1040, 1824, 1080)),
                             ((-1920, -200, 0, 880), (-1900, -200, -1876, -160)),
                             ((1920, 0, 3840, 1080), (1920, 500, 1960, 524))]:
            x, y = popup_position(anchor, work, 340, 200)
            self.assertTrue(work[0] <= x <= work[2] - 340)
            self.assertTrue(work[1] <= y <= work[3] - 200)


if __name__ == '__main__':
    unittest.main()
