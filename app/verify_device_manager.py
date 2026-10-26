import copy
from contextlib import ExitStack
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch
import battery as b
import shared_readings as shared
import tray_common as tc
from tray_app import TrayApp
from device_visibility import managed_readings, visible_readings, device_options
from ui_device_manager import DeviceManager


def reading(key='0B05:1ACE:FF00', level=5):
    return b.DiscoveredReading(key=key, title='ROG 键盘', subtitle='test', level=level)


class ManagementTests(unittest.TestCase):
    def test_hide_all_surfaces_without_mutating_raw_data(self):
        rows = [reading(), reading('0B05:1ACE:FF01', 75)]
        cfg = dict(hidden_devices=[rows[0].key], custom_models={rows[1].key: '我的鼠标'})
        for surface in ('taskbar', 'widget'):
            result = visible_readings(rows, cfg, surface)
            self.assertEqual([(r.key, r.title) for r in result], [(rows[1].key, '我的鼠标')])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1].title, 'ROG 键盘')
        cfg['hidden_devices'] = []
        self.assertEqual(len(managed_readings(rows, cfg)), 2)

    def test_hidden_disconnected_device_remains_recoverable(self):
        cfg = dict(hidden_devices=['absent'], device_names={'absent': '旧键盘'})
        self.assertEqual(device_options([], cfg), [('absent', '旧键盘', False)])

    def test_hidden_does_not_notify_or_create_icon(self):
        app = object.__new__(TrayApp)
        app.cfg = dict(hidden_devices=[reading().key], notify_enabled=True, taskbar_show=True)
        app.readings = [reading()]
        app.notified_stage = {}
        app.send_notification = Mock()
        app.check_low_battery_alerts()
        app.send_notification.assert_not_called()
        self.assertEqual(app._current_slots(), [])

    def test_confirmed_empty_slot_does_not_retain_cached_battery(self):
        old = shared.merge_snapshot(shared.empty_snapshot(), [reading()], 1)
        presence = dict(hid_keys={reading().key}, excluded_keys={reading().key})
        self.assertEqual(shared.merge_snapshot(old, [], 2, presence=presence)['readings'], [])
        presence.pop('excluded_keys')
        self.assertEqual(len(shared.merge_snapshot(old, [], 2, presence=presence)['readings']), 1)

    def test_all_unpaired_channels_are_removed_from_discovery(self):
        iface = b.hw.HidInterface('test', 0x0B05, 0x1ACE, 0, 0xFF00, 1, 64, 64, 0, 'Omni', 'ASUS', '')
        result = b.BatteryResult(ok=False, source='test', extra={'unpaired_omni_slot': True})
        with patch.object(b.hw, 'enumerate_hid', return_value=[iface]), \
             patch.object(b, '_is_candidate_interface', return_value=True), \
             patch.object(b, 'read_battery', return_value=[result]), \
             patch.object(b, '_load_custom_models', return_value={}):
            presence = {}
            self.assertEqual(b.discover_and_read_all(presence=presence), [])
            self.assertIn(reading().key, presence['excluded_keys'])


class ManagerUITests(unittest.TestCase):
    def test_save_cancel_restore_and_concurrent_config(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch.object(tc, 'CONFIG_PATH', str(Path(directory) / 'config.json')))
            stack.enter_context(patch.object(tc, 'post_to_tray'))
            root = tk.Tk(); root.withdraw()
            changed = Mock()
            try:
                dlg = DeviceManager(root, [reading()], changed)
                dlg.variables[reading().key][0].set(False)
                dlg.close()
                self.assertEqual(tc.load_config()['hidden_devices'], [])
                dlg = DeviceManager(root, [reading()], changed)
                dlg.variables[reading().key][0].set(False)
                dlg.variables[reading().key][1].set('不存在的键盘')
                concurrent = tc.load_config()
                concurrent['last_readings'] = {'other': {'level': 88}}
                tc.save_config(concurrent)
                dlg.save()
                cfg = tc.load_config()
                self.assertEqual(cfg['hidden_devices'], [reading().key])
                self.assertEqual(cfg['custom_models'][reading().key], '不存在的键盘')
                self.assertEqual(cfg['last_readings'], concurrent['last_readings'])
                changed.assert_called_once()
                dlg = DeviceManager(root, [], changed)
                self.assertIn(reading().key, dlg.variables)
                dlg.restore_all()
                dlg.variables[reading().key][1].set('')
                dlg.save()
                self.assertEqual(tc.load_config()['hidden_devices'], [])
                self.assertEqual(tc.load_config()['custom_models'], {})
            finally:
                root.destroy()


if __name__ == '__main__':
    unittest.main()
