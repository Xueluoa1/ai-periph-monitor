import ctypes
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

import battery
import shared_readings as shared
import system_battery_evidence as evidence
import xinput_battery as xb


class SystemBatteryTests(unittest.TestCase):
    def test_xinput_uses_only_battery_api_and_four_categories(self):
        calls = []
        def query(slot, kind, ptr):
            calls.append((slot, kind))
            info = ctypes.cast(ptr, ctypes.POINTER(xb.BatteryInformation)).contents
            info.BatteryType, info.BatteryLevel = 2, slot
            return 0
        self.assertEqual([r.label for r in xb.collect(query)], ['空', '低', '中', '满'])
        self.assertEqual(calls, [(i, 0) for i in range(4)])
        with patch.object(xb, 'collect', return_value=xb.collect(query)):
            rows = xb.readings({})
        self.assertTrue(all(r.level is None and not r.ok for r in rows))
        self.assertEqual(rows[0].level_label, '空')

    def test_disconnected_wired_unknown_invalid_and_api_failure(self):
        self.assertIsNone(xb.decode(0, 0, 0))
        for typ, level in ((1, 3), (255, 3), (2, 4), (3, 255), (90, 3)):
            self.assertEqual(xb.decode(0, typ, level).label, '')
        self.assertEqual(xb.collect(lambda *args: 1167), [])
        with self.assertRaises(OSError):
            xb.collect(lambda *args: 5)
        with patch.object(xb, 'api', return_value=None):
            self.assertEqual(xb.collect(), [])

    def test_snapshot_roundtrip_disconnect_and_reassigned_slot(self):
        r = battery.DiscoveredReading('XINPUT:0', 'XInput 手柄 1', '', level_label='低')
        snap = shared.merge_snapshot(shared.empty_snapshot(), [r], 10)
        self.assertEqual(shared.readings_from(snap)[0].level_label, '低')
        self.assertEqual(shared.merge_snapshot(snap, [], 11)['readings'], [])
        failed = shared.merge_snapshot(snap, [], 11, presence={'xinput_error':'failure'})
        self.assertEqual(failed['readings'][0]['level_label'], '')
        self.assertIsNone(failed['readings'][0]['level'])
        r.level_label = ''
        self.assertEqual(shared.merge_snapshot(snap, [r], 12)['readings'][0]['level_label'], '')

    def test_bluetooth_evidence_anonymous_all_brands_zero_and_cache(self):
        d = SimpleNamespace(name='MOONDROP NEKOCAKE', container_id='private-container',
                            instance_id='private-address', battery=0, has_property=True,
                            connected=True, cached=True, headset=True, reason='系统缓存')
        rows = evidence.bluetooth_records([d])
        self.assertEqual(rows[0]['battery_percent'], 0)
        self.assertNotIn('private', json.dumps(rows))
        d.has_property=False
        self.assertIsNone(evidence.bluetooth_records([d])[0]['battery_percent'])





    def test_system_collect_isolates_bluetooth_failure(self):
        emit=Mock()
        with patch('bt_battery.list_bluetooth_devices',side_effect=OSError('BT failure')), \
             patch.object(xb,'collect',return_value=[]),patch.object(xb,'api',return_value=None):
            evidence.collect(emit)
        self.assertEqual([c.args[0] for c in emit.call_args_list],['bluetooth_battery_error','xinput_battery_evidence'])

    def test_card_shows_category_without_percentage_or_battery_bar(self):
        import tkinter as tk
        import ui_theme
        import my_devices
        from ui_cards import DeviceCard
        root=tk.Tk();root.withdraw();ui_theme.configure(root)
        try:
            card=DeviceCard(root,'XInput 手柄 1')
            item=battery.DiscoveredReading('XINPUT:0','XInput 手柄 1','系统手柄槽位',level_label='中',detail='仅四档')
            card.update(my_devices.display_readings([item])[0])
            self.assertEqual(card.lab_level.cget('text'),'中')
            self.assertEqual(card.lab_status.cget('text'),'电量档位')
            self.assertEqual(card.bar.find_all(),())
            self.assertNotIn('鼠标',card.lab_sub.cget('text'))
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()
