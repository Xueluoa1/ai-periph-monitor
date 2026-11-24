"""Regression coverage for reported transport selection and PAW3955 DPI writes."""
import ast
import copy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock,MagicMock,patch
import battery as bt
import mouse_settings as ms
import mchose_keyboard as mk
import gravastar_k1 as k
import log_devices_20261122 as ld
from verify_k1 import FakeDevice,iface as keyboard


def a9():
    return SimpleNamespace(vid=0x373B,pid=0x1279,usage_page=0xFF02,usage=2,
        input_len=17,output_len=17,feature_len=0,path='fixture',vid_pid='373B:1279',product='Receiver')


class FeedbackTests(unittest.TestCase):
    def test_calibration_dropdown_survives_worker_and_save(self):
        # Execute the actual nested UI callbacks with fake widgets; no hardware or Tk loop.
        source=ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8-sig'))
        names=('hexbytes','build_spec','test_cal')
        nodes=[next(n for n in ast.walk(source) if isinstance(n,ast.FunctionDef) and n.name==name) for name in names]
        for mode in ('output','feature'):
            device=Mock();device.read_input.return_value=b'\x08\x50';device.get_feature_report.return_value=b'\x08\x50'
            factory=MagicMock();factory.return_value.__enter__.return_value=device
            iface=SimpleNamespace(vid=0x9999,usage_page=0xFF01,feature_len=64,output_len=64,path='original')
            env=dict(bt=bt,HidDevice=factory,state={'cal_ifaces':[iface]},cb_iface=Mock(),
                txt_cal=Mock(),append_text=Mock(),set_text=Mock(),run_bg=Mock())
            env['cb_iface'].current.return_value=0
            values=dict(name='test',kind=mode,rid='08',req='04',len='64',off='1',wait='0',scale='1',chg='')
            for key,value in values.items():env['v_'+key]=Mock(get=Mock(return_value=value))
            exec(compile(ast.Module(body=nodes,type_ignores=[]),'calibration callbacks','exec'),env)
            env['test_cal']()
            worker=env['run_bg'].call_args.args[0]
            env['v_kind'].get.return_value='feature' if mode=='output' else 'output'
            env['cb_iface'].current.side_effect=AssertionError('worker accessed UI')
            env['state']['cal_ifaces']=[]
            lines=worker()
            self.assertFalse(any('出错' in s for s in lines))
            factory.assert_called_once_with('original',write=True)
            self.assertEqual(device.write_output.call_count,2 if mode=='output' else 0)
            self.assertEqual(device.get_feature_report.call_count,2 if mode=='feature' else 0)
            env['cb_iface'].current.side_effect=None;env['state']['cal_ifaces']=[iface];env['v_kind'].get.return_value=mode
            spec=env['build_spec']()
            with tempfile.TemporaryDirectory() as temp,patch.object(bt,'USER_PROTOCOL_FILE',str(Path(temp)/'protocols.json')):
                bt.save_user_protocol(spec)
                self.assertEqual(bt.load_user_protocols()[0].mode,mode)

    def test_master_codec_identity_and_corruption(self):
        self.assertEqual(ms.atk_encode(1000,1000,'master').hex(),'e703e7030081')
        self.assertEqual(ms.atk_encode(40000,40000,'master').hex(),'1f4e1f4e116a')
        for x,y in ((1,1),(800,1600),(39999,40000),(50000,50000)):
            self.assertEqual(ms.atk_decode(ms.atk_encode(x,y,'master'),'master'),(x,y))
        for value in (0,40001,50001,True):
            with self.assertRaises(ValueError):ms.atk_encode(value,value,'master')
        for raw in (bytes(6),bytes.fromhex('e703e703027f')):
            with self.assertRaises(ValueError):ms.atk_decode(raw,'master')
        d=a9();self.assertEqual(ms.atk_model((1,53),d)[1],'master')
        d.pid=0x127E;self.assertIsNone(ms.atk_model((1,53),d))

    def master_state(self):
        d=a9();mouse=ms.Mouse(d.path,'ATK A9 Ultimate +',d.vid_pid,'atk',(1,53),interface=d)
        rows=tuple(ms.atk_encode(v,v,'master') for v in (400,800,1600,3200,6400,12800,24000,40000))
        header=bytes.fromhex('015404510154')
        return d,ms.DpiState(mouse,[(v,v) for v in (400,800,1600,3200)],1,1,50000,1,
                            protocol_data=(header,rows))

    def test_master_read_uses_six_byte_high_address(self):
        d,state=self.master_state();header,rows=state.protocol_data
        with patch.object(ms.hw,'enumerate_hid',return_value=[d]),patch.object(ms.hw,'HidDevice'),\
             patch.object(ms,'atk_identity',return_value=(1,53)),patch.object(ms,'atk_profile',return_value=1),\
             patch.object(ms,'atk_read',side_effect=[header,*rows,header]) as read:
            result=ms.read(state.mouse)
        self.assertEqual(result,state)
        self.assertEqual([c.args[2:] for c in read.call_args_list],[(0,6),*[(0x1B00+i*6,6) for i in range(8)],(0,6)])

    def test_master_write_readback_and_inactive_preservation(self):
        d,state=self.master_state();after=copy.deepcopy(state);after.stages[0]=(1000,1000)
        rows=list(after.protocol_data[1]);rows[0]=ms.atk_encode(1000,1000,'master')
        after.protocol_data=(after.protocol_data[0],tuple(rows))
        with patch.object(ms,'_read',side_effect=[state,after]),patch.object(ms.hw,'enumerate_hid',return_value=[d]),\
             patch.object(ms.hw,'HidDevice') as factory:
            self.assertEqual(ms.apply(state,0,1000,False),after)
            packets=[c.args[0] for c in factory.return_value.__enter__.return_value.write_output.call_args_list]
        self.assertEqual(len(packets),1);self.assertEqual(packets[0][1:6],bytes.fromhex('07001b0006'))
        self.assertEqual(sum(packets[0])&255,0x55)
        rows[-1]=bytes(6);after.protocol_data=(after.protocol_data[0],tuple(rows))
        with patch.object(ms,'_read',side_effect=[state,after]),patch.object(ms.hw,'enumerate_hid',return_value=[d]),patch.object(ms.hw,'HidDevice'):
            with self.assertRaisesRegex(RuntimeError,'其他档位'):ms.apply(state,0,1000,False)
        with patch.object(ms,'_read') as read:
            with self.assertRaises(ValueError):ms.apply(state,0,40001,False)
            read.assert_not_called()

    def test_hp_logged_identity_and_g4_collection(self):
        online=bytes.fromhex('130701000101000000000000000000000000001d')
        identity=bytes.fromhex('130501000a03000000033c010000000000000066')
        dev=FakeDevice([online,identity,k.frame(0x4a,b'\x50\0'),online,identity])
        with patch.object(k,'report_ids',return_value={19}):
            self.assertEqual(mk.read(dev,keyboard())[0],(80,'discharging',mk.HP_GT98_NAME))
        d=SimpleNamespace(vid=0x35BB,pid=0xA22D,usage_page=0xFF02,usage=2,input_len=64,output_len=64,path='hid#mi_03&col02')
        self.assertTrue(ld.matches(d,'hecate_g4'));self.assertFalse(ld.matches(d,'hecate_g5'))
        d.path='hid#mi_03&col03';self.assertFalse(ld.matches(d,'hecate_g4'))


if __name__=='__main__':unittest.main()
