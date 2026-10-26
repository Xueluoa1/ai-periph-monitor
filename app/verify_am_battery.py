"""AM .97 battery regression using user-provided real Feature reports; no hardware IO."""
import ctypes
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import am_battery as am
import battery


# Captured 2026-10-21 after the user updated firmware. IDs and bytes unmodified.
CAPTURES = (
    (0x0703, '140d80055b0900cf30000051ff010901', 81, 'discharging'),
    (0x0703, '140d80055b0900cf3000004fff014501', 79, 'discharging'),
    (0x0880, '140d00055b0900cf3000014fff013b01', 79, 'charging'),
    (0x0880, '140d00055b0900cf30000151ff014f01', 81, 'charging'),
    (0x0703, '140d80055b0900cf30000051ff014f01', 81, 'discharging'),
)


def iface(pid=0x0703, page=0xFF13, feature_len=62):
    return SimpleNamespace(vid=0x0E8D, pid=pid, usage_page=page, usage=1,
                           input_len=62, output_len=62, feature_len=feature_len,
                           path=f'am97-{pid}-{page}', product='AM Infinity .97',
                           manufacturer='AngryMiao', vid_pid=f'0E8D:{pid:04X}')


def response(payload, command=0x30CF, target=128, kind=0x5B):
    frame = bytes((5, kind)) + (len(payload)+2).to_bytes(2, 'little') + command.to_bytes(2, 'little') + payload
    return (bytes((20, len(frame), target)) + frame).ljust(62, b'\0')


