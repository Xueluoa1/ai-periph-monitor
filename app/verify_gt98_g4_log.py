"""Replay the 08ce15ec 2.4 GHz responses through discovery and the home page."""
from contextlib import nullcontext
from unittest import TestCase
from unittest.mock import patch

import battery
import device_io
import gravastar_k1
import my_devices
import peripheral_protocols
import shared_readings
import xinput_battery
from hid_win import HidInterface
from verify_k1 import FakeDevice


class LoggedHomeReadings(TestCase):
    def test_gt98_and_g4_dock_on_home_page(self):
        keyboard = HidInterface('hp#mi_01&col01', 0x3554, 0xFA09, 0, 0xFF02, 2,
                                20, 20, 0, '2.4G Wireless Receiver', '', '')
        headset = HidInterface('hecate#mi_03&col02', 0x35BB, 0xA22D, 0, 0xFF02, 2,
                               64, 64, 0, 'HECATE G4 MAX底座版', '', '')
        other_collection = HidInterface('hecate#mi_03&col03', 0x35BB, 0xA22D, 0,
                                       0xFF02, 2, 64, 64, 0, headset.product, '', '')
        online = bytes.fromhex('130701000101000000000000000000000000001d')
        identity = bytes.fromhex('130501000a03000000033c010000000000000066')
        notification = bytes.fromhex('130a010004055f01000000000000000000000087')
        percent = bytes.fromhex('134a0100025f01000000000000000000000000c0')
        hp = FakeDevice([online, identity, notification, percent, online, identity])
        dock = FakeDevice([bytes.fromhex('d035bb0a000164000119010a').ljust(64, b'\0')])
        devices = {keyboard.path: hp, headset.path: dock}

        with (patch.object(battery.hw, 'enumerate_hid', return_value=[other_collection, keyboard, headset]),
              patch.object(battery, 'all_protocols', return_value=[]),
              patch.object(battery, '_load_custom_models', return_value={}),
              patch.object(device_io, 'device_session', side_effect=nullcontext),
              patch.object(peripheral_protocols, 'HidDevice', side_effect=lambda path, write: devices[path]),
              patch.object(gravastar_k1, 'report_ids', side_effect=lambda dev, kind: {19} if dev is hp else {208}),
              patch.object(xinput_battery, 'readings', return_value=[])):
            readings = battery.discover_and_read_all()
        snapshot = shared_readings.merge_snapshot(shared_readings.empty_snapshot(), readings, 1)
        home = my_devices.display_readings(shared_readings.readings_from(snapshot))
        self.assertEqual([(row.title, row.level) for row in home], [
            ('惠普 HP GT98 键盘', 95), ('漫步者 HECATE G4 MAX 底座版耳机', 100)])
        self.assertTrue(all(row.ok and not row.stale for row in home))
        self.assertEqual([packet[1] for packet in hp.sent], [7, 5, 0x4A, 7, 5])
        self.assertEqual(dock.sent, [bytes.fromhex('d035bb0100').ljust(64, b'\0')])
