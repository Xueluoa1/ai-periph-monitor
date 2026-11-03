"""Discovery regressions: real interfaces replaced by explicit fake transports."""
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import battery as b
import device_io
import device_visibility
import shared_readings
import my_devices
import xinput_battery


def iface(path='input', page=6, usage=1, vid=0x9999, pid=1, feature=0):
    return b.hw.HidInterface(path,vid,pid,0,page,usage,32,0,feature,'Test wireless','','',
                             num_input_value_caps=1)


def cap(page=6, usage=0x20):
    return SimpleNamespace(UsagePage=page, u=SimpleNamespace(NotRange=SimpleNamespace(Usage=usage)),
                           LogicalMax=100, ReportID=1)


class DiscoveryConsistencyTests(unittest.TestCase):
    def scan_context(self, devices, candidates=()):
        stack = ExitStack()
        stack.enter_context(patch.object(b.hw,'enumerate_hid',return_value=devices))
        stack.enter_context(patch.object(b,'_load_custom_models',return_value={}))
        stack.enter_context(patch.object(b,'all_protocols',return_value=[]))
        stack.enter_context(patch.object(b,'_is_candidate_interface',side_effect=lambda d,s:d.path in candidates))
        stack.enter_context(patch.object(b,'strategy_standard_battery_usage',return_value=None))
        stack.enter_context(patch.object(b.peripheral,'prune_listeners'))
        stack.enter_context(patch.object(xinput_battery,'readings',return_value=[]))
        return stack

    def test_input_only_standard_battery_reaches_home_and_respects_hidden(self):
        d = iface()
        with self.scan_context([d]), patch.object(b,'monitor_input_reports',
                return_value=(b.BatteryResult(True,level=42,source='Input battery'),[])) as listen:
            rows = b.discover_and_read_all()
        listen.assert_called_once_with(d,seconds=3.0,battery_only=True)
        self.assertEqual([(r.key,r.level) for r in rows],[('9999:0001:0006',42)])
        snapshot = shared_readings.merge_snapshot(shared_readings.empty_snapshot(),rows,1)
        home = my_devices.display_readings(shared_readings.readings_from(snapshot))
        self.assertEqual(home[0].level,42)
        self.assertEqual(device_visibility.managed_readings(home,{'hidden_devices':[home[0].key]}),[])

    def test_failed_first_collection_does_not_suppress_same_page_success(self):
        devices = [iface(path='a'),iface(path='b'),iface(path='c')]
        with self.scan_context(devices,('a','b','c')), patch.object(b,'read_battery',side_effect=[
                [b.BatteryResult(False,detail='timeout')],[b.BatteryResult(True,level=73)]]) as read:
            rows = b.discover_and_read_all()
        self.assertEqual([c.args[0].path for c in read.call_args_list],['a','b'])
        self.assertTrue(all(c.kwargs['listen_seconds']==3 for c in read.call_args_list))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0].level,73)

    def test_standard_sibling_replaces_failure_without_changing_user_key(self):
        vendor = iface(path='vendor',page=0xFFAB)
        standard = iface(path='battery')
        with self.scan_context([vendor,standard],('vendor',)), \
                patch.object(b,'read_battery',return_value=[b.BatteryResult(False,detail='timeout')]), \
                patch.object(b,'monitor_input_reports',side_effect=[(None,[]),(b.BatteryResult(True,level=0),[])]):
            rows = b.discover_and_read_all()
        self.assertEqual(len(rows),1)
        self.assertEqual((rows[0].key,rows[0].level,rows[0].error),('9999:0001:FFAB',0,''))

    def test_known_private_route_does_not_fall_back_to_generic_input(self):
        d = iface(vid=0x054C,pid=0x0EBF,page=0xFF04,usage=2)
        with self.scan_context([d],('input',)), \
                patch.object(b,'read_battery',return_value=[b.BatteryResult(False,detail='no response')]), \
                patch.object(b,'monitor_input_reports') as listen:
            rows = b.discover_and_read_all()
        listen.assert_not_called()
        self.assertEqual(rows[0].error,'no response')

    def test_battery_only_listener_avoids_unrelated_and_reserved_input(self):
        dev = Mock(); dev.value_caps.return_value = [cap(1,0x30)]
        with patch.object(b,'HidDevice',return_value=dev) as opened:
            self.assertEqual(b.monitor_input_reports(iface(),battery_only=True),(None,[]))
            dev.read_input.assert_not_called()
            dev.close.assert_called_once()
            opened.reset_mock()
            self.assertEqual(b.monitor_input_reports(iface(page=1,usage=6),battery_only=True),(None,[]))
            opened.assert_not_called()

    def test_battery_listener_stops_after_first_valid_zero(self):
        dev = Mock(); dev.value_caps.return_value = [cap()]
        dev.read_input.return_value = b'\x01\0'; dev.usage_value.return_value = 0
        with patch.object(b,'HidDevice',return_value=dev):
            result, reports = b.monitor_input_reports(iface(),battery_only=True)
        self.assertTrue(result.ok)
        self.assertEqual(result.level,0)
        dev.read_input.assert_called_once()
        self.assertEqual(reports,[b'\x01\0'])
        dev.close.assert_called_once()

    def test_failed_listener_preserves_missing_battery(self):
        dev = Mock(); dev.value_caps.return_value = [cap()]
        dev.read_input.return_value = None
        with patch.object(b,'HidDevice',return_value=dev), \
                patch.object(b.time,'monotonic',side_effect=[0,0,4]):
            result, reports = b.monitor_input_reports(iface(),battery_only=True)
        self.assertIsNone(result)
        self.assertEqual(reports,[])

    def test_later_success_selected_after_failed_query(self):
        fail = b.BatteryResult(False,detail='unsupported feature')
        success = b.BatteryResult(True,level=42)
        self.assertIs(b.best_result([fail,success]),success)


if __name__ == '__main__':
    unittest.main()
