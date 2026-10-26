"""Offline regression: captured report shapes, invalid frames, and HID routing."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import peripheral_protocols as p
import battery
from brand_icons import match_brand


def iface(vid=0x260D, pid=0x1114, page=0xFF02, usage=2, length=17):
    return SimpleNamespace(vid=vid, pid=pid, usage_page=page, usage=usage,
                           input_len=length, output_len=length, feature_len=length,
                           path=f'device-{vid}-{pid}', product='', manufacturer='',
                           vid_pid=f'{vid:04X}:{pid:04X}')


def padded(data, size):
    return bytes(data).ljust(size, b'\x00')


class ProtocolTests(unittest.TestCase):
    def test_standard_hid_fields_are_battery_percentages(self):
        import hid_win
        self.assertEqual({(page,usage) for page,usage,_ in hid_win.BATTERY_USAGES},
                         {(6,0x20),(0x0D,0x3B),(0x85,0x64),(0x85,0x65)})

    def test_standard_percentage_is_not_rescaled_to_storage_range(self):
        from unittest.mock import MagicMock
        cap=SimpleNamespace(UsagePage=0x85,u=SimpleNamespace(NotRange=SimpleNamespace(Usage=0x64)),
                            ReportID=1,LogicalMax=255)
        dev=MagicMock();dev.__enter__.return_value=dev
        dev.value_caps.return_value=[cap];dev.get_feature_report.return_value=b'\x01\x28'
        dev.usage_value.return_value=40
        with patch.object(battery,'HidDevice',return_value=dev):
            self.assertEqual(battery.strategy_standard_battery_usage(iface()).level,40)
            cap.UsagePage=6;cap.u.NotRange.Usage=0x29
            dev.get_feature_report.reset_mock()
            self.assertIsNone(battery.strategy_standard_battery_usage(iface()))
            dev.get_feature_report.assert_not_called()
    def test_captured_input_frames(self):
        self.assertEqual(p.decode('incott',bytes.fromhex('09 5f 10 04 00 0f 0f 10'))[:2],(95,'discharging'))
        self.assertEqual(p.decode('incott',bytes.fromhex('09 e1 10 04 00 0f 0f 10'))[:2],(97,'charging'))
        self.assertEqual(p.decode('rapoo',bytes.fromhex('2a 01 00 00 04 11 20 00 00 62'))[0],98)
        self.assertEqual(p.decode('delux',bytes.fromhex('03 20 40 01 62')),
                         (98,'unknown','多彩 DELUX M600 Pro 鼠标'))
        self.assertIsNone(p.decode('delux',bytes.fromhex('03 55 40 01 62')))
        self.assertEqual(p.decode('delux',bytes.fromhex('03 55 40 01 62'),descriptor='DELUX M800 Mini')[0],98)

    def test_percent_boundaries_and_invalid_data(self):
        for level in (0,1,42,99,100,101,127,255):
            frames = {
                'dareu':padded([8,4,0,0,0,0,level],17),
                'ajazz':padded([0xF0,0x30,1,0,0,0,0,0,level,1],64),
                'hecate':padded([4,0,0,0x1A,6,0,0,0,level,2],64),
                'rapoo':bytes([0x2A,1,0,0,4,0x11,0x20,0,0,level]),
                'delux':bytes([3,0x20,0x40,1,level]),
                'akko':padded([5,level,0,0,0,1,1],65),
                'aula':padded([0,0x55,0x10],9)+bytes(16)+bytes([1,level,1]),
            }
            for protocol, raw in frames.items():
                with self.subTest(protocol=protocol, level=level):
                    result=p.decode(protocol,raw)
                    valid=level<=100 and not(protocol=='akko' and level==0)
                    self.assertEqual(result is not None,valid)
                    if valid:self.assertEqual(result[0],level)
        for protocol in {x.protocol for x in p.CATALOG.values()}:
            for raw in (b'',b'\x00',bytes(65),bytes([255])*65):
                self.assertIsNone(p.decode(protocol,raw),(protocol,raw))
        for raw_level in (101,127,229,255):
            self.assertIsNone(p.decode('incott',padded([9,raw_level,0x10],8)))
        self.assertIsNone(p.decode('aula',padded([0,0x55,0x10],28))) # no wireless link
        self.assertIsNone(p.decode('hecate',padded([4,0,0,0x1A,6,0,0,0xFF,55,0],64)))
        self.assertIsNone(p.decode('ajazz',padded([0xF0,0x20,1,0,0,0,0,0,55,1],64)))

    def test_rk_stale_response_and_charge_bit(self):
        raw=padded([3,0x50,0,10,12,1,0xB2],64)
        self.assertEqual(p.decode('rk',raw,sequence=12)[:2],(50,'charging'))
        self.assertIsNone(p.decode('rk',raw,sequence=13))
        self.assertIsNone(p.decode('rk',padded([3,0x50,0,10,12,2,50],64),sequence=12))

    def test_model_id_and_interface_gates(self):
        cases=[iface(),iface(0x24AE,0x1205,0xFF0B,0x104,64),
               iface(0xA8A5,0x2157,0xFF01,0x10,64),iface(0x0C45,0xFEFC,0xFF80,1,65),
               iface(0x3151,0x5038,0xFFFF,1,65),iface(0x388D,0x0025,0xFF1C,0x92,32),
               iface(0x372E,0x1019,0xFF00,1,64),iface(0x320F,0x706E,0xFF1C,0x92,64),
               iface(0x093A,0x522C,0xFF05,1,8),iface(0x1D57,0xFA60,0x0C,1,8)]
        for item in cases:
            self.assertTrue(p.matches_interface(item),item)
            bad=copy.copy(item);bad.pid=0xEEEE
            self.assertFalse(p.matches_interface(bad))
            bad=copy.copy(item);bad.usage_page=1
            self.assertFalse(p.matches_interface(bad))
        with patch.object(p,'HidDevice',side_effect=AssertionError('must not open')):
            self.assertIsNone(p.read(iface(pid=0xEEEE),battery.BatteryResult))
            self.assertFalse(p.read(iface(page=1),battery.BatteryResult).ok)

    def test_exact_route_never_falls_through(self):
        item=iface(0xA8A5,0x2157,0xFF01,0x10,64)
        failure=battery.BatteryResult(ok=False,source='AJAZZ')
        with patch.object(p,'read',return_value=failure), \
             patch.object(battery,'strategy_mchose',side_effect=AssertionError('wrong protocol')):
            self.assertEqual(battery.read_battery(item),[failure])

    def test_query_bytes_and_response(self):
        class FakeDevice:
            def __init__(self, response):self.response=response;self.sent=[]
            def drain(self):pass
            def write_output(self,data,**kwargs):self.sent.append(data)
            def read_input(self,**kwargs):return self.response
        for protocol, device, response, prefix in (
            ('dareu',iface(),padded([8,4,0,0,0,0,77],17),bytes([8,4])),
            ('ajazz',iface(0xA8A5,0x2157,0xFF01,0x10,64),padded([0xF0,0x30,1,0,0,0,0,0,77,1],64),bytes([0xF0,0x30,1])),
            ('hecate',iface(0x320F,0x706E,0xFF1C,0x92,64),padded([4,0,0,0x1A,6,0,0,0,77,2],64),bytes([4,0x20,0,0x1A,6]))):
            dev=FakeDevice(response)
            result,_=p._active_read(dev,device,protocol)
            self.assertEqual(result[0],77)
            self.assertEqual(len(dev.sent),1)
            self.assertEqual(len(dev.sent[0]),device.output_len)
            self.assertTrue(dev.sent[0].startswith(prefix))
            if protocol=='dareu':self.assertEqual(sum(dev.sent[0])&255,0x55)

    def test_listener_disconnect_cleanup(self):
        from threading import Event
        listener=SimpleNamespace(stop=Event())
        with patch.dict(p._listeners,{'gone':listener},clear=True):
            p.prune_listeners([])
            self.assertTrue(listener.stop.is_set())
            self.assertEqual(p._listeners,{})

    def test_brand_aliases(self):
        names={'dareu':'达尔优','rapoo':'雷柏','ajazz':'黑爵','aula':'狼蛛','darmoshark':'达摩鲨',
               'incott':'因科特','delux':'多彩','inphic':'英菲克','thunderobot':'雷神',
               'machenike':'机械师','flesports':'腹灵','durgod':'杜伽','kzzi':'珂芝','rk':'Royal Kludge',
               'akko':'艾酷','lofree':'洛斐','varmilo':'阿米洛','melgeek':'MelGeek','iqunix':'IQUNIX','hecate':'漫步者电竞'}
        for brand,alias in names.items():
            self.assertEqual(match_brand(alias+' 测试设备'),brand)
            self.assertEqual(match_brand(brand+' X1'),brand)
        for title in ('USB receiver','Deluxe Mouse','Network Keyboard','AULA / AJAZZ 2.4G 键盘','Akko / MonsGeek 2.4G 键盘'):
            self.assertIsNone(match_brand(title),title)


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ProtocolTests))
    report={'test_groups':result.testsRun,'passed':result.wasSuccessful(),
            'receiver_ids':len(p.CATALOG),'hardware_validation':'Not performed for newly supported models'}
    (Path(__file__).resolve().parent.parent/'release'/'protocol-verification-v5.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    raise SystemExit(0 if result.wasSuccessful() else 1)
