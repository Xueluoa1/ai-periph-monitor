import ctypes
import unittest
from unittest.mock import Mock,patch
import hid_evidence as e
import hid_win as hw
import protocol_probes as probes
from test_diagnostic import iface

class EvidenceTests(unittest.TestCase):
    def test_m618xsd_automatically_queries_online_and_battery(self):
        import delux_m618xsd as m
        d=iface(vid=0x320F,pid=0x221D,usage_page=0xFF1C,usage=0x92,input_len=64,output_len=64,feature_len=0)
        online=bytearray(m.ONLINE_QUERY);online[8]=255
        battery=bytearray(m.BATTERY_QUERY);battery[8:10]=bytes((42,2))
        dev=Mock();dev.read_input.side_effect=[bytes(online),bytes(battery)]
        with patch.object(probes,'probe_other_and_generic',return_value={}):
            result=probes.run_interface_probes(dev,d,Mock())
        self.assertEqual(result['capabilities']['battery']['level_percent'],42)
        self.assertIn('M618XSD',result['capabilities']['battery']['model'])
        self.assertEqual([c.args[0] for c in dev.write_output.call_args_list],[m.ONLINE_QUERY,m.BATTERY_QUERY])

    def test_jbl_route_never_queries_or_infers_battery(self):
        for page,size in ((0xFF13,62),(0xFF14,512)):
            d=iface(vid=0x0ECB,pid=0x214B,usage_page=page,usage=1,input_len=size,output_len=size,feature_len=0)
            dev=Mock()
            with patch.object(probes,'probe_other_and_generic') as generic:
                result=probes.run_interface_probes(dev,d,Mock())
            self.assertEqual(result['probes_executed'],['jbl_smart_tx_evidence'])
            self.assertNotIn('battery',result['capabilities'])
            self.assertFalse(result['capabilities']['identity']['headphone_identity_verified'])
            dev.write_output.assert_not_called();dev.set_feature_report.assert_not_called();generic.assert_not_called()
            self.assertEqual(e.query_status([],result,'completed'),'passive_no_report')

    def test_extended_jbl_capture_keeps_report_filter(self):
        dev=Mock();emit=Mock();d=iface(vid=0x0ECB,pid=0x214B,usage_page=0xFF13,input_len=62)
        evidence={'errors':[],'controls':[{'report_type':'input','UsagePage':0xFF13,'ReportID':7}]}
        with patch.object(hw.HidDevice,'read_input',side_effect=[b'\x08\x50',b'\x07data']),patch.object(e.time,'monotonic',side_effect=[0,0,2,9]):
            e.observe(dev,d,evidence,emit,seconds=8,max_reports=256,max_unique=32)
        self.assertEqual([c.kwargs['hex'] for c in emit.call_args_list if c.args[0]=='rx'],[b'\x07data'.hex()])
        self.assertEqual(emit.call_args.kwargs['window_seconds'],8)
        dev.write_output.assert_not_called()

    def test_gamesir_log_interface_runs_battery_reader(self):
        d=iface(vid=0x3537,pid=0x10B9,usage_page=0xFFF0,usage=0x40,feature_len=0)
        dev=Mock();raw=bytearray(64);raw[0]=0x12;raw[1:5]=bytes([128]*4);raw[36]=68
        dev.read_input.return_value=bytes(raw)
        with patch.object(probes,'probe_other_and_generic',return_value={}):
            result=probes.run_interface_probes(dev,d,Mock())
        self.assertEqual(result['capabilities']['battery']['level_percent'],68)
        self.assertEqual(result['capabilities']['battery']['status'],'unknown')
        dev.write_output.assert_called_once_with(bytes((15,242))+bytes(62),use_hidd=False)

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