class AmBatteryTests(unittest.TestCase):
    def fake_device(self, stack, raw):
        dev = MagicMock()
        dev.__enter__.return_value = dev
        dev.write_access = True
        dev.get_feature_report.return_value = raw
        stack.enter_context(patch.object(am.hw, 'HidDevice', return_value=dev))
        stack.enter_context(patch.object(am, 'feature_report_ids', return_value={20}))
        stack.enter_context(patch.object(am.time, 'sleep'))
        return dev

    def test_native_abi(self):
        self.assertEqual(ctypes.sizeof(am.hw.HIDP_BUTTON_CAPS), 72)
        self.assertEqual(am.hw.HIDP_BUTTON_CAPS.u.offset, 56)

    def test_real_usb_and_receiver_reports(self):
        for pid, hexdata, level, status in CAPTURES:
            with self.subTest(pid=pid, level=level), ExitStack() as stack:
                raw = bytes.fromhex(hexdata).ljust(62, b'\0')
                dev = self.fake_device(stack, raw)
                result = am.read(iface(pid), battery.BatteryResult)
                self.assertTrue(result.ok, result.detail)
                self.assertEqual((result.level, result.status), (level, status))
                sent = dev.set_feature_report.call_args.args[0]
                self.assertEqual(sent[:9], bytes.fromhex('1406') + bytes((am.IDENTITIES[(0x0E8D,pid)],)) + bytes.fromhex('055a0200cf30'))
                self.assertEqual(len(sent), 62)
                dev.write_output.assert_not_called()
                dev.read_input.assert_not_called()
                dev.set_feature_report.assert_called_once()

    def test_zero_and_full_are_valid(self):
        for value in (0, 100):
            self.assertEqual(am.decode_battery(bytes((0,0,value,255,1)))[0], value)

    def test_status_absence_and_bad_percent_rejected(self):
        for payload in (b'', bytes((1,0,50,255,1)), bytes((0,0,101,255,1)),
                        bytes((0,2,50,255,1)), bytes((0,0,50,255,0))):
            with self.subTest(payload=payload), self.assertRaises((ValueError, OSError)):
                am.decode_battery(payload)

    def test_other_frames_echo_and_wrong_target_not_battery(self):
        for raw in (response(b'\0\0', command=0x30C9),
                    response(bytes((0,0,81,255,1)), target=0),
                    am.battery_request(128,62), bytes(62)):
            self.assertIsNone(am.BatteryResponse(128).feed(raw))

    def test_notification_before_battery(self):
        notification = bytes.fromhex('055d0400b12c0001')
        frame = response(bytes((0,0,81,255,1)))[3:14]
        raw = bytes((20,len(notification+frame),128)) + notification + frame
        self.assertEqual(am.BatteryResponse(128).feed(raw), (81,'discharging'))

    def test_fragmented_response(self):
        frame = response(bytes((0,1,79,255,1)))[3:14]
        parser = am.BatteryResponse(128)
        self.assertIsNone(parser.feed(bytes((20,8,128))+frame[:8]))
        self.assertEqual(parser.feed(bytes((20,3,128))+frame[8:]), (79,'charging'))

    def test_malformed_lengths(self):
        for raw in (bytes((20,59,128,5)), bytes((20,60,128))+bytes(60),
                    bytes((20,6,128,5,91,255,255,207,48))):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                am.BatteryResponse(128).feed(raw)

    def test_old_firmware_and_wrong_interfaces_never_send(self):
        with patch.object(am.hw,'HidDevice') as open_device:
            for d in (iface(feature_len=0), iface(page=0xFF15), iface(pid=0x0881)):
                self.assertFalse(am.read(d,battery.BatteryResult).ok)
            open_device.assert_not_called()

    def test_report_id_required(self):
        with ExitStack() as stack:
            dev = self.fake_device(stack,b'')
            stack.enter_context(patch.object(am,'feature_report_ids',return_value={23}))
            self.assertFalse(am.read(iface(),battery.BatteryResult).ok)
            dev.set_feature_report.assert_not_called()

    def test_timeout_never_falls_through_to_guessing(self):
        with ExitStack() as stack:
            self.fake_device(stack,bytes(62))
            stack.enter_context(patch.object(am.time,'monotonic',side_effect=[0,0,1]))
            legacy = stack.enter_context(patch.object(battery,'strategy_angry_miao'))
            standard = stack.enter_context(patch.object(battery,'strategy_standard_battery_usage'))
            results = battery._read_battery_unlocked(iface(),listen_seconds=0)
            self.assertEqual(len(results),1)
            self.assertFalse(results[0].ok)
            legacy.assert_not_called();standard.assert_not_called()

    def test_bad_response_returns_unknown_instead_of_guess(self):
        with ExitStack() as stack:
            self.fake_device(stack,response(bytes((0,0,255,81,1))))
            result = am.read(iface(),battery.BatteryResult)
            self.assertFalse(result.ok);self.assertIsNone(result.level)

    def test_discovery_and_home_tray_widget_share_result(self):
        import my_devices
        from tray_app import TrayApp
        from device_visibility import visible_readings
        with ExitStack() as stack:
            self.fake_device(stack,bytes.fromhex(CAPTURES[-1][1]).ljust(62,b'\0'))
            stack.enter_context(patch.object(battery.hw,'enumerate_hid',return_value=[iface(),iface(page=0xFF14),iface(page=0xFF15)]))
            stack.enter_context(patch.object(battery,'_load_custom_models',return_value={}))
            readings = battery.discover_and_read_all()
            self.assertEqual(len(readings),1)
            row = readings[0]
            self.assertEqual((row.level,row.key,row.status),(81,'0E8D:0703:FF13','放电中'))
            self.assertEqual(row.title,am.MODEL_NAME)
            # The home page now consumes the shared snapshot. Isolate this
            # fixture from the user's persisted live-device readings.
            shared = my_devices.shared
            snapshot = shared.merge_snapshot(shared.empty_snapshot(), readings, 100)
            stack.enter_context(patch.object(shared,'collect',return_value=snapshot))
            self.assertEqual(my_devices.read_all()[0].level,81)
            tray=TrayApp.__new__(TrayApp);tray.cfg={'taskbar_show':True};tray.readings=readings
            self.assertEqual(tray._current_slots()[0][2],81)
            self.assertEqual(visible_readings(readings,{},'widget')[0].level,81)

    def test_discovery_reports_missing_firmware_once(self):
        with patch.object(battery.hw,'enumerate_hid',return_value=[iface(feature_len=0),iface(page=0xFF15)]), \
             patch.object(battery,'_load_custom_models',return_value={}):
            readings=battery.discover_and_read_all()
        self.assertEqual(len(readings),1)
        self.assertIsNone(readings[0].level)
        self.assertIn('固件',readings[0].error)

    def test_only_exact_device_and_channel_selected(self):
        self.assertTrue(battery._is_candidate_interface(iface(feature_len=0),[]))
        self.assertFalse(battery._is_candidate_interface(iface(page=0xFF15),[]))
        self.assertFalse(am.supports_device(iface(pid=0x0881)))


if __name__ == '__main__':
    unittest.main()
