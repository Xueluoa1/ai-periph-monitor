"""Wireless battery frames: captured samples plus malformed synthetic replies."""
import unittest
from unittest.mock import Mock, patch

import battery
import receiver_battery as rx
import sony_inzone_battery as sony
from verify_receiver_battery import iface


def sony_reply(tid=0x1234, level=73, charging=0):
    # Synthetic valid EVENT with independent, fixed-length battery layout.
    r = bytearray.fromhex('020e04ff0b0096c314041034120049bf')
    r[11:13] = tid.to_bytes(2, 'little')
    r[13:15] = bytes((charging, level))
    r[15] = sum(r[5:15]) & 255
    return bytes(r).ljust(64, b'\0')


class ExpandedBatteryTests(unittest.TestCase):
    def test_kp70c2_capture_and_other_plain_skus(self):
        raw = bytes.fromhex('02155b0050010050010200ffff0be8e7').ljust(64, b'\0')
        for pid in rx.BETOP_MODELS:
            d = iface(0x20BC, pid); dev = Mock()
            dev.read_input.return_value = raw
            result = rx.query(dev, d)
            self.assertEqual((result.level, result.status), (91, 'discharging'))
            dev.write_output.assert_called_once_with(b'\x02\x15'+bytes(62), use_hidd=False)
        for pid in (0x5126, 0x5128, 0x5125, 0x5127, 0x5154, 0x5155, 0x515D, 0x515E,
                    0x5169, 0x516A, 0x5161, 0x5162, 0x518C):
            self.assertNotIn((0x20BC, pid), rx.CATALOG)
        self.assertNotIn((0x20DD, 0x518D), rx.CATALOG)
        self.assertEqual(rx.decode('betop_kp_plain', bytes.fromhex('02155b0057010050')).status, 'unknown')

    def test_eightbitdo_public_pro3_capture_is_passive(self):
        raw = bytes.fromhex('040f807f7f80000000000000096014').ljust(64, b'\0')
        dev = Mock(); dev.read_input.side_effect = [bytes(64), raw]
        result = rx.query(dev, iface(0x2DC8, 0x6009))
        self.assertEqual((result.level, result.status), (20, 'discharging'))
        self.assertEqual([c[0] for c in dev.mock_calls], ['read_input', 'read_input'])
        d = iface(0x2DC8, 0x6009)
        d.path = 'hid#vid_2dc8&pid_6009&mi_00#usb'
        self.assertFalse(rx.matches(d))
        with self.assertRaises(ValueError): rx.query(Mock(), d)

    def test_eightbitdo_battery_bounds_and_ordinary_reports(self):
        for protocol, rid in (('eightbitdo_bt', 1), ('eightbitdo_pro3', 4)):
            for value in (1, 20, 100, 0x81, 0xE4):
                raw = bytes((rid,))+bytes(13)+bytes((value,))
                result = rx.decode(protocol, raw)
                self.assertEqual(result.level, value & 127)
                self.assertEqual(result.status, 'charging' if value & 128 else 'discharging')
            for raw in (bytes((rid,))+bytes(63), bytes((rid,))+bytes(13),
                        bytes((rid,))+bytes(13)+b'\x65', bytes((rid,))+bytes(13)+b'\xff',
                        b'\x03'+bytes(13)+b'\x64'):
                self.assertIsNone(rx.decode(protocol, raw))
        dev = Mock(); dev.read_input.return_value = bytes(64)
        with patch.object(rx.time, 'monotonic', return_value=0):
            self.assertIsNone(rx.query(dev, iface(0x2DC8, 0x6003)))
        self.assertEqual(dev.read_input.call_count, 16)
        dev.get_feature_report.assert_not_called()
        dev.write_output.assert_not_called()
        dev.set_feature_report.assert_not_called()

    def test_eightbitdo_monitor_uses_read_access_and_bluetooth_label(self):
        d = iface(0x2DC8, 0x6009)
        with patch.object(rx, 'HidDevice') as hid, patch.object(rx, 'query', return_value=rx.Reading(20)):
            result = rx.read(d, battery.BatteryResult)
        hid.assert_called_once_with(d.path, write=False)
        self.assertTrue(result.ok)
        self.assertEqual(result.extra['connection'], '蓝牙')

    def test_alpha2_captured_percent_and_invalid_replies(self):
        # Public Cloud Alpha 2 sample cross-checked with NGENUITY at 51%.
        raw = bytes.fromhex('510233001a00').ljust(64, b'\0')
        self.assertEqual(rx.decode('hyperx_alpha2', raw).level, 51)
        self.assertEqual(rx.decode('hyperx_alpha2', raw).status, 'unknown')
        for invalid in (raw[:63], raw.replace(b'\x51', b'\x50', 1),
                        b'\x51\x02\xff'+bytes(61)):
            self.assertIsNone(rx.decode('hyperx_alpha2', invalid))
        dev = Mock(); dev.read_input.return_value = raw
        self.assertEqual(rx.query(dev, iface(0x03F0, 0x08BE)).level, 51)
        dev.write_output.assert_called_once_with(b'\x50\x02'+bytes(62), use_hidd=False)
        self.assertNotIn((0x03F0, 0x0ABE), rx.CATALOG)

    def test_gamebuds_selects_only_active_ears(self):
        for frame, expected in (('b0000003035028', 40), ('b00000030250ff', 80),
                                ('b000000203ff00', 0), ('b0000002020000', None),
                                ('b000000303ff28', None), ('b000000303', None)):
            for prefix in (b'', b'\0'):
                result = rx.decode('arctis_gamebuds', prefix+bytes.fromhex(frame))
                self.assertEqual(result.level if result else None, expected)

    def test_sony_request_matches_published_get_layout(self):
        with patch.object(sony.secrets, 'randbelow', return_value=0x1232):
            packet, tid = sony.request()
        self.assertEqual(tid, 0x1234)
        self.assertEqual(packet, bytes.fromhex('020c0100fc0896c34104013412e5')+bytes(50))
        with patch.object(sony.secrets, 'randbelow', side_effect=[0, 65533]):
            self.assertEqual([sony.request()[1] for _ in range(2)], [2, 65535])

    def test_sony_percent_charging_checksum_and_transaction(self):
        for level in (0, 73, 100):
            for charging in (0, 1):
                raw = sony_reply(level=level, charging=charging)
                self.assertEqual(sony.decode(raw, 0x1234),
                                 (level, 'charging' if charging else 'discharging'))
        for index, value in ((0, 0), (1, 13), (1, 63), (2, 1), (3, 0), (5, 1),
                             (6, 0), (8, 0x41), (9, 0x21), (10, 0xA0), (11, 1),
                             (14, 255), (15, 0)):
            raw = bytearray(sony_reply()); raw[index] = value
            # Keep valid checksum for semantic errors, independently testing fields.
            if index != 15: raw[15] = sum(raw[5:15]) & 255
            self.assertIsNone(sony.decode(raw, 0x1234), (index, value))
        self.assertIsNone(sony.decode(sony_reply()[:16], 0x1234))
        self.assertIsNone(sony.decode(sony_reply(), 0x1235))

    def test_sony_query_skips_unsolicited_and_echo(self):
        for pid in (0x0EBF, 0x0FA8):
            with patch.object(sony.secrets, 'randbelow', return_value=0x1232):
                packet, tid = sony.request()
                dev = Mock(); dev.read_input.side_effect = [packet, sony_reply(tid=1), sony_reply(tid)]
                result = rx.query(dev, iface(0x054C, pid))
            self.assertEqual(result.level, 73)
            dev.write_output.assert_called_once_with(packet, use_hidd=False)
            dev.set_feature_report.assert_not_called()


if __name__ == '__main__':
    unittest.main()
