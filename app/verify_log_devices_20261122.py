"""Logged identities/signature plus synthetic battery replies; no live writes."""
from dataclasses import asdict
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import battery
import bt_battery as bt
import log_devices_20261122 as ld
import peripheral_protocols as pp
import mchose_keyboard as mk
import gravastar_k1 as k
import shared_readings as shared
import receiver_battery as rx
import wireless_battery as wireless
from verify_k1 import FakeDevice,iface as keyboard


def iface(protocol):
    vid,pid,page,usage,size,path={
        'hecate_g5':(0x35bb,0xa00b,0xff02,2,64,'hid#vid_35bb&pid_a00b&mi_03&col03'),
        'rapoo_v2':(0x24ae,0x1464,0xff00,2,19,'hid#vid_24ae&pid_1464&mi_01&col09'),
        'sonix_keyboard':(0x05ac,0x024f,0xff60,0x61,33,'hid#vid_05ac&pid_024f&mi_03'),
    }[protocol]
    return SimpleNamespace(vid=vid,pid=pid,usage_page=page,usage=usage,input_len=size,
        output_len=size,feature_len=0,path=path,product='',manufacturer='')


class LogDevicesTests(unittest.TestCase):
    def test_vgn_logged_interface_and_compx_checks(self):
        d=SimpleNamespace(vid=0x391d,pid=0x1a05,usage_page=0xff02,usage=2,
            input_len=17,output_len=17,feature_len=0,path='fixture')
        self.assertIsNone(rx.profile_for(d))
        self.assertFalse(rx.matches(d))
        d.pid=0x1005
        self.assertTrue(rx.matches(d))
        r=bytearray.fromhex('0804000000025000000000000000000000');r[-1]=(0x55-sum(r[:-1]))&255
        dev=Mock();dev.read_input.return_value=bytes(r)
        self.assertEqual(rx.query(dev,d).level,80)
        self.assertEqual(dev.write_output.call_args.args[0].hex(),'0804000000000000000000000000000049')
        r[-1]^=1;self.assertIsNone(rx.decode('vgn3_master',r))
        d.usage_page=0xff06;d.input_len=d.output_len=49
        self.assertFalse(rx.matches(d))
        d.vid,d.pid,d.usage_page,d.feature_len=0x5253,0x1020,0xff01,64
        self.assertIsNone(wireless.route(d))

    def test_shapes_and_main_routes(self):
        for protocol in ('hecate_g5','rapoo_v2','sonix_keyboard'):
            d=iface(protocol);self.assertTrue(pp.matches_interface(d))
            with patch.object(pp,'read',return_value='result'):
                self.assertEqual(battery._read_battery_unlocked(d),['result'])
            d.usage=99;self.assertFalse(pp.matches_interface(d))
        d=iface('hecate_g5');d.path=d.path.replace('col03','col02')
        self.assertFalse(pp.matches_interface(d))

    def test_g5_echo_offline_and_charge_validation(self):
        for percent in (0,72,100):
            for charge in (0,1):
                raw=bytes((0xd0,0x35,0xbb,7,0,1,percent,charge,1,2,3)).ljust(64,b'\0')
                self.assertEqual(ld.decode('hecate_g5',raw)[:2],(percent,'charging' if charge else 'discharging'))
        for raw in ('d035bb0100','d035bb0700006400010203','d035bb070001ff00010203',
                    'ed35bb0700016400010203','d035bb0700016402010203'):
            self.assertIsNone(ld.decode('hecate_g5',bytes.fromhex(raw).ljust(64,b'\0')))
        dev=Mock();dev.read_input.return_value=bytes.fromhex('d035bb0700014800010203').ljust(64,b'\0')
        with patch.object(k,'report_ids',return_value={0xd0}):
            self.assertEqual(ld.read(dev,iface('hecate_g5'),'hecate_g5')[0][0],72)
        dev.write_output.assert_called_once_with(bytes.fromhex('d035bb0100').ljust(64,b'\0'),use_hidd=False)
        with patch.object(k,'report_ids',return_value={2}),self.assertRaises(ValueError):
            ld.read(dev,iface('hecate_g5'),'hecate_g5')

    def test_rapoo_passive_only_and_unknown_charge(self):
        r=bytearray.fromhex('07100340064006016400030100644601020280')
        for state in (0,1,2):
            r[7]=state;self.assertEqual(ld.decode('rapoo_v2',r),(100,'unknown',ld.RAPOO_NAME))
        dev=Mock();dev.read_input.return_value=bytes(r)
        self.assertEqual(ld.read(dev,iface('rapoo_v2'),'rapoo_v2')[0][0],100)
        dev.write_output.assert_not_called();dev.set_feature_report.assert_not_called()
        dev.get_feature_report.assert_not_called();dev.drain.assert_not_called()
        for offset,value in ((0,8),(7,0xff),(8,101),(13,0),(1,0x1f)):
            bad=bytearray(r);bad[offset]=value;self.assertIsNone(ld.decode('rapoo_v2',bad))

    def test_sonix_battery_not_apple_and_zero_is_unavailable(self):
        for level in (1,93,100):
            raw=bytes((0,0x20,1,0,level)).ljust(33,b'\0')
            dev=Mock();dev.read_input.return_value=raw
            self.assertEqual(ld.read(dev,iface('sonix_keyboard'),'sonix_keyboard')[0][0],level)
            packet=dev.write_output.call_args.args[0]
            self.assertEqual(len(packet),33);self.assertEqual(packet[-1],sum(packet[1:-1])&255)
        for level in (0,101,255):
            self.assertIsNone(ld.decode('sonix_keyboard',bytes((0,0x20,1,0,level)).ljust(33,b'\0')))

    def test_hola_actual_identity_then_read_battery(self):
        online=bytes.fromhex('130701000101000000000000000000000000001d')
        identity=bytes.fromhex('130501000a0300000001a70100000000000000cf')
        dev=FakeDevice([online,identity,k.frame(0x4a,b'\x50\0'),online,identity])
        with patch.object(k,'report_ids',return_value={19}):
            self.assertEqual(mk.read(dev,keyboard())[0],(80,'discharging',mk.HOLA_NAME))
        self.assertEqual([r[1] for r in dev.sent],[7,5,0x4a,7,5])
        bad=bytearray(identity);bad[11]=2;bad[-1]=sum(bad[:-1])&255
        with self.assertRaises(ValueError):mk.identify(FakeDevice([online,bytes(bad)]),keyboard())
        bad=bytearray(identity);bad[-1]^=1
        with self.assertRaises(ValueError):mk.read_signature(FakeDevice([bytes(bad)]))
        with self.assertRaises(ValueError):k.identify(FakeDevice([online,identity]))

    def test_bluetooth_disconnected_reason_agrees_with_fields(self):
        d=bt.BluetoothDevice('QY X98Pro BT1','fixture',93,True,False)
        r=bt.group_devices([d],connected_only=False)[0]
        self.assertFalse(r.connected);self.assertTrue(r.cached)
        self.assertIn('设备未连接',r.reason);self.assertNotIn('设备已连接',r.reason)

    def test_voltage_snapshot_roundtrip_without_percentage(self):
        reading=battery.DiscoveredReading(key='test',title='梭鱼 X',subtitle='',
            level=None,level_label='3.98V',voltage_mv=3975)
        snapshot=shared.merge_snapshot(shared.empty_snapshot(),[reading],1)
        parsed=shared.readings_from(snapshot)[0]
        self.assertIsNone(parsed.level);self.assertEqual(parsed.level_label,'3.98V')
        snapshot['readings'][0]['level_label']='99V'
        with self.assertRaises(ValueError):shared.readings_from(snapshot)
