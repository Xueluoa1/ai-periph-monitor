"""16fd7413 interface fixture and synthetic M7 Pro receiver responses."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import battery
import machenike_m7 as m7
import peripheral_protocols as protocols


class M7Tests(TestCase):
    def setUp(self):
        self.iface = SimpleNamespace(vid=0x248A, pid=0x5B2F, usage_page=0xFF00, usage=1,
                                     input_len=0, output_len=0, feature_len=33)
        self.dev = Mock()
        self.dev.value_caps.return_value = [SimpleNamespace(ReportID=12)]
        self.raw = bytearray(33)
        self.raw[:7] = bytes.fromhex('0c 01 20 00 01 00 10')
        self.raw[8:12] = b'M263'
        self.raw[15] = 0x95
        self.raw[18:20] = bytes((72, 1))

    @patch.object(m7.time, 'sleep')
    def test_detect_and_read_after_empty_feature(self, sleep):
        self.assertTrue(battery._is_candidate_interface(self.iface, []))
        self.dev.get_feature_report.side_effect = [bytes(33), self.raw]
        parsed, _ = protocols._active_read(self.dev, self.iface, 'machenike_m7')
        self.assertEqual(parsed, (72, 'charging', '机械师 M7 Pro 兼容鼠标（M263）'))
        first, second = [c.args[0] for c in self.dev.set_feature_report.call_args_list]
        self.assertEqual(first[:4], bytes.fromhex('0c012000'))
        self.assertEqual(second[:4], first[:4])
        self.assertEqual(second[4], (first[4] + 1) & 255)
        self.assertEqual(first[5:], bytes(28))
        self.assertEqual(second[5:], bytes(28))
        self.dev.get_feature_report.assert_called_with(12, 33)

    def test_invalid_frames_and_other_model(self):
        for index, value in ((0, 5), (2, 0), (3, 1), (6, 0), (8, 0xFF), (15, 0), (18, 101), (19, 3)):
            raw = self.raw.copy()
            raw[index] = value
            self.assertIsNone(m7.decode(raw))
        self.assertIsNone(m7.decode(self.raw[:-1]))
        self.raw[15], self.raw[19] = 4, 2
        self.raw[8:12] = b'M802'
        self.assertEqual(m7.decode(self.raw), (100, 'full', '2.4G 无线鼠标（型号代码 M802）'))
        self.raw[18:20] = bytes((0, 0))
        self.assertEqual(m7.decode(self.raw)[:2], (0, 'discharging'))

    def test_wrong_collection_or_cable_does_not_send(self):
        self.dev.value_caps.return_value = [SimpleNamespace(ReportID=5)]
        with self.assertRaises(ValueError):
            m7.read(self.dev, self.iface)
        self.iface.usage_page, self.iface.feature_len = 0xFFEF, 64
        self.assertFalse(protocols.matches_interface(self.iface))
        self.iface.pid = 0x5B2E
        self.assertIsNone(protocols.profile_for(self.iface))
        self.dev.set_feature_report.assert_not_called()
