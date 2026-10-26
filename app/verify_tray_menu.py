"""Native menu routing and cross-process settings synchronization regressions."""
import copy
from contextlib import ExitStack
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch
import tray_app as tray
import tray_common as tc
import my_devices
import shared_readings as shared
import ui_theme as ui


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.cfg = copy.deepcopy(tc.DEFAULT_CONFIG)
        self.stack.enter_context(patch.object(tc, 'load_config', side_effect=lambda: copy.deepcopy(self.cfg)))
        self.save = self.stack.enter_context(patch.object(tc, 'save_config', side_effect=self.persist))
        self.stack.enter_context(patch.object(tc, 'is_autostart_enabled', return_value=False))
        self.stack.enter_context(patch.object(tc, 'sync_widget_state'))
        self.stack.enter_context(patch.object(tc, 'promote_tray_icons_to_taskbar'))
        for name in ('CreatePopupMenu', 'GetCursorPos', 'SetForegroundWindow', 'PostMessageW', 'DestroyMenu'):
            self.stack.enter_context(patch.object(tray.USER32, name, return_value=1))
        self.append = self.stack.enter_context(patch.object(tray.USER32, 'AppendMenuW'))
        self.track = self.stack.enter_context(patch.object(tray.USER32, 'TrackPopupMenu', return_value=0))
        self.app = object.__new__(tray.TrayApp)
        self.app.cfg = copy.deepcopy(self.cfg)
        self.app.hwnd = 1
        self.app.open_ui = Mock()
        self.app.update_icons = Mock()
        self.app.log = Mock()

    def persist(self, cfg):
        self.cfg.clear(); self.cfg.update(copy.deepcopy(cfg))

    def test_cancel_only_renders_fresh_checkmarks(self):
        self.cfg.update(widget_compact=True, widget_locked=True, desktop_widget=False)
        self.app.show_context_menu()
        entries = {c.args[2]: (c.args[1], c.args[3]) for c in self.append.call_args_list}
        self.assertEqual(entries[tray.IDM_HOME][1], '打开主页')
        self.assertTrue(entries[tray.IDM_WIDGET_COMPACT][0] & tray.MF_CHECKED)
        self.assertTrue(entries[tray.IDM_WIDGET_LOCK][0] & tray.MF_CHECKED)
        self.assertFalse(entries[tray.IDM_DESKTOP_PIN][0] & tray.MF_CHECKED)
        self.save.assert_not_called()

    def test_all_four_toggles_persist_both_directions_and_apply(self):
        for command, key, _, default in tray.MENU_TOGGLES:
            for before in (False, True):
                with self.subTest(key=key, before=before):
                    self.cfg[key] = before
                    self.app.cfg = copy.deepcopy(self.cfg)
                    self.app.open_ui.reset_mock()
                    self.app.update_icons.reset_mock()
                    self.track.return_value = command
                    self.app.show_context_menu()
                    self.assertEqual(self.cfg[key], not before)
                    self.assertEqual(self.app.cfg[key], not before)
                    if key == 'taskbar_show':
                        self.app.update_icons.assert_called_once()
                        if before:
                            self.app.open_ui.assert_called_once_with(home=True)
                    else:
                        self.app.open_ui.assert_not_called()

    def test_save_keeps_changes_that_arrive_while_menu_open(self):
        def choose(*args):
            self.cfg['last_readings'] = {'another': {'level': 42}}
            self.cfg['hidden_devices'] = ['keyboard']
            return tray.IDM_WIDGET_LOCK
        self.track.side_effect = choose
        self.app.show_context_menu()
        self.assertTrue(self.cfg['widget_locked'])
        self.assertEqual(self.cfg['hidden_devices'], ['keyboard'])
        self.assertEqual(self.cfg['last_readings'], {'another': {'level': 42}})

    def test_home_does_not_write_settings(self):
        self.track.return_value = tray.IDM_HOME
        self.app.show_context_menu()
        self.app.open_ui.assert_called_once_with(home=True)
        self.save.assert_not_called()


def walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from walk(child)


class UISyncTests(unittest.TestCase):
    def test_external_toggles_update_open_window_and_home_navigation(self):
        cfg = copy.deepcopy(tc.DEFAULT_CONFIG)
        with ExitStack() as stack:
            stack.enter_context(patch.object(tc, 'load_config', side_effect=lambda: copy.deepcopy(cfg)))
            stack.enter_context(patch.object(tc, 'save_config', side_effect=lambda value: cfg.update(copy.deepcopy(value))))
            for name in ('sync_widget_state', 'post_to_tray', 'promote_tray_icons_to_taskbar'):
                stack.enter_context(patch.object(tc, name))
            stack.enter_context(patch.object(tc, 'is_tray_running', return_value=True))
            stack.enter_context(patch.object(tc, 'is_widget_running', return_value=False))
            stack.enter_context(patch.object(tc, 'is_autostart_enabled', return_value=False))
            stack.enter_context(patch.object(shared, 'load_snapshot', return_value=shared.empty_snapshot()))
            stack.enter_context(patch.object(my_devices, 'read_all', return_value=[]))
            root = my_devices.build_ui(); root.withdraw()
            errors = []
            root.report_callback_exception = lambda *args: errors.append(str(args[1]))
            stack.enter_context(patch.object(tc, 'ui_hwnd', return_value=root.winfo_id()))
            def pump():
                end = time.monotonic() + 1.2
                while time.monotonic() < end:
                    root.update(); time.sleep(.02)
            def button(text):
                return next(w for w in walk(root) if isinstance(w, ttk.Button) and w.cget('text') == text)
            def switch(text):
                label = next(w for w in walk(root) if isinstance(w, ttk.Label) and w.cget('text') == text)
                return next(w for w in walk(label.master.master) if isinstance(w, ui.Switch))
            try:
                pump()
                button('设置').invoke()
                cfg.update(taskbar_show=False, desktop_widget=False, widget_compact=True, widget_locked=True)
                pump()
                for label, expected in [('显示到任务栏', False), ('显示桌面挂件', False), ('紧凑模式', True), ('锁定位置', True)]:
                    self.assertEqual(switch(label).variable.get(), expected, label)
                # Real Win32 property is consumed by Tk's pump and returns home.
                self.assertTrue(tc.request_ui_home(root.winfo_id()))
                pump()
                from ui_device_list import DeviceList
                listing = next(w for w in walk(root) if isinstance(w, DeviceList))
                self.assertEqual(listing.master.winfo_manager(), 'pack')
                self.assertEqual(button('返回设备').master.master.winfo_manager(), '')
                self.assertFalse(tc.consume_ui_home())
                self.assertFalse(errors, errors)
            finally:
                for callback in root.tk.call('after', 'info'):
                    root.tk.call('after', 'cancel', callback)
                root.destroy()


if __name__ == '__main__':
    unittest.main()
