import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import ajazz_ak820 as k
import peripheral_protocols as catalog
import battery


def iface(**changes):
    values = dict(vid=0x1A2C,pid=0x8FFF,usage_page=0xFF02,usage=2,input_len=65,
                  output_len=65,feature_len=0,path='fixture',product='2.4 Wireless Device',manufacturer='')
    values.update(changes)
    return SimpleNamespace(**values)


def response(level=73, mode=1):
    data=bytearray(k.BATTERY_QUERY);data[6:8]=bytes((level,mode))
    return bytes(data)


class AK820Tests(unittest.TestCase):
    def test_log_interface_and_siblings(self):
        self.assertTrue(catalog.matches_interface(iface()))
        for values in (dict(pid=0xA036),dict(vid=0x0C45),dict(usage_page=0xFF01,usage=0xFF01,input_len=4,output_len=0),
                       dict(usage_page=1,usage=6),dict(input_len=64),dict(output_len=64),dict(feature_len=65)):
            dev=Mock();d=iface(**values)
            self.assertFalse(k.matches(d))
            with self.assertRaises(ValueError):k.read(dev,d)
            dev.write_output.assert_not_called()

    def test_percent_and_connection_not_charging(self):
        for level in (0,1,73,100):
            for mode in (1,3):
                self.assertEqual(k.decode(response(level,mode)),(level,'unknown',k.MODEL))
        for mode in (0,2,4,255):self.assertIsNone(k.decode(response(73,mode)))
        for level in (101,254,255):self.assertIsNone(k.decode(response(level)))

    def test_reject_unrelated_truncated_and_echo(self):
        for data in (b'',bytes(65),k.BATTERY_QUERY,response()[:-1],response()+b'\0'):
            self.assertIsNone(k.decode(data))
        for index in range(6):
            data=bytearray(response());data[index]^=1
            self.assertIsNone(k.decode(data))

    def test_single_query_skips_unrelated_input(self):
        dev=Mock();dev.read_input.side_effect=[k.BATTERY_QUERY,b'\x06\x01\x02\x03',response()]
        parsed,raw=catalog._active_read(dev,iface(),'ajazz_ak820')
        self.assertEqual(parsed,(73,'unknown',k.MODEL));self.assertEqual(raw,response())
        dev.write_output.assert_called_once_with(bytes.fromhex('00aa55cc330e')+bytes(59),use_hidd=False)
        dev.set_feature_report.assert_not_called()

    def test_timeout_offline_and_failed_transport(self):
        dev=Mock();dev.read_input.return_value=None
        with patch.object(k.time,'monotonic',side_effect=[0,0,1]):
            self.assertEqual(k.read(dev,iface()),(None,b''))
        dev=Mock();dev.read_input.return_value=response(73,0)
        self.assertIsNone(k.read(dev,iface())[0]);self.assertEqual(dev.write_output.call_count,1)
        dev=Mock();dev.write_output.return_value=False
        self.assertEqual(k.read(dev,iface()),(None,b''));dev.read_input.assert_not_called()

    def test_monitor_names_keyboard_and_retains_unknown_state(self):
        with patch.object(catalog,'HidDevice'),patch.object(k,'read',return_value=((73,'unknown',k.MODEL),response())):
            result=catalog.read(iface(),battery.BatteryResult)
        self.assertTrue(result.ok);self.assertEqual(result.level,73)
        self.assertEqual(result.status,'unknown');self.assertIn('键盘',result.extra['model_name'])


if __name__=='__main__':unittest.main()
