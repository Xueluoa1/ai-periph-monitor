"""Replay the G23's Nov 24 Feature snapshot. Pairing responses below are simulated."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import battery
import incott_receiver as incott
import peripheral_protocols as protocols

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools' / 'peripheral_diagnostic'))
import protocol_probes


class Receiver:
    def __init__(self):
        self.sent = []
        self.identity = bytes.fromhex('098f010201f0f000ff')
        self.status = bytes.fromhex('09894901000100015a')
        self.last = 0x89

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def get_feature_report(self, rid, length):
        if rid != 9 or length not in (None, 9):
            raise OSError('unexpected Feature length')
        return self.identity if self.last == 0x8F else self.status

    def set_feature_report(self, raw):
        self.sent.append(raw)
        self.last = raw[1]


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        timer = patch.object(incott.time, 'sleep')
        timer.start()
        self.addCleanup(timer.stop)
        self.dev = Receiver()
        self.d = SimpleNamespace(vid=0x093A, pid=0x522C, input_len=33, output_len=33,
                                 feature_len=9, usage_page=0xFF05, usage=1, reserved=False,
                                 product='Wireless Gaming Mouse', manufacturer='', path='test')

    def test_monitor_and_diagnostic_query_the_reported_interface(self):
        with patch.object(protocols, 'HidDevice', return_value=self.dev):
            result = protocols.read(self.d, battery.BatteryResult)
        self.assertEqual((result.level, result.status, result.extra['model_name']),
                         (73, 'discharging', '因科特 Incott G23 鼠标'))
        log = protocol_probes.run_interface_probes(self.dev, self.d, Mock())
        self.assertEqual(log['capabilities']['battery']['level_percent'], 73)
        self.assertEqual(self.dev.sent,
                         [bytes.fromhex(cmd).ljust(9,b'\0') for cmd in ('098f','0989')]*2)

    def test_sibling_and_usb_cable_are_excluded(self):
        self.d.feature_len = 0
        self.assertFalse(protocols.matches_interface(self.d))
        self.d.feature_len = 9
        self.d.pid = 0x622C
        self.assertFalse(protocols.matches_interface(self.d))

    def test_long_feature_and_legacy_input(self):
        self.d.feature_len = 64
        dev = Mock()
        dev.get_feature_report.return_value = self.dev.identity.ljust(64, b'\0')
        self.assertEqual(incott.query(dev, self.d, 0x8F)[:9], self.dev.identity)
        dev.set_feature_report.assert_called_once_with(bytes.fromhex('098f').ljust(64, b'\0'))
        dev.get_feature_report.assert_called_with(9, 64)
        self.d.product, self.d.feature_len = 'incott 8k wireless mouse', 0
        listener = Mock(return_value=((95,'discharging',''), b'event'))
        dev.reset_mock()
        self.assertEqual(incott.read(dev, self.d, listener), listener.return_value)
        listener.assert_called_once_with(None)
        dev.set_feature_report.assert_not_called()

    def test_unknown_shared_receiver_never_gets_incott_battery_command(self):
        self.dev.identity = bytes.fromhex('098f011402f0f100ff')
        with self.assertRaises(ValueError):
            incott.read(self.dev, self.d, Mock(side_effect=AssertionError()))
        self.assertEqual([pkt[1] for pkt in self.dev.sent], [0x8F])

    def test_offline_or_invalid_feature_does_not_manufacture_a_level(self):
        for raw in ('098900000000000000','0989e501000100015a','09814901000100015a','098949'):
            self.assertIsNone(incott.decode_status(bytes.fromhex(raw)))
        self.assertEqual(incott.decode_status(bytes.fromhex('0989c901000100015a'))[:2],
                         (73, 'charging'))
        self.dev.status = bytes(9)
        event = bytes.fromhex('09 e1 10 04 00 0f 0f 10')
        fallback = Mock(return_value=(protocols.decode('incott', event), event))
        result, raw = incott.read(self.dev, self.d, fallback)
        self.assertEqual((result[:2], raw), ((97, 'charging'), event))
        fallback.assert_called_once_with(self.dev.identity[2:7])


if __name__ == '__main__':
    unittest.main()
