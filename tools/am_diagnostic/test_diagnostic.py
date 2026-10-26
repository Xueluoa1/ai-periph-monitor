"""Offline protocol tests. No hardware API is imported or called."""
import unittest
import ctypes
import sys
from types import SimpleNamespace
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch, MagicMock

import am_diagnostic as m


def dpi_block():
    data = bytearray(64)
    data[0:4] = bytes((0xD4, 1, 1, 3))
    for i in range(8):
        data[8+i*2:10+i*2] = (400*(i+1)).to_bytes(2, 'little')
        data[24+i*2:26+i*2] = (800*(i+1)).to_bytes(2, 'little')
        data[40+i*3:43+i*3] = bytes((i, 20, 30))
    data[7] = 255 - (sum(data[:7]) & 255)
    return bytes(data)


def race_payload():
    return bytes((0, 3, 1)) + b''.join((400*(i+1)).to_bytes(2, 'little') for i in range(8)) + \
        b''.join((800*(i+1)).to_bytes(2, 'little') for i in range(8))


def race_reply(payload, command=0x30C5, type_=0x5B):
    return bytes((5, type_)) + (len(payload)+2).to_bytes(2, 'little') + command.to_bytes(2, 'little') + payload


def feature_fragment(data, target=0):
    return bytes((20, len(data), target)) + data


class ProtocolTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'win32', 'Windows ABI')
    def test_native_caps_sizes_and_offsets(self):
        import hid_win as hw
        self.assertEqual(ctypes.sizeof(hw.HIDP_BUTTON_CAPS), 72)
        self.assertEqual(hw.HIDP_BUTTON_CAPS.ReportCount.offset, 16)
        self.assertEqual(hw.HIDP_BUTTON_CAPS.u.offset, 56)
        self.assertEqual(ctypes.sizeof(hw.HIDP_VALUE_CAPS), 72)

    def test_input_handle_ready_before_query(self):
        calls = []
        dev = MagicMock()
        dev._open_reader.side_effect = lambda: calls.append('open') or True
        dev.drain.side_effect = lambda: calls.append('drain')
        reader = m.Reader(dev, SimpleNamespace(output_len=62), {})
        reader.send = lambda packet, feature: calls.append('send')
        frame = race_reply(race_payload())
        reader.receive = lambda feature, rid: bytes((7, len(frame), 0)) + frame
        with patch.object(m, 'emit'), patch.object(m.time, 'sleep'):
            self.assertEqual(reader.race(0x30C5, 0, False), race_payload())
        self.assertEqual(calls, ['open', 'drain', 'send'])

    def test_input_open_failure_prevents_send(self):
        dev = MagicMock()
        dev._open_reader.return_value = False
        reader = m.Reader(dev, SimpleNamespace(output_len=62), {})
        reader.send = MagicMock()
        with patch.object(m, 'emit'), self.assertRaises(OSError):
            reader.race(0x30C5, 0, False)
        reader.send.assert_not_called()

    def test_reserved_interface_never_opens_capability_reader(self):
        d = SimpleNamespace(vid=0x0E8D, pid=0x0880, reserved=True, usage_page=1, error='')
        hw = MagicMock()
        with patch.object(m, 'metadata', return_value={}), patch.object(m, 'emit'):
            m.probe(d, hw)
        hw.HidDevice.assert_not_called()

    def test_receiver_output_tries_default_route_first(self):
        d = SimpleNamespace(vid=0x0E8D, pid=0x0703, reserved=False, usage_page=0xFF13,
                            error='', path='fake', product='AM Infinity .97', feature_len=0, output_len=62)
        hw = MagicMock()
        queries = []
        def query(command, target, feature):
            queries.append((command, target))
            if target == 0:
                raise TimeoutError('offline default route')
            return race_payload() if command == 0x30C5 else b'\0\1'
        with patch.object(m, 'metadata', return_value={}), patch.object(m, 'emit'), \
             patch.object(m, 'report_ids', side_effect=[[], [6], [7]]), \
             patch.object(m.Reader, 'race', side_effect=query):
            m.probe(d, hw)
        self.assertEqual(queries[:4], [(0x30C9, 0), (0x30C5, 0), (0x30C9, 128), (0x30C5, 128)])

    def test_race_read_packet(self):
        packet = m.race_report(0x30C5, 0x80, 20, 65)
        self.assertEqual(packet[:9], bytes.fromhex('14 06 80 05 5a 02 00 c5 30'))
        self.assertEqual(len(packet), 65)

    def test_all_race_setters_rejected(self):
        for command in (0x30C4, 0x30C6, 0x30C7, 0x30C8, 0x30CE, 0x2CB2):
            with self.subTest(command=command), self.assertRaises(ValueError):
                m.race_report(command, 0, 20, 65)

    def test_am30_setters_and_firmware_rejected(self):
        for command in (0x54, 0x53, 0x05, 0x02, 0x50, 0x61, 0x7F, 0xF8, 0x40):
            with self.subTest(command=command), self.assertRaises(ValueError):
                m.am30_request(command)

    def test_transport_allowlist(self):
        for command, argument in ((0xF6, 9), (0xFE, 32), (0x54, None), (0xF8, None)):
            with self.subTest(command=command), self.assertRaises(ValueError):
                m.am30_transport(command, argument)

    def test_am30_profile_checksum(self):
        request = m.am30_request(0xD4, 2)
        self.assertEqual(request[:2], b'\xd4\x02')
        self.assertEqual(len(request), 64)
        self.assertEqual(sum(request[:8]) & 255, 255)
        with self.assertRaises(ValueError):
            m.am30_request(0xD4, 8)

    def test_am30_decode(self):
        state = m.decode_am30_dpi(dpi_block())
        self.assertEqual((state['enabled_count'], state['active_stage']), (3, 2))
        self.assertTrue(state['header_checksum_ok'])
        self.assertEqual(state['stages'][1]['y'], 1600)
        self.assertEqual(state['stages'][1]['color'], '#01141e')

    def test_malformed_am30_rejected(self):
        good = dpi_block()
        for data in (good[:32], bytes(64), b'\x54'+good[1:], good[:2]+b'\x08'+good[3:],
                     good[:3]+b'\x00'+good[4:], good[:8]+b'\0\0'+good[10:]):
            with self.subTest(data=data[:10]), self.assertRaises(ValueError):
                m.decode_am30_dpi(data)

    def test_race_decode_fixed_slots(self):
        state = m.decode_race_dpi(race_payload())
        self.assertEqual(state['enabled_count'], 3)
        self.assertEqual(len(state['stages']), 8)
        self.assertEqual(state['stages'][2]['y'], 2400)

    def test_race_decode_variable_slots(self):
        data = bytes((0, 2, 0)) + bytes.fromhex('90 01 20 03 58 02 b0 04')
        state = m.decode_race_dpi(data)
        self.assertEqual(state['stages'][1]['y'], 1200)

    def test_malformed_race_rejected(self):
        for data in (b'', bytes((1, 2, 0)), bytes((0, 9, 0)),
                     bytes((0, 1, 1))+bytes(8), bytes((0, 2, 0))+bytes(8)):
            with self.subTest(data=data), self.assertRaises(ValueError):
                m.decode_race_dpi(data)

    def test_multifragment_response(self):
        frame = race_reply(race_payload() + bytes(24))
        assembler = m.RaceAssembler()
        self.assertEqual(assembler.feed(feature_fragment(frame[:59]), 20, 0), [])
        self.assertEqual(assembler.feed(feature_fragment(frame[59:]), 20, 0), [(0x30C5, race_payload()+bytes(24))])

    def test_stale_report_echo_and_wrong_target(self):
        assembler = m.RaceAssembler()
        raw = feature_fragment(race_reply(race_payload()))
        self.assertEqual(len(assembler.feed(raw, 20, 0)), 1)
        self.assertEqual(assembler.feed(raw, 20, 0), [])
        self.assertEqual(m.RaceAssembler().feed(feature_fragment(m.race_request(0x30C5)), 20, 0), [])
        self.assertEqual(m.RaceAssembler().feed(raw, 20, 0x80), [])

    def test_unknown_identity_has_no_adapter(self):
        self.assertNotIn((0x3151, 0x5006), m.MODELS)
        self.assertNotIn((0x0E8D, 0x0704), m.MODELS)
        self.assertNotIn((0x3151, 0x402B), m.MODELS)

    def test_receiver_read_flow(self):
        reader = m.Reader(None, None, None)
        calls = []
        ready = bytearray(64)
        ready[5] = 1
        response_ready = bytearray(64)
        response_ready[0] = 1
        responses = iter((bytes(ready), bytes(response_ready), dpi_block()))
        def send(packet, response=True):
            calls.append(packet)
            return next(responses) if response else None
        reader.am30_send = send
        with patch.object(m, 'emit'), patch.object(m.time, 'sleep'):
            self.assertEqual(reader.am30(0xD4, True, 1), dpi_block())
        self.assertEqual([p[0] for p in calls], [0xF6, 0xF7, 0xFE, 0xD4, 0xF7, 0xFC])
        self.assertEqual(calls[0][1], 5)
        self.assertEqual(calls[2][1], 64)
        self.assertNotIn(0x54, [p[0] for p in calls])

    def test_offline_receiver_cleanup(self):
        reader = m.Reader(None, None, None)
        calls = []
        offline = bytearray(64)
        offline[4] = 1
        def send(packet, response=True):
            calls.append(packet)
            return bytes(offline) if response else None
        reader.am30_send = send
        with patch.object(m, 'emit'), self.assertRaises(OSError):
            reader.am30(0xD4, True, 1)
        self.assertEqual(calls[-1][:2], b'\xf6\0')
        self.assertNotIn(0xD4, [p[0] for p in calls])

    def test_query_error_is_logged_without_success(self):
        with patch.object(m, 'emit') as emit:
            result = m.attempt('dpi', lambda: m.decode_am30_dpi(bytes(64)))
        self.assertIsNone(result)
        self.assertEqual(emit.call_args.args[0], 'query_error')
        self.assertEqual(emit.call_args.kwargs['label'], 'dpi')

    def test_worker_timeout_leaves_diagnostic_record(self):
        child = MagicMock()
        child.wait.side_effect = [subprocess.TimeoutExpired('worker', 1), -1]
        with tempfile.TemporaryDirectory() as folder, patch.object(m.subprocess, 'Popen', return_value=child):
            rows = m.run_child('inventory', Path(folder)/'worker.jsonl', 1)
        child.kill.assert_called_once()
        self.assertTrue(any(row['event'] == 'worker_timeout' for row in rows))
        self.assertTrue(any(row['event'] == 'worker_exit' for row in rows))


if __name__ == '__main__':
    unittest.main()
