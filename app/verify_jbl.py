"""Smart Tx identity must never manufacture a battery reading or send queries."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import battery
import jbl_smart_tx as jbl
from bt_battery import BluetoothDevice


def interface(**changes):
    values = dict(vid=0x0ECB, pid=0x214B, usage_page=0xFF13, usage=1,
                  input_len=62, output_len=62, feature_len=0)
    values.update(changes)
    return SimpleNamespace(**values)


class JblTests(unittest.TestCase):
    def test_only_primary_collection_is_displayed(self):
        primary=interface()
        secondary=interface(usage_page=0xFF14,input_len=512,output_len=512)
        self.assertTrue(jbl.matches(primary))
        self.assertTrue(jbl.matches(secondary))
        self.assertTrue(battery._is_candidate_interface(primary,[]))
        self.assertFalse(battery._is_candidate_interface(secondary,[]))
        for changes in (dict(pid=0x2088),dict(usage_page=0xFF00),dict(input_len=64),dict(feature_len=62)):
            self.assertFalse(jbl.matches(interface(**changes)))

    def test_route_is_terminal_and_does_not_open_hid(self):
        with patch.object(battery,'HidDevice') as device,patch.object(battery,'strategy_angry_miao') as am:
            result=battery._read_battery_unlocked(interface())[0]
        device.assert_not_called();am.assert_not_called()
        self.assertFalse(result.ok)
        self.assertIsNone(result.level)
        self.assertEqual(result.extra['model_name'],jbl.MODEL)

    def test_bluetooth_zero_and_cached_values_remain_separate(self):
        devices=[BluetoothDevice('JBL TOUR ONE M3','private-path',battery=0,has_property=True,
                                 connected=True,container_id='private-container'),
                 BluetoothDevice('JBL Smart Tx','second-path',battery=86,has_property=True,
                                 connected=False,cached=True)]
        result=jbl.bluetooth_evidence(devices)
        self.assertEqual([d['battery_percent'] for d in result],[0,86])
        self.assertTrue(result[1]['cached'])
        self.assertFalse(result[1]['connected'])
        self.assertTrue(all(d['associated_with_usb'] is False for d in result))
        self.assertNotIn('private-path',str(result))
        self.assertNotIn('private-container',str(result))

    def test_no_brandwide_or_invalid_battery_inference(self):
        devices=[BluetoothDevice('JBL TOUR ONE M3','a',battery=101,has_property=True),
                 BluetoothDevice('JBL TOUR ONE M3','b',battery=50,has_property=False),
                 BluetoothDevice('JBL Quantum 910','c',battery=80,has_property=True),
                 BluetoothDevice('Other TOUR ONE M3','d',battery=80,has_property=True)]
        result=jbl.bluetooth_evidence(devices)
        self.assertEqual(len(result),2)
        self.assertTrue(all(d['battery_percent'] is None for d in result))


if __name__=='__main__':unittest.main()
