"""Read-only synthetic protocol replies; no local hardware validation implied."""
import unittest
from unittest.mock import Mock, patch
import darmoshark_telink as d
import peripheral_protocols as p
import battery
from verify_wireless_battery import iface


def receiver(pid=0xff30):
    result=iface(0x248a,pid,0x8c,1,33)
    result.feature_len=1001
    return result


def bond(mouse=0xff12,connected=1):
    return bytes((0x51,3,6,0x8a,0x24,mouse&255,mouse>>8,connected)).ljust(1001,b'\0')


def identity(mouse=0xff12,level=62):
    r=bytearray(1001);r[:3]=b'\x51\x06\x01'
    r[4:8]=b'\x8a\x24'+mouse.to_bytes(2,'little');r[11]=level
    return bytes(r)


class TelinkTests(unittest.TestCase):
    def test_two_models_fresh_battery_after_bond(self):
        for pid,(mouse,name) in d.MODELS.items():
            for level in (0,62,100):
                dev=Mock();dev.read_input.return_value=b''
                dev.get_feature_report.side_effect=[identity(mouse,99),bond(mouse),bond(mouse),identity(mouse,level)]
                self.assertEqual(d.read(dev,receiver(pid))[0],(level,'unknown',name))
                self.assertEqual([c.args[0] for c in dev.set_feature_report.call_args_list],
                    [bytes((0x51,op)).ljust(1001,b'\0') for op in (3,6)])
                dev.write_output.assert_not_called()

    def test_unknown_pair_sleep_corrupt_battery(self):
        for b in (bond(0xff18),bond(connected=0)):
            with patch.object(d,'query',return_value=b) as query:
                self.assertIsNone(d.read(Mock(),receiver())[0]);query.assert_called_once()
        for r in (identity(0xff18),identity(level=101)):
            with patch.object(d,'query',side_effect=[bond(),r]):
                self.assertIsNone(d.read(Mock(),receiver())[0])
        dev=Mock();dev.read_input.return_value=b'\x54\xe4\x02'
        self.assertIsNone(d.read(dev,receiver())[0]);dev.get_feature_report.assert_not_called()

    def test_timeout_never_accepts_stale_identity(self):
        dev=Mock();dev.read_input.return_value=b'';dev.get_feature_report.return_value=identity()
        with patch.object(d.time,'monotonic',side_effect=[0,0,1]):
            self.assertIsNone(d.read(dev,receiver())[0])
        self.assertEqual(dev.set_feature_report.call_count,1)

    def test_exact_interface_and_terminal_main_route(self):
        r=receiver();self.assertTrue(p.matches_interface(r))
        with patch.object(p,'read',return_value='ok'):
            self.assertEqual(battery._read_battery_unlocked(r),['ok'])
        for attr,value in (('pid',0xff12),('usage_page',1),('usage',2),('feature_len',65),('input_len',65)):
            with patch.object(r,attr,value):self.assertFalse(d.matches(r))
        with self.assertRaises(ValueError):d.query(Mock(),0x40,0)
