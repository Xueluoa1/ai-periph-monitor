"""Regression coverage for routing, failed collection and evidence export."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

import peripheral_diagnostic as m
import diagnostic_runner as runner
import protocol_probes as probes


def iface(**kwargs):
    fields = dict(vid=0x9999, pid=1, usage_page=0xFFFF, usage=1, output_len=64,
                  input_len=64, feature_len=65, reserved=False)
    fields.update(kwargs)
    return SimpleNamespace(**fields)


class DiagnosticTests(unittest.TestCase):
    def test_new_betop_and_headset_routes_use_shared_queries(self):
        cases = [
            (dict(vid=0x20BC,pid=0x518D,usage_page=0x00FF,usage=3), '02155b0050010050',91),
            (dict(vid=0x03F0,pid=0x08BE,usage_page=0xFF13,usage=0xFF00), '510233001a00',51),
            (dict(vid=0x1038,pid=0x2269,usage_page=0xFFC0), 'b003003201',50),
            (dict(vid=0x1038,pid=0x230A,usage_page=0xFFC0), 'b0000003035028',40),
        ]
        for values, raw, level in cases:
            dev = Mock(); dev.read_input.return_value = bytes.fromhex(raw).ljust(64,b'\0')
            result = probes.run_interface_probes(dev,iface(**values),Mock())
            self.assertEqual(result['capabilities']['battery']['level_percent'],level)
            dev.write_output.assert_called_once()
            dev.get_feature_report.assert_not_called()

    def test_eightbitdo_diagnostic_never_enables_enhanced_mode(self):
        d = iface(vid=0x2DC8,pid=0x6009,usage_page=1,usage=5,output_len=0,
                  path='hid#vid&00022dc8_pid&6009#bt')
        dev = Mock()
        dev.read_input.return_value = bytes.fromhex('040f807f7f80000000000000096014').ljust(64,b'\0')
        result = probes.run_interface_probes(dev,d,Mock())
        self.assertEqual(result['capabilities']['battery']['level_percent'],20)
        self.assertEqual([call[0] for call in dev.mock_calls if call[0] != 'trace'],['read_input'])
        d.path = 'hid#vid_2dc8&pid_6009#usb'
        dev.reset_mock()
        result = probes.run_interface_probes(dev,d,Mock())
        self.assertFalse(result['capabilities']['identity']['interface_verified'])
        self.assertEqual([call for call in dev.mock_calls if call[0] != 'trace'],[])

    def test_passive_bluetooth_is_scheduled_without_output_or_feature(self):
        d = iface(vid=0x2DC8,pid=0x6009,usage_page=1,usage=5,output_len=0,feature_len=0,
                  path='hid#vid&00022dc8_pid&6009#bt')
        self.assertTrue(m.should_probe_interface(vars(d),False,set()))
        d.path = 'hid#vid_2dc8&pid_6009#usb'
        self.assertFalse(m.should_probe_interface(vars(d),True,set()))
        d.usage = 6
        self.assertFalse(m.should_probe_interface(vars(d),True,set()))

    def test_controller_and_mouse_queries_export_the_same_decoding(self):
        cases=[
            (dict(vid=0x20BC,pid=0x5191,usage_page=0xFF00,usage=3,product='BTP-KP20EB'),
             '02155d0050010100',93,''),
            (dict(vid=0x37D7,pid=0x2501,usage_page=0xFFA0,output_len=32),
             '045aa5010100800200000000054501007045',None,'5/5 档'),
            (dict(vid=0x37D7,pid=0x2401,usage_page=0xFFA0,output_len=33,input_len=33),
             '005aa501010082020000000003',None,'3/5 档'),
            (dict(vid=0x1038,pid=0x1838,usage_page=0xFFC0), 'd20b',50,'约 50%'),
        ]
        for values,raw,level,label in cases:
            d=iface(**values);dev=Mock()
            dev.read_input.return_value=bytes.fromhex(raw).ljust(d.input_len,b'\0')
            result=probes.run_interface_probes(dev,d,Mock())
            battery=result['capabilities']['battery']
            self.assertEqual((battery['level_percent'],battery['level_label']),(level,label))
            self.assertFalse(result['capabilities']['identity']['hardware_verified'])
            dev.write_output.assert_called_once()
            dev.set_feature_report.assert_not_called()

    def test_cyclone_battery_is_automatically_queried(self):
        d=iface(vid=0x3537,pid=0x100B,usage_page=0xFFF0,usage=0x40,feature_len=0)
        dev=Mock();raw=bytearray(64);raw[0]=0x12;raw[1:5]=b'\x80'*4;raw[36]=73
        dev.read_input.return_value=bytes(raw)
        with patch.object(probes,'probe_other_and_generic',return_value={}):
            result=probes.run_interface_probes(dev,d,Mock())
        self.assertEqual(result['capabilities']['battery']['level_percent'],73)
        self.assertIn('旋风2',result['capabilities']['battery']['model'])
        dev.write_output.assert_called_once_with(bytes.fromhex('0f03')+bytes(62),use_hidd=False)

    def test_wireless_receiver_query_and_unrelated_collection(self):
        d=iface(vid=0x1038,pid=0x22A1,usage_page=0xFFC0,usage=1)
        emit=Mock()
        with patch.object(probes.receiver_catalog,'query',return_value=probes.receiver_catalog.Reading(42)) as query:
            result=probes.run_interface_probes(Mock(),d,emit)
            self.assertEqual(result['capabilities']['battery']['level_percent'],42)
            query.assert_called_once()
        d.usage_page=0xFF13
        with patch.object(probes.receiver_catalog,'query') as query:
            result=probes.run_interface_probes(Mock(),d,emit)
            query.assert_not_called()
            self.assertFalse(result['capabilities']['identity']['interface_verified'])

    def test_pro_max_capture_decodes_one_sensor_and_indicator(self):
        device=iface(vid=0x3554,pid=0xF58A,usage_page=0xFF02,usage=2,input_len=17,output_len=17)
        eeprom={0:'01540451035200550055',10:'0154',12:'07070047',16:'0f0f0037',20:'1f1f0017',24:'3f3f00d7',
                44:'ff000056',48:'00ff0056',52:'0000ff56',56:'ff00ff57',76:'025380d503520055ff00'}
        def exchange(dev,iface,emit,label,cmd,payload,accept):
            if cmd==4:return bytes.fromhex('08040000000214000dfe00000000000028')[1:]
            if cmd==0x10:return bytes.fromhex('081000000003021b3800000000000000e5')[1:]
            if cmd==0x0e:return bytes.fromhex('080e00000001000000000000000000003e')[1:]
            address=payload[1]*256+payload[2]
            if address not in eeprom:return None
            return bytes((8,0,payload[1],payload[2],payload[3]))+bytes.fromhex(eeprom[address])
        with patch.object(probes,'_atk_exchange',side_effect=exchange):
            result=probes.probe_atk_compx(Mock(),device,Mock())
        self.assertEqual(result['battery'],dict(level_percent=20,charging=False,voltage_mv=None))
        self.assertEqual(result['identity']['model'],'VXE R1 Pro Max')
        self.assertEqual([s['dpi'] for s in result['dpi']['stages']],[[v,v] for v in (400,800,1600,3200)])
        self.assertNotIn('dpi_ultra',result['dpi']['stages'][0])
        self.assertEqual(result['lighting']['mode_name'],'呼吸')

    def test_v98_query_only_on_confirmed_vendor_collection(self):
        d=iface(vid=0x320F,pid=0x5088,usage_page=0xFF1C,usage=0x92,feature_len=0)
        dev=Mock();dev.all_value_caps.return_value=[];dev.caps.FeatureReportByteLength=0
        with patch.object(probes.wk,'read',return_value=((37,'charging',''),b'reply')) as query:
            result=probes.run_interface_probes(dev,d,Mock())
        query.assert_called_once()
        self.assertEqual(result['capabilities']['battery']['level_percent'],37)
        d.usage_page=0xFFEF;d.usage=0
        with patch.object(probes.wk,'read') as query:
            probes.run_interface_probes(dev,d,Mock())
        query.assert_not_called()

    def test_xiaomi_diagnostic_gate_rejects_writes_and_reset(self):
        import xiaomi_probe as xp
        dev=Mock();query=xp.QueryDevice(dev,Mock())
        d=iface(vid=0x2717,pid=0x5111,usage_page=0xFFEF,usage=0,input_len=64,output_len=64,feature_len=64)
        for cmd in (*xp.xm.SETS,0x14,0x81,0x82):
            with self.assertRaises(ValueError):query.set_feature_report(bytes((5,1,cmd,0))+bytes(60))
        dev.set_feature_report.assert_not_called()
        query.set_feature_report(xp.xm.packet(d,0x16))
        dev.set_feature_report.assert_called_once()

    def test_xiaomi_battery_survives_configuration_failure(self):
        import xiaomi_probe as xp
        d=iface(vid=0x2717,pid=0x5111,usage_page=0xFFEF,usage=0,input_len=64,output_len=64,feature_len=64)
        with patch.object(xp.xm,'battery',return_value=(44,'discharging')),patch.object(xp.xm,'read_config',side_effect=ValueError('unconfirmed')):
            result=xp.probe(Mock(),d,Mock())
        self.assertEqual(result,{'battery':{'level_percent':44,'charging':False}})

    def test_r1_nk_capture_uses_sensor_codec_and_declared_battery_length(self):
        device=iface(vid=0x373B,pid=0x10C9,usage_page=0xFF02,usage=2,input_len=17,output_len=17)
        eeprom={0:'01540451015400550055',10:'0154',12:'09090043',16:'12120031',
                20:'2525000b',24:'4b4b00bf',44:'ff000056',48:'00ff0056',52:'0000ff56',56:'ff00ff57'}
        def exchange(dev,iface,emit,label,cmd,payload,accept):
            if cmd==4:return bytes.fromhex('08040000000255000fa40000000000003f')[1:]
            if cmd==0x10:return bytes.fromhex('08100000000202430000000000000000f6')[1:]
            if cmd==0x0e:return bytes.fromhex('080e00000001000000000000000000003e')[1:]
            address=payload[1]*256+payload[2]
            if address not in eeprom:return None
            return bytes((8,0,payload[1],payload[2],payload[3]))+bytes.fromhex(eeprom[address])
        with patch.object(probes,'_atk_exchange',side_effect=exchange):
            result=probes.probe_atk_compx(Mock(),device,Mock())
        self.assertEqual(result['battery'],dict(level_percent=85,charging=False,voltage_mv=None))
        self.assertEqual(result['identity']['raw_payload'],'0243')
        self.assertEqual(result['identity']['model'],probes.r1nk.MODEL)
        self.assertEqual([s['dpi'] for s in result['dpi']['stages']],[[v,v] for v in (400,800,1600,3200)])
        self.assertNotIn('dpi_ultra',result['dpi']['stages'][0])

    def test_am_controls_transport_rejects_setting_writes(self):
        from am_controls_probe import QueryDevice
        import am_controls as ac
        dev=Mock();query=QueryDevice(dev,Mock())
        for cmd,payload in [(ac.SET_RATE,b'\x40\x1f'),(ac.SET_LIGHT,b'\x01\0\x06'),(ac.SET_DONGLE,bytes(13))]:
            for packet in ac.packets(cmd,payload,0,62):
                with self.assertRaises(ValueError):query.set_feature_report(packet)
        dev.set_feature_report.assert_not_called()
        packet=ac.packets(ac.GET_RATE,b'',128,62)[0]
        query.set_feature_report(packet)
        dev.set_feature_report.assert_called_once_with(packet)

    def test_rog_lighting_padding_is_not_extra_zones(self):
        rgb=bytes.fromhex('03120300000264ff6e000000').ljust(64,b'\0')
        battery=bytes.fromhex('03120700004b0214630f0000').ljust(64,b'\0')
        def reply(dev,device,emit,label,rid,cmd,*args,**kwargs):
            if label.startswith('battery_rid_'):return battery
            if label=='rgb_lighting':return rgb
            return None
        with patch.object(probes,'_asus_tx_rx',side_effect=reply):
            result=probes.probe_asus_rog(Mock(),iface(vid=0x0B05,pid=0x1ACE,usage_page=0xFF01),Mock())
        self.assertEqual(result['lighting'],[dict(zone_query_index=0,mode_code=2,
            mode_name='彩色循环 (Color Cycle)',brightness=100,color='#ff6e00')])
        self.assertIn(0xF0,probes.ASUS_LIGHT_MODES)

    def test_unknown_vendor_and_wrong_pulsar_never_get_other_brand_commands(self):
        for d in [iface(), iface(vid=0x3710, pid=0x5408), iface(vid=0x3151, pid=0x5038,usage_page=0xFF00),
                  iface(vid=0xA8A5, pid=0x2157), iface(vid=0x0B05, pid=0x9999)]:
            dev = Mock()
            with patch.object(probes, 'probe_other_and_generic', return_value={}):
                probes.run_interface_probes(dev, d, Mock())
            dev.set_feature.assert_not_called()
            dev.write_output.assert_not_called()
            dev.read_input.assert_not_called()

    def test_known_pulsar_uses_compx(self):
        with patch.object(probes, 'probe_atk_compx', return_value={}) as query, \
                patch.object(probes, 'probe_other_and_generic', return_value={}):
            probes.run_interface_probes(Mock(), iface(vid=0x3710, pid=0x5406,
                                       usage_page=0xFF02, usage=2, output_len=17), Mock())
        query.assert_called_once()

    def test_generic_does_not_guess_ids_or_listen_for_keys(self):
        dev = Mock()
        dev.all_value_caps.return_value = []
        dev.caps.FeatureReportByteLength = 65
        with patch.object(probes, 'report_ids_for', return_value=[]):
            probes.probe_other_and_generic(dev, iface(), Mock())
        dev.get_feature.assert_not_called()
        dev.read_input.assert_not_called()

    def test_partial_jsonl_is_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'test.jsonl'
            p.write_text('{"event":"tx"}\n{"event":', encoding='utf-8')
            self.assertEqual(runner.read_events(p)[-1]['event'], 'truncated_record')

    def test_collector_cannot_import_legacy_dpi_write_test(self):
        import ast
        tree = ast.parse(Path(m.__file__).read_text(encoding='utf-8'))
        names = [n.id for n in ast.walk(tree) if isinstance(n, ast.Name)]
        imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        self.assertNotIn('adapt_asus_rog', names)
        self.assertNotIn('dpi_adaptation_engine', imports)

    def test_real_desktop_resolves_to_existing_directory(self):
        self.assertTrue(runner.desktop_directory().is_dir())

    def test_timed_out_interface_keeps_earlier_battery_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'iface_test.jsonl').write_text(json.dumps(dict(event='decoded', label='battery', value={'level_percent': 0})), encoding='utf-8')
            records = [dict(interface_id='iface_test', product='Test', brand_name='Test',
                            vid_hex='0x9999', pid_hex='0x0001', usage_page_hex='0xFF00', usage_hex='0x0001')]
            runner.adaptation_report(folder, records, [{'worker': 'iface_test', 'status': 'timeout'}], m)
            data = json.loads((folder / '适配数据.json').read_text(encoding='utf-8'))
            self.assertEqual(data['interfaces'][0]['capabilities']['battery']['candidate_value']['level_percent'], 0)

    def test_inventory_timeout_still_exports_complete_failure_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = SimpleNamespace(output=tmp, inventory_only=False, all=False, vid=[])
            with patch.object(runner.subprocess, 'run', side_effect=subprocess.TimeoutExpired('worker', 20)), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.run(args, m), 2)
            package = next(Path(tmp).glob('*.zip'))
            with zipfile.ZipFile(package) as z:
                self.assertIn('诊断摘要.txt', z.namelist())
                self.assertIn('inventory.stderr.txt', z.namelist())
                data = json.loads(z.read('适配数据.json'))
                self.assertEqual(data['workers'][0]['status'], 'timeout')
                self.assertFalse(data['write_support_verified'])
                self.assertNotIn('_interfaces.json', z.namelist())

    def test_empty_diff_does_not_claim_equal(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(m.diff_runs(Path(tmp), Path(tmp)), 2)
            self.assertIn('不能判断', out.getvalue())

    def test_diff_retains_multiple_responses_and_length_changes(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as out:
            a, b = Path(tmp) / 'a', Path(tmp) / 'b'
            a.mkdir(); b.mkdir()
            for directory, frames in [(a, ['0102', '0304']), (b, ['010203', '0304'])]:
                text = '\n'.join(json.dumps(dict(event='rx', interface_id='stable', label='query', hex=x)) for x in frames)
                (directory / 'iface_test.jsonl').write_text(text, encoding='utf-8')
            self.assertEqual(len(m._load_rx_map(a)), 2)
            m.diff_runs(a, b)
            self.assertIn('报文长度变化', out.getvalue())


if __name__ == '__main__':
    unittest.main()
