"""Wireless query regression tests; synthetic frames except noted captures."""
from dataclasses import replace
from types import SimpleNamespace
import itertools
import unittest
from unittest.mock import Mock, patch

import battery
import receiver_battery as rx
import shared_readings as shared


def iface(vid, pid, protocol=None):
    protocol = protocol or rx.CATALOG[vid, pid].protocol
    page, usage, inp, out, feature = {
        'hyperx2': (0xFF90, 0x0303, 20, 52, 0),
        'hyperx3': (0xFF13, 1, 62, 62, 0),
        'nzxt': (0xFFCA, 1, 64, 64, 0),
        'keychron': (0xFF00, 1, 64, 0, 64),
        'hitscan': (0xFF02, 2, 17, 17, 0),
        'gw_old': (0xFFFF, 1, 0, 0, 65),
        'gw_new': (0xFFFF, 1, 0, 0, 65),
    }.get(protocol, (0xFFC0, 1, 64, 64, 0))
    return SimpleNamespace(vid=vid, pid=pid, usage_page=page, usage=usage,
                           input_len=inp, output_len=out, feature_len=feature,
                           path='test&mi_04#vendor', product='', manufacturer='',
                           reserved=False, vid_pid=f'{vid:04X}:{pid:04X}')


class ReceiverTests(unittest.TestCase):
    def test_catalog_uses_complete_interface_match(self):
        for vid, pid in rx.CATALOG:
            d = iface(vid, pid)
            with self.subTest(vid=vid, pid=pid):
                self.assertTrue(rx.matches(d))
                self.assertTrue(battery._is_candidate_interface(d, []))
                d.usage_page = 1
                self.assertFalse(rx.matches(d))
                self.assertFalse(battery._is_candidate_interface(d, []))
                dev = Mock()
                with self.assertRaises(ValueError):rx.query(dev, d)
                self.assertEqual(dev.mock_calls, [])

    def test_cable_and_unknown_ids_are_not_added(self):
        for vid, pid in ((0x3770,0x0100), (0x3434,0xD048), (0x1E71,0x2129),
                         (0x33E4,0x5804), (0x33E4,0x3808), (0x33E4,0xFFFF),
                         (0x1038,0xFFFF), (0x03F0,0xFFFF)):
            self.assertIsNone(rx.profile_for(SimpleNamespace(vid=vid,pid=pid)))

    def test_keychron_needs_management_interface(self):
        d=iface(0x3434,0xD028)
        d.path='test&mi_01#vendor'
        self.assertFalse(rx.matches(d))
        d.path='test&mi_040#vendor'
        self.assertFalse(rx.matches(d))

    def test_nova_generation_and_discrete_values(self):
        for code in range(5):
            r=bytes((0xB0,3,code,3))
            pct=rx.decode('nova7_percent',r)
            step=rx.decode('nova7_steps',r)
            self.assertEqual(pct.level,code)
            self.assertIsNone(step.level)
            self.assertEqual(step.label,f'{code}/4 档')
            self.assertEqual(rx.decode('nova7_steps',b'\0'+r),step)
        for r in (b'\xb0\x02\x64\x03',b'\xb0\x03\x64\0',b'\xb0\x03\x65\x03',
                  b'\x01\0\x63\x02',b'\xb0\x03\x64\x04', b'\xb0\x03'):
            self.assertIsNone(rx.decode('nova7_percent',r))
        self.assertIsNone(rx.decode('nova7_steps',b'\xb0\x03\x05\x03'))
        self.assertEqual(rx.decode('nova7_percent',b'\xb0\x03\x64\x01').status,'charging')

    def test_nova5_and_arctis7_offline_echo_bounds(self):
        self.assertEqual(rx.decode('nova5',bytes.fromhex('b003003201').ljust(64,b'\0')).level,50)
        self.assertEqual(rx.decode('arctis7_steps',bytes.fromhex('b0030400')).label,'4/4 档')
        for protocol in ('nova5','arctis7_steps'):
            self.assertIsNone(rx.decode(protocol,b'\0\xb0'+bytes(62)))
            self.assertIsNone(rx.decode(protocol,b'\xb0'+bytes(63)))
        self.assertIsNone(rx.decode('nova5',bytes.fromhex('b002003201').ljust(64,b'\0')))
        self.assertIsNone(rx.decode('arctis7_steps',bytes.fromhex('b0010400')))

    def test_hyperx_capture_and_empty_replies(self):
        # Public NGENUITY capture described in HaloBattery issue 155: 48%.
        r=bytes.fromhex('66890ed730').ljust(62,b'\0')
        self.assertEqual(rx.decode('hyperx3',r).level,48)
        self.assertEqual(rx.decode('hyperx3',r[:4]+b'\0'+r[5:]).level,0)
        self.assertIsNone(rx.decode('hyperx3',r[:4]+b'\xff'+r[5:]))
        for r in (bytes.fromhex('6689').ljust(62,b'\0'),bytes(62),b'\x66\x89\x0e\xd7\x30'):
            self.assertIsNone(rx.decode('hyperx3',r))
        r=bytes.fromhex('06ffbb02000fd249').ljust(20,b'\0')
        self.assertEqual(rx.decode('hyperx2',r).level,73)
        self.assertIsNone(rx.decode('hyperx2',bytes.fromhex('06ffbb02').ljust(52,b'\0')))

    def test_nzxt_endianness_ack_and_voltage(self):
        r=bytearray(64);r[:3]=bytes.fromhex('4e0297');r[8:10]=bytes.fromhex('0ffd');r[17]=77
        self.assertEqual(rx.decode('nzxt',r).voltage_mv,4093)
        self.assertEqual(rx.decode('nzxt',r).level,77)
        r[22]=1
        self.assertEqual(rx.decode('nzxt',r).status,'charging')
        r[18]=1
        self.assertIsNone(rx.decode('nzxt',r))
        self.assertIsNone(rx.decode('nzxt',bytes.fromhex('4ee5').ljust(64,b'\0')))

    def test_gwolves_windows_offsets_status_and_zero(self):
        for protocol,header in (('gw_new','a100020200830100'),('gw_old','a1028f000100')):
            r=bytes.fromhex(header).ljust(64,b'\0')
            for prefix in (b'',b'\0'):
                self.assertEqual(rx.decode(protocol,prefix+r).level,0)
                self.assertEqual(rx.decode(protocol,prefix+r).status,'charging')
                self.assertIsNone(rx.decode(protocol,(prefix+r).replace(b'\xa1',b'\xa2',1)))
        self.assertIsNone(rx.decode('gw_old',bytes.fromhex('a1028f000265').ljust(64,b'\0')))
        self.assertIn('共享型号',rx.CATALOG[0x33E4,0x3854].name)

    def test_public_hitscan_frame_and_checksum(self):
        raw=bytes.fromhex('08040000000264001129000000000000a9')
        self.assertEqual(rx.decode('hitscan',raw).level,100)
        self.assertIsNone(rx.decode('hitscan',raw[:-1]+b'\0'))

    def test_feature_queries_have_exact_wireless_frames(self):
        for pid,header,reply in ((0x3817,'00000002020083','00a10002020083002a'),
                                 (0x5803,'0000028f01','00a1028f00002a')):
            dev=Mock();dev.get_feature_report.return_value=bytes.fromhex(reply).ljust(65,b'\0')
            with patch.object(rx.time,'sleep'):
                self.assertEqual(rx.query(dev,iface(0x33E4,pid)).level,42)
            dev.set_feature_report.assert_called_once_with(bytes.fromhex(header).ljust(65,b'\0'))
            dev.write_output.assert_not_called()
        d=iface(0x3434,0xD028);r=bytearray(64);r[:2]=b'\xb4\x06';r[20]=42
        dev=Mock();dev.read_input.return_value=bytes(r)
        self.assertEqual(rx.query(dev,d).level,42)
        dev.set_feature_report.assert_called_once_with(b'\xb3\x06'+bytes(62))
        dev.write_output.assert_not_called()

    def test_input_query_skips_echo_and_unrelated_reports(self):
        d=iface(0x1038,0x22A1);dev=Mock()
        dev.read_input.side_effect=[b'\0\xb0'+bytes(62),bytes(64),b'\xb0\x03\x49\x03'+bytes(60)]
        self.assertEqual(rx.query(dev,d).level,73)
        dev.write_output.assert_called_once_with(b'\0\xb0'+bytes(62),use_hidd=False)

    def test_hyperx_secondary_failure_preserves_battery(self):
        d=iface(0x03F0,0x05B7)
        with patch.object(rx,'_input_query',side_effect=[(rx.Reading(48),b'capture'),OSError('busy')]):
            result=rx.query(Mock(),d)
        self.assertEqual((result.level,result.status,result.raw),(48,'unknown',b'capture'))

    def test_failed_diagnostic_write_cannot_accept_old_input(self):
        dev=Mock();dev.write_output.return_value=False
        dev.read_input.return_value=b'\xb0\x03\x49\x03'+bytes(60)
        with self.assertRaises(OSError):rx.query(dev,iface(0x1038,0x22A1))
        dev.read_input.assert_not_called()

    def test_timeout_and_terminal_route_never_guess(self):
        d=iface(0x1038,0x22A1);dev=Mock();dev.read_input.return_value=b''
        with patch.object(rx.time,'monotonic',side_effect=itertools.count()):
            self.assertIsNone(rx.query(dev,d))
        failure=battery.BatteryResult(ok=False,detail='asleep')
        with patch.object(rx,'read',return_value=failure), patch.object(battery,'strategy_standard_battery_usage') as generic:
            self.assertEqual(battery._read_battery_unlocked(d),[failure])
            generic.assert_not_called()

    def test_category_roundtrip_failure_and_disconnect(self):
        r=battery.DiscoveredReading('1038:2202:FFC0','Nova 7','',level_label='2/4 档')
        snap=shared.merge_snapshot(shared.empty_snapshot(),[r],10)
        self.assertEqual(shared.readings_from(snap)[0].level_label,'2/4 档')
        failed=shared.merge_snapshot(snap,[replace(r,level_label='',error='asleep')],11)
        row=shared.readings_from(failed)[0]
        self.assertTrue(row.stale)
        self.assertIn('上次电量',row.status)
        self.assertIsNone(row.level)
        self.assertEqual(shared.merge_snapshot(snap,[],12)['readings'],[])


if __name__ == '__main__':
    unittest.main()
