"""Captured mouse replies; keyboard reply fixtures are synthetic, not hardware proof."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
import battery as b
import wireless_battery as w
import weisheng_keyboard as wk
import vxe_r1_pro_max as vxe
import mouse_settings as ms
import dpi_extended as dx
import dpi_adapters as da
import peripheral_protocols as pp
from verify_wireless_battery import iface

BATTERY=bytes.fromhex('08040000000214000dfe00000000000028')
HEADER=bytes.fromhex('01540451035200550055')
ROWS=bytes.fromhex('070700470f0f00371f1f00173f3f00d7')

class LogTests(unittest.TestCase):
    def test_captured_battery_has_no_declared_voltage(self):
        self.assertEqual(w.decode_compx(BATTERY),(20,'discharging',None))

    def test_battery_name_requires_identity(self):
        d=iface(0x3554,0xF58A)
        for identity,name in [((2,27),vxe.MODEL),((2,67),None)]:
            with patch.object(w.hw,'HidDevice'),patch.object(w,'query',return_value=((20,'discharging',None),BATTERY)),patch.object(w,'_input',return_value=(identity,b'identity')):
                result=w.read(d,b.BatteryResult)
            self.assertTrue(result.ok)
            self.assertEqual(result.extra.get('model_name'),name)

    def read_config(self,profiles=(1,1)):
        d=iface(0x3554,0xF58A);m=ms.Mouse(d.path,'VXE',d.vid_pid,'dpi',(d.vid,d.pid))
        # Inactive rows are synthetic padding: the capture contains four active rows.
        table=ROWS+ROWS
        def read(dev,interface,address,count):
            return HEADER if address==0 else table[address-12:address-12+count]
        with patch.object(ms,'atk_identity',return_value=(2,27)),patch.object(ms,'atk_profile',side_effect=profiles),patch.object(ms,'atk_read',side_effect=read):
            return dx.vxe_read(None,d,m)

    def test_captured_dpi_and_profile(self):
        s=self.read_config()
        self.assertEqual(s.stages,((400,400),(800,800),(1600,1600),(3200,3200)))
        self.assertEqual((s.active,s.base.profile),(3,1))
        changed=replace(s,base=replace(s.base,profile=2))
        self.assertNotEqual(da.fingerprint(s),da.fingerprint(changed))

    def test_profile_change_during_read_rejected(self):
        with self.assertRaisesRegex(ValueError,'配置层'):
            self.read_config((1,2))

    def test_captured_light_and_corrupt_pair(self):
        raw=bytes.fromhex('025380d503520055')
        state=vxe.lighting(raw)
        self.assertEqual((state['mode_name'],state['brightness_level'],state['speed_level']),('呼吸',2,2))
        with self.assertRaises(ValueError):vxe.lighting(raw[:-1]+b'\1')

    def test_keyboard_route_is_exact(self):
        d=iface(0x320F,0x5088,0xFF1C,0x92,64)
        self.assertTrue(pp.matches_interface(d))
        self.assertIn('VGN V98',pp.profile_for(d).model)
        for change in [dict(usage_page=0xFFEF,usage=0),dict(usage_page=1),dict(pid=0x1234),dict(output_len=65)]:
            other=SimpleNamespace(**(vars(d)|change))
            self.assertFalse(wk.matches(other))
            with self.assertRaises(ValueError):wk.read(Mock(),other)

    def test_keyboard_synthetic_replies_and_echo_rejection(self):
        self.assertIsNone(wk.decode(wk.QUERY))
        raw=bytearray(wk.QUERY);raw[7:10]=bytes([99,37,1])
        self.assertEqual(wk.decode(raw),(37,'charging',''))
        for index,value in [(0,5),(3,0x19),(8,101),(9,3)]:
            bad=bytearray(raw);bad[index]=value
            self.assertIsNone(wk.decode(bad))
        self.assertIsNone(wk.decode(raw[:-1]))

    def test_keyboard_one_query_and_bounded_timeout(self):
        dev=Mock();dev.read_input.return_value=b''
        d=iface(0x320F,0x5088,0xFF1C,0x92,64)
        with patch.object(wk.time,'monotonic',side_effect=[0,0,2]):
            self.assertEqual(wk.read(dev,d),(None,b''))
        dev.write_output.assert_called_once_with(wk.QUERY,use_hidd=False)
        self.assertEqual(wk.QUERY[:8],bytes.fromhex('0400001a06000000'))

if __name__=='__main__':unittest.main()
