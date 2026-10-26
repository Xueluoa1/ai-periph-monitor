"""Captured AM35 reads plus simulated writes; never changes physical hardware."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import am_dpi as am
import mouse_settings as ms


# Actual .97 USB/receiver DPI payload, from the user's v1.1 diagnostic logs.
DPI = bytes.fromhex('00080490012003b0044006800c00191027307590012003b0044006800c001910273075')
VALUES = (400, 800, 1200, 1600, 3200, 6400, 10000, 30000)
PROFILE_WITH_NOTIFICATION = bytes.fromhex(
    '141a80055d0e00b12c00060100ce001a3bed328001055b0400c9300000').ljust(62, b'\0')


def iface(pid=0x0703, path='am-test'):
    return SimpleNamespace(path=path, vid=0x0E8D, pid=pid, usage_page=0xFF13,
                           usage=1, feature_len=62, output_len=0, input_len=0,
                           product='AM', vid_pid=f'0E8D:{pid:04X}')


def frame(command, payload, response_type=0x5B):
    return bytes((5, response_type)) + (len(payload)+2).to_bytes(2, 'little') + command.to_bytes(2, 'little') + payload


def report(command, payload, target=0x80, response_type=0x5B):
    body = frame(command, payload, response_type)
    return (bytes((0x14, len(body), target)) + body).ljust(62, b'\0')


class FakeDevice:
    """Protocol-level simulator preserving hidden slots and RGB bytes."""
    write_access = True

    def __init__(self):
        self.config = am.decode(DPI, 0)
        self.sent = []
        self.reply = b''
        self.switches = []
        self.fail_command = None
        self.corrupt_other_stage = False

    def __enter__(self): return self
    def __exit__(self, *args): pass

    def set_feature_report(self, raw):
        self.sent.append(raw)
        cmd = int.from_bytes(raw[7:9], 'little')
        payload = raw[9:3+raw[1]]
        config = self.config
        if cmd == am.GET_PROFILE:
            result = bytes((0, config.profile))
        elif cmd == am.GET_DPI:
            result = bytes((0, config.count, config.active))
            for axis in (0, 1):
                result += b''.join(row[axis].to_bytes(2, 'little') for row in config.stages)
            result += config.colors
        elif cmd == am.SET_DPI:
            rows = list(config.stages)
            rows[payload[0]] = (int.from_bytes(payload[1:3], 'little'), int.from_bytes(payload[3:5], 'little'))
            if self.corrupt_other_stage: rows[7] = (50, 50)
            self.config = replace(config, stages=tuple(rows))
            result = b'\0'
        elif cmd == am.SET_ACTIVE:
            self.switches.append(payload[0])
            self.config = replace(config, active=payload[0])
            result = b'\0'
        else:
            raise AssertionError(f'Unexpected command {cmd:04X}')
        self.reply = report(cmd, result, raw[2], 0x5D if cmd == am.GET_DPI else 0x5B)
        if self.fail_command == cmd:
            # Simulate the device accepting a write before the host sees an error.
            raise TimeoutError('simulated lost ACK')

    def get_feature_report(self, *args): return self.reply


class AmDpiTests(unittest.TestCase):
    def setUp(self):
        self.iface = iface()
        self.mouse = ms.Mouse(self.iface.path, 'AM .97', self.iface.vid_pid, 'am35',
                              (self.iface.vid, self.iface.pid))
        self.device = FakeDevice()
        for p in (
            patch.object(ms.hw, 'enumerate_hid', return_value=[self.iface]),
            patch.object(ms.hw, 'HidDevice', return_value=self.device),
            patch.object(am.am, 'feature_report_ids', return_value={0x14}),
            patch.object(am.time, 'sleep'),
        ):
            p.start(); self.addCleanup(p.stop)

    def commands(self):
        return [int.from_bytes(p[7:9], 'little') for p in self.device.sent]

    def test_discovery_feature_only_and_connection_names(self):
        other = iface(0x0880, 'usb')
        with patch.object(ms.hw, 'enumerate_hid', return_value=[self.iface, other]):
            found = ms.discover()
        self.assertEqual([m.protocol for m in found], ['am35', 'am35'])
        self.assertIn('2.4G', found[0].name)
        self.assertIn('USB', found[1].name)
        self.assertEqual(self.device.sent, [])

    def test_discovery_wrong_collection_and_other_model_ignored(self):
        wrong = iface(); wrong.usage_page = 1
        r2 = iface(0x0881); r2.output_len = 64
        with patch.object(ms.hw, 'enumerate_hid', return_value=[wrong, r2]):
            self.assertEqual(ms.discover(), [])

    def test_old_firmware_visible_but_not_writable(self):
        self.iface.feature_len = 0
        found = ms.discover()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].protocol, '')
        with self.assertRaisesRegex(ValueError, '固件'): ms.read(found[0])
        self.assertEqual(self.device.sent, [])

    def test_captured_reads_both_targets(self):
        for pid, target in ((0x0703, 128), (0x0880, 0)):
            self.iface.pid = pid; self.mouse.identity = (0x0E8D, pid)
            state = ms.read(self.mouse)
            self.assertEqual(state.stages, [(v, v) for v in VALUES])
            self.assertEqual((state.active, state.profile), (4, 1))
            self.assertEqual(self.device.sent[-2], am.request(am.GET_DPI, b'', target, 62))
        self.assertTrue(all(c in (am.GET_DPI, am.GET_PROFILE) for c in self.commands()))

    def test_exact_write_reports_and_allowlist(self):
        self.assertEqual(am.request(am.SET_DPI, bytes.fromhex('01e803e803'), 128, 62),
                         bytes.fromhex('140b80055a0700c43001e803e803').ljust(62, b'\0'))
        self.assertEqual(am.request(am.SET_ACTIVE, b'\x04', 0, 62),
                         bytes.fromhex('140700055a0300c73004').ljust(62, b'\0'))
        for command, payload in ((0x30CA, b''), (am.SET_DPI, b'\0'*5), (am.SET_ACTIVE, b'\x08')):
            with self.assertRaises(ValueError): am.request(command, payload, 128, 62)

    def test_notifications_fragmentation_echo_and_wrong_target(self):
        parser = am.Response(128, am.GET_PROFILE)
        self.assertEqual(parser.feed(PROFILE_WITH_NOTIFICATION), b'\0\0')
        parser = am.Response(128, am.GET_DPI)
        self.assertIsNone(parser.feed(am.request(am.GET_DPI, b'', 128, 62)))
        self.assertIsNone(parser.feed(report(am.GET_DPI, DPI, target=0)))
        data = frame(am.GET_DPI, DPI, 0x5D)
        first = (bytes((20, 20, 128))+data[:20]).ljust(62, b'\0')
        self.assertIsNone(parser.feed(first))
        self.assertIsNone(parser.feed(first))
        self.assertEqual(parser.feed((bytes((20, len(data)-20, 128))+data[20:]).ljust(62, b'\0')), DPI)

    def test_invalid_responses_stop(self):
        for payload in (b'', b'\x01', b'\0\0\0', b'\0\x08\x08'+DPI[3:], DPI[:-1], b'\0\x08\x04'+bytes(32)):
            with self.assertRaises(ValueError): am.decode(payload, 0)
        for raw in (b'\x14', bytes((20, 60, 128))+bytes(60), report(am.GET_DPI, b'\x01')):
            with self.assertRaises(ValueError): am.Response(128, am.GET_DPI).feed(raw)

    def test_hidden_slots_and_colors_not_confused_with_enabled_count(self):
        raw = b'\0\x02\x01'+DPI[3:]+bytes(range(24))
        config = am.decode(raw, 2)
        self.assertEqual((config.count, config.active, len(config.stages)), (2, 1, 8))
        self.assertEqual(config.stages[1], (800, 800))
        self.assertEqual(config.colors, bytes(range(24)))
        compact = bytes.fromhex('00020190012003b0044006')
        self.assertEqual(am.decode(compact, 0).stages, ((400, 1200), (800, 1600)))

    def test_inactive_edit_preserves_other_slots_profile(self):
        self.device.config = replace(self.device.config, count=5)
        state = ms.read(self.mouse)
        result = ms.apply(state, 1, 1000, False)
        self.assertEqual(result.stages[1], (1000, 1000))
        self.assertEqual(result.active, 4)
        self.assertEqual(result.protocol_data.stages[5:], state.protocol_data.stages[5:])
        self.assertNotIn(am.SET_ACTIVE, self.commands())
        self.assertEqual(self.commands().count(am.SET_DPI), 1)

    def test_current_edit_reselects_current_stage(self):
        state = ms.read(self.mouse)
        result = ms.apply(state, 4, 3250, False)
        self.assertEqual(result.active, 4)
        self.assertEqual(self.device.switches, [4])
        self.assertEqual([c for c in self.commands() if c in (am.SET_DPI, am.SET_ACTIVE)], [am.SET_DPI, am.SET_ACTIVE])

    def test_switch_only_preserves_asymmetric_xy(self):
        rows = list(self.device.config.stages); rows[0] = (400, 800)
        self.device.config = replace(self.device.config, stages=tuple(rows))
        state = ms.read(self.mouse)
        result = ms.apply(state, 0, None, True)
        self.assertEqual(result.stages, state.stages)
        self.assertEqual(result.active, 0)
        self.assertNotIn(am.SET_DPI, self.commands())

    def test_no_change_sends_no_settings_write(self):
        state = ms.read(self.mouse)
        ms.apply(state, 0, 400, False)
        ms.apply(state, 4, None, True)
        self.assertNotIn(am.SET_DPI, self.commands())
        self.assertNotIn(am.SET_ACTIVE, self.commands())

    def test_invalid_and_stale_snapshot_never_write(self):
        state = ms.read(self.mouse)
        for value in (49, 801, 30050, True):
            with self.assertRaises(ValueError): ms.apply(state, 0, value, False)
        self.device.config = replace(self.device.config, profile=1)
        with self.assertRaisesRegex(ValueError, '已变化'): ms.apply(state, 0, 1000, False)
        self.assertNotIn(am.SET_DPI, self.commands())

    def test_reject_missing_report_and_no_access(self):
        with patch.object(am.am, 'feature_report_ids', return_value={6}):
            with self.assertRaisesRegex(ValueError, 'Feature'): ms.read(self.mouse)
        self.device.write_access = False
        with self.assertRaisesRegex(OSError, '访问'): ms.read(self.mouse)
        self.assertEqual(self.device.sent, [])

    def test_write_error_reads_back_without_retry(self):
        state = ms.read(self.mouse)
        self.device.fail_command = am.SET_DPI
        with self.assertRaisesRegex(RuntimeError, '读回档位 2 为 X 1000 / Y 1000'):
            ms.apply(state, 1, 1000, False)
        self.assertEqual(self.commands().count(am.SET_DPI), 1)
        self.assertEqual(self.commands()[-3:], [am.GET_PROFILE, am.GET_DPI, am.GET_PROFILE])

    def test_readback_detects_unrelated_slot_changed(self):
        state = ms.read(self.mouse)
        self.device.corrupt_other_stage = True
        with self.assertRaisesRegex(RuntimeError, '与预期不一致'): ms.apply(state, 0, 1000, False)

    def test_profile_change_during_read_fails(self):
        with patch.object(am, 'exchange', side_effect=[b'\0\0', DPI, b'\0\1']):
            with self.assertRaisesRegex(ValueError, '配置层已变化'): ms.read(self.mouse)

    def test_timeout_only_sends_one_request(self):
        with patch.object(am.time, 'monotonic', side_effect=[0, 0, 2]), \
             patch.object(self.device, 'get_feature_report', return_value=b''):
            with self.assertRaises(TimeoutError): am.exchange(self.device, self.iface, am.GET_DPI)
        self.assertEqual(self.commands(), [am.GET_DPI])

    def test_disconnected_or_changed_identity_never_sends(self):
        with patch.object(ms.hw, 'enumerate_hid', return_value=[]):
            with self.assertRaisesRegex(OSError, '断开'): ms.read(self.mouse)
        self.iface.pid = 0x0881
        with self.assertRaisesRegex(ValueError, '身份已变化'): ms.read(self.mouse)
        self.assertEqual(self.device.sent, [])

    def test_colors_survive_write_and_are_checked_on_readback(self):
        self.device.config = replace(self.device.config, count=2, active=0,
                                     stages=((400, 400), (800, 800)), colors=bytes(range(6)))
        state = ms.read(self.mouse)
        result = ms.apply(state, 1, 1000, False)
        self.assertEqual(result.protocol_data.colors, bytes(range(6)))
        writes = [r for r in self.device.sent if int.from_bytes(r[7:9], 'little') == am.SET_DPI]
        self.assertEqual(writes[0][1], 11)  # Five-byte payload, no color overwrite.

    def test_multiple_receivers_remain_separate(self):
        second = iface(path='second-am')
        with patch.object(ms.hw, 'enumerate_hid', return_value=[self.iface, second]):
            found = ms.discover()
        self.assertEqual([m.path for m in found], ['am-test', 'second-am'])


if __name__ == '__main__':
    unittest.main()
