import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import delux_m618xsd as m
import peripheral_protocols as catalog
import battery


def iface(**changes):
    values = dict(vid=0x320F, pid=0x221D, usage_page=0xFF1C, usage=0x92,
                  input_len=64, output_len=64, feature_len=0, path='fixture',
                  product='Wireless Gaming Mouse', manufacturer='')
    values.update(changes)
    return SimpleNamespace(**values)


def reply(packet, payload):
    raw = bytearray(packet)
    raw[8:8+len(payload)] = payload
    raw[1:3] = sum(raw[3:]).to_bytes(2, 'little')
    return bytes(raw)


class M618Tests(unittest.TestCase):
    def test_exact_route_and_sibling_exclusion(self):
        self.assertTrue(catalog.matches_interface(iface()))
        self.assertIn('M618XSD', catalog.profile_for(iface()).model)
        for changes in (dict(pid=0x706E), dict(usage_page=1, usage=0),
                        dict(input_len=33), dict(output_len=65), dict(feature_len=64)):
            d = iface(**changes)
            self.assertFalse(m.matches(d))
            dev = Mock()
            with self.assertRaises(ValueError):m.read(dev, d)
            dev.write_output.assert_not_called()

    def test_wire_frames_and_online_then_battery(self):
        self.assertEqual(m.ONLINE_QUERY[:8].hex(), '04aa00aa00000000')
        self.assertEqual(m.BATTERY_QUERY[:8].hex(), '0420001a06000000')
        online = reply(m.ONLINE_QUERY, b'\xff')
        raw = reply(m.BATTERY_QUERY, bytes((67, 2, 0, 0, 0, 0)))
        dev = Mock();dev.read_input.side_effect = [online, raw]
        parsed, received = catalog._active_read(dev, iface(), 'delux_m618xsd')
        self.assertEqual(parsed, (67, 'discharging', m.MODEL))
        self.assertEqual(received, raw)
        self.assertEqual([c.args[0] for c in dev.write_output.call_args_list],
                         [m.ONLINE_QUERY, m.BATTERY_QUERY])
        dev.set_feature_report.assert_not_called()

    def test_offline_and_failed_write_never_publish_battery(self):
        raw = bytearray(reply(m.ONLINE_QUERY, b'\0'));raw[1] = 0
        dev = Mock();dev.read_input.return_value = bytes(raw)
        self.assertIsNone(m.read(dev, iface())[0])
        self.assertEqual(dev.write_output.call_count, 1)
        dev = Mock();dev.write_output.return_value = False
        self.assertIsNone(m.read(dev, iface())[0])
        dev.read_input.assert_not_called()

    def test_decoder_boundaries_and_rejections(self):
        for percent in (0, 50, 100):
            for status, expected in ((0, 'unknown'), (1, 'charging'), (2, 'discharging')):
                r = reply(m.BATTERY_QUERY, bytes((percent, status, 1, 0, 0, 0)))
                self.assertEqual(m.decode(r), (percent, expected, m.MODEL))
        for raw in (b'', bytes(64), m.BATTERY_QUERY, reply(m.ONLINE_QUERY, b'\xff')):
            self.assertIsNone(m.decode(raw))
        for index, value in ((0, 5), (3, 5), (4, 7), (5, 1), (7, 254), (7, 255), (8, 101), (9, 3)):
            raw = bytearray(reply(m.BATTERY_QUERY, bytes((50, 2))))
            raw[index] = value
            self.assertIsNone(m.decode(raw), (index, value))

    def test_no_response_is_unknown_not_zero(self):
        dev = Mock();dev.read_input.return_value = None
        with patch.object(m.time, 'monotonic', side_effect=[0, 0, 2]):
            self.assertEqual(m.read(dev, iface()), (None, b''))

    def test_monitor_result_uses_confirmed_name(self):
        with patch.object(catalog, 'HidDevice') as hid, patch.object(m, 'read', return_value=((72, 'charging', m.MODEL), b'fixture')):
            result = catalog.read(iface(), battery.BatteryResult)
        self.assertTrue(result.ok)
        self.assertEqual(result.level, 72)
        self.assertEqual(result.extra['model_name'], m.MODEL)


if __name__ == '__main__':unittest.main()
