"""Protocol-shape fixtures, not claims of hardware validation."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import battery as b
import wireless_battery as w


def iface(vid=0x373B, pid=0x101A, page=0xFF02, usage=2, length=17):
    return SimpleNamespace(vid=vid, pid=pid, usage_page=page, usage=usage,
                           output_len=length, input_len=length, feature_len=length,
                           path='test-only', product='', manufacturer='',
                           vid_pid=f'{vid:04X}:{pid:04X}')


def compx(level=73, declared=4):
    r = bytearray.fromhex('08040000000449010ed800000000000000')
    r[5], r[6] = declared, level
    r[16] = (0x55 - sum(r[:16])) & 255
    return bytes(r)


def razer(command=0x80, level=128):
    r = bytearray(b._razer_packet(0x1F, 7, command))
    r[1], r[10] = 2, level
    r[89] = 0
    for value in r[3:89]:
        r[89] ^= value
    return bytes(r)


class WirelessTests(unittest.TestCase):
    def test_public_atk_capture_replies(self):
        # OpenMouse captures: X1 Pro Max receiver and A9 Plus NearLink receiver.
        for raw, expected in [('08040000000219000e46000000000000da',25),
                              ('08040000000214000df200000000000034',20),
                              ('0804000000026400108e00000000000045',100)]:
            self.assertEqual(w.decode_compx(bytes.fromhex(raw)), (expected,'discharging',None))

    def test_compx_integrity_and_boundaries(self):
        for level in (0, 1, 73, 100):
            self.assertEqual(w.decode_compx(compx(level)), (level, 'charging', 3800))
        with self.assertRaises(ValueError): w.decode_compx(compx(101))
        bad = bytearray(compx()); bad[6] ^= 1
        self.assertIsNone(w.decode_compx(bad))
        self.assertIsNone(w.decode_compx(compx()[:10]))
        self.assertIsNone(w.decode_compx(bytes.fromhex('0804000000000000000000000000000049')))

    def test_compx_does_not_read_undeclared_voltage(self):
        self.assertIsNone(w.decode_compx(compx(declared=2))[2])

    def test_page_report_id_status_and_zero(self):
        raw = bytes.fromhex('a100020200830100')
        for prefix in (b'', b'\0'):
            self.assertEqual(w.decode_page(prefix+raw), (0, 'charging', None))
        self.assertIsNone(w.decode_page(bytes.fromhex('a200020200830149')))
        self.assertIsNone(w.decode_page(bytes.fromhex('a100020200820149')))
        with self.assertRaises(ValueError): w.decode_page(bytes.fromhex('a100020200830165'))

    def test_mchose_inversion_identity_and_echo(self):
        data = bytearray(19); data[:2] = b'\x53\x52'; data[9:11] = b'\x49\x01'
        r = b'\x11\xf9' + bytes(x ^ 255 for x in data)
        self.assertEqual(w.decode_mchose(r, 0x5253), (73, 'charging', None))
        self.assertIsNone(w.decode_mchose(r, 0x3837))
        self.assertIsNone(w.decode_mchose(b'\x11\xf9'+b'\xff'*19, 0x5253))

    def test_mchose_long_report_fallback(self):
        d = iface(0x5253, 0x1020, 0xFF01, 1, 65)
        with patch.object(w, '_feature', side_effect=[OSError(), ((42, 'unknown', None), b'reply')]) as feature:
            self.assertEqual(w.query(Mock(), d, 'mchose')[0][0], 42)
        packets = [c.args[1] for c in feature.call_args_list]
        self.assertEqual([(p[0], len(p)) for p in packets], [(0x11, 21), (0x12, 65)])

    def test_g7_offset_and_echo(self):
        self.assertEqual(w.decode_g7(bytes.fromhex('aa30a50b0a0101012e00')), (46, 'discharging', None))
        self.assertIsNone(w.decode_g7(bytes.fromhex('005530a50b2e010101').ljust(65, b'\0')))

    def test_vaxee_percent_is_payload_not_length(self):
        for prefix in (b'', b'\x0e'):
            self.assertEqual(w.decode_vaxee(prefix+bytes.fromhex('a50b03010114'), 0x0B), 20)
        self.assertIsNone(w.decode_vaxee(bytes.fromhex('0ea50b010100'), 0x0B))
        self.assertIsNone(w.decode_vaxee(bytes.fromhex('0ea50b03010214'), 0x0B))
        with self.assertRaises(ValueError): w.decode_vaxee(bytes.fromhex('0ea50b03010115'), 0x0B)

    def test_vaxee_secondary_failure_keeps_battery(self):
        with patch.object(w, '_feature', side_effect=[(17,b'battery'), OSError()]):
            self.assertEqual(w.query(Mock(), iface(), 'vaxee'), ((85, 'unknown', None), b'battery'))

    def test_endgame_busy_sleep_and_unknown_voltage(self):
        raw = bytearray(64); raw[0:2] = b'\xa1\x01'; raw[16:19] = b'\x41\x72\x0f'
        self.assertEqual(w.decode_endgame(raw), (65, 'unknown', None))
        for status in (0,3,7,8):
            raw[1] = status
            self.assertIsNone(w.decode_endgame(raw))

    def test_beast4k_crc_and_distinct_route(self):
        self.assertEqual(w.crc16(b'123456789'), 0x4B37)
        req = w.beast4k_request()
        self.assertEqual((len(req), req[0], req[3:5]), (64,4,b'\x1a\x06'))
        r = bytearray(req); r[8] = 42
        self.assertEqual(w.decode_beast4k(r), (42,'unknown',None))
        self.assertEqual(w.route(iface(0x36A7,0xA887,0xFF1C,0x92,64)), 'beast4k')
        self.assertIsNone(w.route(iface(0x36A7,0xA887,0xFFFF,1,65)))

    def test_interface_routes_and_unknown_models(self):
        cases = [(iface(), 'compx'), (iface(0x3554), 'compx'),
                 (iface(0x3710,0x5406), 'compx'),
                 (iface(0x373E,0x1E,0xFFFF,1,65), 'page'),
                 (iface(0x36A7,0xA880,0xFFFF,1,65), 'page'),
                 (iface(0x2023,0xF015,0xFFFF,1,65), 'page'),
                 (iface(0x3837,0x100B,0xFF01,1,65), 'mchose'),
                 (iface(0xA8A5,0x2255,0xFF01,0x10,65), 'g7'),
                 (iface(0x3057,0x1001,0xFF05,1,64), 'vaxee'),
                 (iface(0x3367,0x1970,0xFF01,2,1041), 'endgame')]
        for d, expected in cases:
            with self.subTest(expected=expected, vid=d.vid):
                self.assertEqual(w.route(d),expected)
                self.assertTrue(b._is_candidate_interface(d,b.all_protocols()))
        for d in (iface(page=0xFF05), iface(0x373E,0xFFFF,0xFFFF,1,65),
                  iface(0x342D,0xA880,0xFFFF,1,65), iface(0x24AE,0xFFFF)):
            self.assertTrue(w.claims(d))
            self.assertFalse(b._is_candidate_interface(d,b.all_protocols()))

    def test_known_failure_never_falls_back_to_guessed_protocol(self):
        d = iface()
        with patch.object(w,'read',return_value=b.BatteryResult(ok=False)), patch.object(b,'strategy_vendor_protocol') as fallback:
            self.assertFalse(b._read_battery_unlocked(d,0)[0].ok)
            fallback.assert_not_called()

    def test_input_reader_starts_before_write_and_checks_command(self):
        dev = Mock(); dev.read_input.side_effect = [b'not-a-battery-report',compx()]
        result = w.query(dev,iface(),'compx')
        self.assertEqual(result[0][0],73)
        self.assertEqual([c[0] for c in dev.mock_calls][:2],['drain','write_output'])
        packet = dev.write_output.call_args.args[0]
        self.assertEqual((len(packet),sum(packet)&255),(17,0x55))

    def test_page_windows_packet_has_report_id_and_body(self):
        with patch.object(w,'_feature') as feature:
            w.query(Mock(),iface(0x373E,0x1E,0xFFFF,1,65),'page')
        packet=feature.call_args.args[1]
        self.assertEqual(len(packet),65)
        self.assertEqual(packet[:7],bytes.fromhex('00000002020083'))

    def test_hidpp_errors_and_feature_index(self):
        self.assertEqual(b._hidpp_error(bytes.fromhex('1101ff00010900')),9)
        self.assertIsNone(b._hidpp_error(bytes.fromhex('11010381010000')))
        with patch.object(b,'_hidpp_call',return_value=bytes.fromhex('11010001050f09')):
            self.assertEqual(b._hidpp_feature_index(Mock(),0x11,1,0x1004),5)
        with patch.object(b,'_hidpp_call',return_value=bytes.fromhex('11010001000f09')):
            self.assertIsNone(b._hidpp_feature_index(Mock(),0x11,1,0x1004))

    def test_hidpp_rejects_other_client_and_returns_matching_error(self):
        dev=Mock();dev.read_input.side_effect=[bytes.fromhex('11010212550000'),bytes.fromhex('11010211550000')]
        self.assertEqual(b._hidpp_call(dev,0x11,1,2,1,[])[3],0x11)
        self.assertEqual(dev.mock_calls[0][0],'drain')
        dev.read_input.side_effect=[bytes.fromhex('10018f02110900')]
        self.assertEqual(b._hidpp_error(b._hidpp_call(dev,0x11,1,2,1,[])),9)

    def test_logitech_unified_function_and_status(self):
        with patch.object(b,'_hidpp_feature_index',side_effect=[None,5]), patch.object(b,'_hidpp_call',return_value=bytes.fromhex('1101051149000100')) as request, patch.object(b,'_hidpp_get_device_name',return_value='Test'):
            result=b._logitech_probe_index(Mock(),iface(),0x11,1)
        self.assertEqual((result.level,result.status),(73,'charging'))
        self.assertEqual(request.call_args.args[4],1)

    def test_logitech_zero_does_not_invent_eighty_percent(self):
        with patch.object(b,'_hidpp_feature_index',return_value=5), patch.object(b,'_hidpp_call',return_value=bytes.fromhex('1101050100000100')), patch.object(b,'_hidpp_get_device_name',return_value=''):
            self.assertEqual(b._logitech_probe_index(Mock(),iface(),0x11,1).level,0)

    def test_logitech_voltage_is_big_endian(self):
        with patch.object(b,'_hidpp_feature_index',side_effect=[None,None,5]), patch.object(b,'_hidpp_call',return_value=bytes.fromhex('110105010ed880')), patch.object(b,'_hidpp_get_device_name',return_value=''):
            result=b._logitech_probe_index(Mock(),iface(),0x11,1)
        self.assertEqual(result.voltage_mv,3800)
        self.assertIsNone(result.level)

    def test_razer_requires_full_matching_crc_reply(self):
        r=razer(level=0)
        self.assertTrue(b._valid_razer_reply(r,0x1F,0x80))
        self.assertFalse(b._valid_razer_reply(r[:11],0x1F,0x80))
        self.assertFalse(b._valid_razer_reply(r,0x3F,0x80))
        self.assertFalse(b._valid_razer_reply(r,0x1F,0x84))
        broken=bytearray(r);broken[10]=100
        self.assertFalse(b._valid_razer_reply(broken,0x1F,0x80))


if __name__ == '__main__':
    unittest.main()
