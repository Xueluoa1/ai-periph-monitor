"""Synthetic protocol fixtures from official driver analysis; no hardware writes."""
import unittest
from unittest.mock import Mock,patch
import darmoshark_m3pro as dm
import peripheral_protocols as pp
from verify_wireless_battery import iface

def reply(target=1,model=0x711,connected=True,level=47,charge=0):
    r=bytearray(65);r[1]=target;r[3]=0x8c;r[5]=8 if connected else 0
    r[6]=charge;r[7]=level;r[12:14]=model.to_bytes(2,'little')
    return bytes(r)

class DarmosharkTests(unittest.TestCase):
    def setUp(self):self.d=iface(0x1915,0x0723,0xFF0A,1,65)

    def test_exact_route(self):
        self.assertTrue(pp.matches_interface(self.d))
        for attr,value in [('usage_page',1),('usage',2),('pid',0x0725),('input_len',64)]:
            with patch.object(self.d,attr,value):self.assertFalse(dm.matches(self.d))

    def test_getter_frames_and_checksum(self):
        for target in (1,65):
            p=dm.request(target)
            self.assertEqual(p[:5],bytes((0,target,0,0x81,1)))
            self.assertEqual((len(p),p[-1],sum(p[1:])&255),(65,0x1e if target==1 else 0xde,0xa1))
        with self.assertRaises(ValueError):dm.request(2)

    def test_model_gate_blocks_mouse_query(self):
        for response in (reply(model=0x710),reply(connected=False)):
            with patch.object(dm,'exchange',return_value=(response[1:],response)) as query:
                self.assertIsNone(dm.read(Mock(),self.d)[0])
            query.assert_called_once()

    def test_two_read_only_queries_and_zero_battery(self):
        for level in (0,47,100):
            dev=Mock();dev.read_input.side_effect=[reply(),reply(65,level=level,charge=1)]
            parsed,raw=dm.read(dev,self.d)
            self.assertEqual(parsed,(level,'charging',dm.MODEL))
            self.assertEqual([c.args[0] for c in dev.write_output.call_args_list],[dm.request(1),dm.request(65)])
            dev.set_feature_report.assert_not_called()

    def test_invalid_frames_are_not_battery(self):
        self.assertIsNone(dm.body(dm.request(65),65))
        self.assertIsNone(dm.body(reply(),65))
        self.assertIsNone(dm.body(reply(65)[:-1],65))
        for level,charge in ((101,0),(40,2)):
            dev=Mock();dev.read_input.side_effect=[reply(),reply(65,level=level,charge=charge)]
            self.assertIsNone(dm.read(dev,self.d)[0])

    def test_timeout_does_not_resend_or_probe_another_target(self):
        dev=Mock();dev.read_input.return_value=b''
        with patch.object(dm.time,'monotonic',side_effect=[0,0,2]):
            self.assertEqual(dm.read(dev,self.d),(None,b''))
        dev.write_output.assert_called_once()

if __name__=='__main__':unittest.main()
