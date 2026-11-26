"""33433a57 descriptor fixtures plus simulated replies to the documented queries."""
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import Mock, patch

import battery
import eweadn_s9 as s9
import mchose_glw as glw
import peripheral_protocols as protocols


def interface(vid, pid, page, usage, sizes, product):
    return SimpleNamespace(vid=vid, pid=pid, usage_page=page, usage=usage,
                           input_len=sizes[0], output_len=sizes[1], feature_len=sizes[2],
                           product=product, path='fixture')


def s9_reply(sequence, block):
    frame = bytearray(64)
    frame[:6] = bytes((3, 0x50, 0, 10, sequence, block))
    return frame


def glw_reply(charge_byte, size=56):
    frame = bytearray(65)
    frame[:9] = bytes((0, 0xaa, 5, 0, 0, size, 0, 0, 0))
    frame[11], frame[41] = 0x27, charge_byte
    frame[4] = sum(frame[5:9 + size]) & 255
    return bytes(frame)


class NewReceiverTests(TestCase):
    def setUp(self):
        self.mouse = interface(0x372e, 0x1026, 0xff00, 1, (0, 0, 64), 'EWEADN S9')
        self.keyboard = interface(0x3837, 0x3033, 1, 0, (65, 65, 0), 'MCHOSE G75 V2 2.4G')

    def test_routes_match_logged_collections_only(self):
        self.assertTrue(battery._is_candidate_interface(self.mouse, []))
        self.assertTrue(battery._is_candidate_interface(self.keyboard, []))
        self.mouse.usage_page = 0xff06
        self.keyboard.usage_page, self.keyboard.usage = 0xff31, 0x74
        self.assertFalse(protocols.matches_interface(self.mouse))
        self.assertFalse(protocols.matches_interface(self.keyboard))
        self.keyboard.product = 'MCHOSE K87S 2.4G'
        self.assertIsNone(protocols.profile_for(self.keyboard))

    def test_keyboard_filters_echo_and_returns_valid_frame(self):
        dev = Mock()
        dev.value_caps.return_value = [SimpleNamespace(ReportID=0)]
        dev.read_input.side_effect = [glw.REQUEST, glw_reply(0x80 | 67)]
        parsed, _ = protocols._active_read(dev, self.keyboard, 'mchose_glw')
        self.assertEqual(parsed, (67, 'charging', glw.NAME))
        dev.write_output.assert_called_once_with(
            bytes.fromhex('00 55 05 00 38 38 00 00 00').ljust(65, b'\0'), use_hidd=False)
        self.assertIsNone(glw.decode(glw_reply(127)))
        self.assertIsNone(glw.decode(glw_reply(50)[:-1]))
        self.assertEqual(glw.decode(glw_reply(0))[0], 0)
        self.assertEqual(glw.decode(glw_reply(52, 33))[0], 52)
        self.assertIsNone(glw.decode(glw_reply(52, 32)))
        for index in (0, 1, 2, 3, 4, 6, 7):
            raw = bytearray(glw_reply(35))
            raw[index] ^= 1
            self.assertIsNone(glw.decode(raw), index)
        dev.reset_mock()
        dev.value_caps.return_value = [SimpleNamespace(ReportID=1)]
        with self.assertRaises(ValueError):
            glw.read(dev, self.keyboard)
        dev.write_output.assert_not_called()

    @patch.object(s9.time, 'sleep')
    def test_mouse_requests_online_model_and_basics(self, _):
        dev = Mock()
        dev.value_caps.return_value = [SimpleNamespace(ReportID=3)]
        online, identity, basics = s9_reply(1, 0), s9_reply(2, 0), s9_reply(2, 1)
        online[6] = 1
        identity[8:14] = (9895604650000).to_bytes(6, 'big')
        basics[6] = 0x80 | 46
        idle = bytes.fromhex('03 00 02').ljust(64, b'\0')  # actual unsolicited GetFeature snapshot
        dev.get_feature_report.side_effect = [idle, online, identity, identity, basics]
        parsed, _ = protocols._active_read(dev, self.mouse, 'eweadn_s9')
        self.assertEqual(parsed, (46, 'charging', '前行者 EWEADN S9 Ultra 鼠标'))
        requests = [c.args[0] for c in dev.set_feature_report.call_args_list]
        self.assertEqual([r[:7].hex() for r in requests],
                         ['03985000014700', '03215000024f80', '03225000024f81'])
        self.assertTrue(all(len(r) == 64 and not any(r[7:]) for r in requests))

    @patch.object(s9.time, 'sleep')
    def test_offline_unknown_model_and_out_of_range_are_unavailable(self, _):
        dev = Mock()
        dev.value_caps.return_value = [SimpleNamespace(ReportID=3)]
        online = s9_reply(1, 0)
        dev.get_feature_report.return_value = online
        with self.assertRaisesRegex(ValueError, '未在线'):
            s9.read(dev, self.mouse)
        self.assertEqual(dev.set_feature_report.call_count, 1)
        online[6] = 1
        identity = s9_reply(2, 0)
        dev.get_feature_report.side_effect = [online, identity]
        with self.assertRaisesRegex(ValueError, '未知型号'):
            s9.read(dev, self.mouse)
        identity[8:14] = (9895604649998).to_bytes(6, 'big')
        invalid = s9_reply(2, 1)
        invalid[6] = 0xff
        dev.get_feature_report.side_effect = [online, identity, invalid]
        self.assertIsNone(s9.read(dev, self.mouse)[0])
        dev.value_caps.return_value = [SimpleNamespace(ReportID=9)]
        with self.assertRaisesRegex(ValueError, '报告 ID'):
            s9.read(dev, self.mouse)


if __name__ == '__main__':
    main()
