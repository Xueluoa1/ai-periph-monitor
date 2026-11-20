"""Synthetic battery replies; logged identities are not live hardware validation."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import mchose_headset as mh
import mchose_keyboard as mk
import gravastar_k1 as k
import peripheral_protocols as pp
import battery
import mchose_v3_battery as mv3
from functools import reduce
from operator import xor
from verify_k1 import FakeDevice, iface as keyboard


def headset(**kw):
    return SimpleNamespace(**(dict(vid=0x291D,pid=0x385D,usage_page=0xFF90,usage=1,
        input_len=64,output_len=64,feature_len=0,product='MCHOSE V9 PRO',manufacturer='',path='fixture')|kw))


class MchoseExpansionTests(unittest.TestCase):
    def test_v3_validated_frame_and_shared_model_identity(self):
        d=headset(vid=0x3837,pid=0x1014,usage_page=0xff01,product='MCHOSE A7 V3 Ultra+')
        raw=bytearray(64);raw[:8]=bytes.fromhex('4d01010e00090000')
        raw[8:22]=bytes.fromhex('3738264001000000000001005000')
        raw[22]=reduce(xor,raw[2:22],0)
        self.assertEqual(mv3.decode(raw),(80,'discharging'))
        self.assertEqual(mv3.request()[:9].hex(),'4d0101000009000008')
        dev=Mock();dev.read_input.return_value=bytes(raw)
        parsed,_=mv3.read(dev,d)
        self.assertIn('A7 V3 Ultra+',parsed[2])  # PID 4026 must not rename it A5.
        d.product='Receiver';self.assertEqual(mv3.read(dev,d)[0][2],mv3.GENERIC)
        for offset,value in ((0,0),(1,0),(3,60),(4,2),(18,0),(19,2),(20,101)):
            bad=bytearray(raw);bad[offset]=value;bad[22]=reduce(xor,bad[2:22],0)
            self.assertIsNone(mv3.decode(bad))
        bad=bytearray(raw);bad[22]^=1;self.assertIsNone(mv3.decode(bad))
        self.assertIsNone(mv3.decode(mv3.request()))
        self.assertFalse(mv3.matches(headset(vid=0x3837,pid=0x4033,usage_page=0xff01)))
    def test_headset_exact_models_and_channel(self):
        for vid,pid in mh.MODELS:
            self.assertTrue(pp.matches_interface(headset(vid=vid,pid=pid)))
        for change in ({'pid':1},{'usage_page':0xFF82},{'usage':2},{'output_len':16},
                       {'vid':0x3837,'pid':0x6009}):
            self.assertFalse(mh.matches(headset(**change)))
        self.assertIn('V9 PRO',mh.model_name(headset()))
        self.assertIn('共享',mh.model_name(headset(product='USB Receiver')))

    def test_headset_query_and_status(self):
        for level,status,label in ((0,2,'discharging'),(80,3,'charging'),(100,4,'full')):
            dev=Mock();dev.read_input.side_effect=[b'\x55\x65\x01'.ljust(64,b'\0'),
                bytes((0x55,0x65,level,status)).ljust(64,b'\0')]
            parsed,raw=mh.read(dev,headset())
            self.assertEqual(parsed[:2],(level,label))
            dev.write_output.assert_called_once_with(b'\x55\x65\x01'.ljust(64,b'\0'),use_hidd=False)
            dev.set_feature_report.assert_not_called()
        for r in (b'',bytes.fromhex('ed02020e').ljust(64,b'\0'),
                  bytes.fromhex('55656502').ljust(64,b'\0'),bytes.fromhex('5565501a').ljust(64,b'\0')):
            self.assertIsNone(mh.decode(r))

    def test_keyboard_model_gate_and_never_write_settings(self):
        for signature,name in mk.MODELS.items():
            dev=FakeDevice([k.frame(7,b'\1'),k.frame(5,signature),k.frame(0x4a,b'\x50\0'),
                            k.frame(7,b'\1'),k.frame(5,signature)])
            with patch.object(k,'report_ids',return_value={19}):
                parsed,_=mk.read(dev,keyboard())
            self.assertEqual(parsed,(80,'discharging',name))
            self.assertEqual([p[1] for p in dev.sent],[7,5,0x4a,7,5])
        for signature in (bytes(6),bytes.fromhex('030000000116')):
            dev=FakeDevice([k.frame(7,b'\1'),k.frame(5,signature)])
            with patch.object(k,'report_ids',return_value={19}),self.assertRaises(ValueError):
                mk.read(dev,keyboard())
            self.assertEqual([p[1] for p in dev.sent],[7,5])

    def test_k1_lighting_still_refuses_mchose(self):
        dev=FakeDevice([k.frame(7,b'\1'),k.frame(5,bytes.fromhex('030000000115'))])
        with self.assertRaises(ValueError):k.identify(dev)

    def test_keyboard_corruption_pair_change_and_sleep(self):
        sig=bytes.fromhex('030000000115')
        for response in (b'\x65\0',b'\x50\xff',b'\x50\2'):
            dev=FakeDevice([k.frame(7,b'\1'),k.frame(5,sig),k.frame(0x4a,response)])
            with patch.object(k,'report_ids',return_value={19}),self.assertRaises(ValueError):
                mk.read(dev,keyboard())
        dev=FakeDevice([k.frame(7,b'\1'),k.frame(5,sig),k.frame(0x4a,b'\x50\0'),
                        k.frame(7,b'\1'),k.frame(5,bytes.fromhex('030000000335'))])
        with patch.object(k,'report_ids',return_value={19}),self.assertRaises(ValueError):
            mk.read(dev,keyboard())
        dev=FakeDevice([k.frame(7,b'\1'),k.frame(5,sig),k.frame(0x4a,b'\0\0')])
        with patch.object(k,'report_ids',return_value={19}):self.assertIsNone(mk.read(dev,keyboard())[0])

    def test_main_route_and_correct_model(self):
        d=keyboard()
        with patch.object(pp,'read',return_value='result') as reader:
            self.assertEqual(battery._read_battery_unlocked(d),['result'])
            reader.assert_called_once()
        for d in (keyboard(),keyboard(vid=0x41E4,pid=0x2004)):
            self.assertTrue(pp.matches_interface(d))
        self.assertFalse(pp.matches_interface(keyboard(usage_page=0xff04,output_len=0)))
