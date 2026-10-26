import ctypes
import unittest
from unittest.mock import Mock,patch
import hid_evidence as e
import hid_win as hw
import protocol_probes as probes
from test_diagnostic import iface

class EvidenceTests(unittest.TestCase):
    def test_range_and_signed_capabilities(self):
        c=hw.HIDP_VALUE_CAPS();c.IsRange=1;c.LogicalMin=-127;c.LogicalMax=127;c.BitSize=8;c.ReportCount=6
        values=(ctypes.c_ushort*8).from_address(ctypes.addressof(c.u))
        values[:]=[1,8,2,3,4,5,6,13]
        out=e.cap_record(c,'input')
        self.assertEqual(out['range']['DataIndexMax'],13)
        self.assertEqual(out['LogicalMin'],-127)
        self.assertEqual(ctypes.sizeof(hw.HIDP_BUTTON_CAPS),72)
        self.assertEqual(ctypes.sizeof(e.LinkNode),24 if ctypes.sizeof(ctypes.c_void_p)==8 else 20)

    def test_passive_capture_rejects_mixed_keyboard_reports(self):
        evidence={'errors':[],'controls':[{'report_type':'input','UsagePage':0xFF00,'ReportID':65}]}
        self.assertEqual(e.vendor_input_ids(evidence,iface()),{65})
        evidence['controls'].append({'report_type':'input','UsagePage':7,'ReportID':1})
        self.assertFalse(e.vendor_input_ids(evidence,iface()))
        evidence['controls'].pop();evidence['errors'].append('missing capability')
        self.assertFalse(e.vendor_input_ids(evidence,iface()))

    def test_passive_records_only_declared_ids_and_never_writes(self):
        dev=Mock();emit=Mock();evidence={'errors':[],'controls':[{'report_type':'input','UsagePage':0xFF00,'ReportID':65}]}
        with patch.object(hw.HidDevice,'read_input',side_effect=[b'\x01secret',b'\x41status',b'\x41status']),patch.object(e.time,'monotonic',side_effect=[0,0,.1,.2,2]):
            e.observe(dev,iface(),evidence,emit)
        frames=[c.kwargs['hex'] for c in emit.call_args_list if c.args[0]=='rx']
        self.assertEqual(frames,[b'\x41status'.hex()])
        dev.write_output.assert_not_called();dev.set_feature_report.assert_not_called();dev.drain.assert_not_called()

    def test_query_outcome_is_specific(self):
        summary={'probes_executed':['generic_safe_dump']}
        self.assertEqual(e.query_status([],summary,'completed'),'protocol_not_registered')
        self.assertEqual(e.query_status([{'event':'tx'}],{},'completed'),'query_no_response')
        self.assertEqual(e.query_status([{'event':'tx'},{'event':'rx'}],{},'completed'),'query_response_unparsed')
        self.assertEqual(e.query_status([],{},'timeout'),'timeout')

    def test_monitor_catalog_is_reused_for_exact_akko_interface(self):
        d=iface(vid=0x3151,pid=0x5038)
        with patch.object(probes.battery_catalog,'_active_read',return_value=((52,'discharging',''),b'fixture')) as read,patch.object(probes,'probe_other_and_generic',return_value={}):
            result=probes.run_interface_probes(Mock(),d,Mock())
        self.assertEqual(read.call_args.args[2],'akko')
        self.assertEqual(result['capabilities']['battery']['level_percent'],52)

if __name__=='__main__':unittest.main()
