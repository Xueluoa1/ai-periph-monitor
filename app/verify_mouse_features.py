"""Protocol fixtures and native lifecycle checks; never write physical mouse settings."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import mouse_settings as ms
from input_test import Metrics, InputSession


class MouseTests(unittest.TestCase):
    def setUp(self):
        self.iface=SimpleNamespace(path='test',vid=0x0B05,pid=0x1A94,output_len=65)
        self.mouse=ms.Mouse('test','ROG test','0B05:1A94','rog',(0x1A94,0))
        self.state=ms.DpiState(self.mouse,[(400,400),(800,800),(1600,1600),(3200,3200)],3,50,36000,50,
                               colors=[bytes.fromhex(x) for x in ('ff0000','c100ff','003dff','31ff00')])

    def test_atk_known_encoding_and_rejection(self):
        self.assertEqual(ms.atk_encode(800,800,'step50'),bytes.fromhex('0f0f0037'))
        self.assertEqual(ms.atk_encode(800,800,'ultra'),bytes.fromhex('4f4f00b7'))
        for family,values in [('step50',(50,800,30000,36000)),('ultra',(10,800,10000,10050,30000,42000))]:
            for dpi in values:self.assertEqual(ms.atk_decode(ms.atk_encode(dpi,dpi,family),family),(dpi,dpi))
        with self.assertRaises(ValueError):ms.atk_encode(801,801,'step50')
        with self.assertRaises(ValueError):ms.atk_decode(bytes.fromhex('0f0f0036'),'step50')
        self.assertEqual(ms.atk_frame(8,(0,0,12,4)).hex(),'080800000c040000000000000000000035')

    def test_read_real_rog_fixture(self):
        replies=[bytes.fromhex('00120000000200070005070004ffff09'),
                 bytes.fromhex('0012040200070007000f000f001f001f003f003f00'),
                 bytes.fromhex('0012040300ff0000c100ff003dff31ff00')]
        with patch.object(ms.hw,'enumerate_hid',return_value=[self.iface]),patch.object(ms.hw,'HidDevice'),patch.object(ms,'rog_query',side_effect=replies):
            state=ms.read(self.mouse)
        self.assertEqual(state.stages,self.state.stages)
        self.assertEqual((state.active,state.profile),(3,1))
        self.assertEqual(state.colors,self.state.colors)

    def test_empty_receiver_profile_stops_before_dpi_query(self):
        profile=bytes.fromhex('00120000000200070000000000')
        with patch.object(ms.hw,'enumerate_hid',return_value=[self.iface]),patch.object(ms.hw,'HidDevice'),patch.object(ms,'rog_query',return_value=profile) as query:
            with self.assertRaisesRegex(OSError,'无法读取鼠标配置'):ms.read(self.mouse)
            self.assertEqual(query.call_count,1)

    def test_rog_device_error_is_not_reported_as_timeout(self):
        device=Mock();device.read_input.return_value=bytes.fromhex('00ffaa00')
        with self.assertRaisesRegex(OSError,'FF AA'):ms.rog_query(device,self.iface,0,(0x12,4,2))
        self.assertEqual(device.write_output.call_count,1)

    def test_invalid_and_stale_values_never_write(self):
        with patch.object(ms.hw,'HidDevice') as device:
            for value in (0,49,801,36050,True):
                with self.assertRaises(ValueError):ms.apply(self.state,0,value,False)
            changed=copy.deepcopy(self.state);changed.active=0
            with patch.object(ms,'_read',return_value=changed):
                with self.assertRaisesRegex(ValueError,'已变化'):ms.apply(self.state,0,1000,False)
            device.assert_not_called()

    def test_write_preserves_colors_and_other_stages(self):
        confirmed=copy.deepcopy(self.state);confirmed.stages[1]=(1000,1000);confirmed.active=1
        with patch.object(ms,'_read',side_effect=[self.state,confirmed]),patch.object(ms.hw,'enumerate_hid',return_value=[self.iface]),patch.object(ms.hw,'HidDevice'),patch.object(ms,'rog_query') as send:
            self.assertIs(ms.apply(self.state,1,1000,True),confirmed)
            packets=[bytes(call.args[3]) for call in send.call_args_list]
        self.assertEqual(packets,[bytes.fromhex('513101001300c100ff'),bytes.fromhex('5131090002'),bytes.fromhex('5003')])

    def test_no_change_does_not_write(self):
        with patch.object(ms,'_read',return_value=self.state),patch.object(ms.hw,'enumerate_hid',return_value=[self.iface]),patch.object(ms.hw,'HidDevice'),patch.object(ms,'rog_query') as send:
            ms.apply(self.state,0,400,False)
            send.assert_not_called()

    def test_switch_only_preserves_xy_and_sends_no_dpi_packet(self):
        self.state.stages[0]=(400,800)
        confirmed=copy.deepcopy(self.state);confirmed.active=0
        with patch.object(ms,'_read',side_effect=[self.state,confirmed]),patch.object(ms.hw,'enumerate_hid',return_value=[self.iface]),patch.object(ms.hw,'HidDevice'),patch.object(ms,'rog_query') as send:
            self.assertIs(ms.apply(self.state,0,None,True),confirmed)
            self.assertEqual([bytes(call.args[3]) for call in send.call_args_list],[bytes.fromhex('5131090001'),bytes.fromhex('5003')])

    def test_edit_inactive_stage_does_not_activate_it(self):
        confirmed=copy.deepcopy(self.state);confirmed.stages[0]=(1000,1000)
        with patch.object(ms,'_read',side_effect=[self.state,confirmed]),patch.object(ms.hw,'enumerate_hid',return_value=[self.iface]),patch.object(ms.hw,'HidDevice'),patch.object(ms,'rog_query') as send:
            result=ms.apply(self.state,0,1000,False)
            self.assertEqual(result.active,3)
            self.assertEqual([bytes(call.args[3]) for call in send.call_args_list],[bytes.fromhex('513100001300ff0000'),bytes.fromhex('5003')])

    def test_failed_readback_reported(self):
        with patch.object(ms,'_read',return_value=self.state),patch.object(ms.hw,'enumerate_hid',return_value=[self.iface]),patch.object(ms.hw,'HidDevice'),patch.object(ms,'rog_query'):
            with self.assertRaisesRegex(RuntimeError,'部分改动'):ms.apply(self.state,0,1000,False)

    def test_disconnected_device_and_changed_pairing(self):
        with patch.object(ms.hw,'enumerate_hid',return_value=[]):
            with self.assertRaisesRegex(OSError,'断开'):ms.read(self.mouse)
        self.iface.pid=0x1ACE
        with patch.object(ms.hw,'enumerate_hid',return_value=[self.iface]),patch.object(ms,'omni_identity',return_value={}),patch.object(ms.hw,'HidDevice') as device:
            with self.assertRaisesRegex(ValueError,'配对'):ms.read(self.mouse)
            device.assert_not_called()

    def test_same_model_receivers_remain_separate(self):
        interfaces=[SimpleNamespace(path=f'hid#vid_373b&pid_1216&mi_02#device{i}',vid=0x373B,pid=0x1216,vid_pid='373B:1216',usage_page=0xFF02,usage=2,output_len=17,input_len=17,product='ATK') for i in (1,2)]
        with patch.object(ms.hw,'enumerate_hid',return_value=interfaces),patch.object(ms.hw,'HidDevice'),patch.object(ms,'atk_identity',return_value=(1,8)):
            found=ms.discover()
        self.assertEqual(len(found),2)
        self.assertNotEqual(found[0].path,found[1].path)

    def test_unknown_atk_identity_not_writable(self):
        self.iface.__dict__.update(vid=0x373B,usage_page=0xFF02,usage=2,output_len=17,input_len=17,vid_pid='373B:1216',product='ATK')
        with patch.object(ms.hw,'enumerate_hid',return_value=[self.iface]),patch.object(ms.hw,'HidDevice'),patch.object(ms,'atk_identity',return_value=(255,255)):
            mouse=ms.discover()[0]
        self.assertFalse(mouse.protocol)
        with self.assertRaises(ValueError):ms.read(mouse)

    def test_metrics_repeat_release_wheel_and_separate_mice(self):
        m=Metrics()
        m.keyboard('keyboard',0x1E,0,65);m.keyboard('keyboard',0x1E,0,65)
        self.assertEqual((m.peak_keys,len(m.seen)),(1,1))
        m.keyboard('keyboard',0x30,0,66);m.keyboard('keyboard',0x1E,1,65)
        self.assertEqual(m.peak_keys,2)
        m.mouse('first',1,0,1,1,1.1);m.mouse('first',2,0,1,1,1.2)
        m.mouse('second',0x400,120,1,1,1.3)
        s=m.snapshot(1.5)
        self.assertEqual(s['rates'],{'first':2,'second':1})
        self.assertEqual((s['wheel'],s['clicks']['左键']),(1,1))
        self.assertNotIn('左键',s['pressed'])
        self.assertEqual(m.snapshot(2.5)['rates'],{'first':0,'second':0})

    def test_raw_input_native_start_stop_twice(self):
        for _ in range(2):
            session=InputSession(0)  # No foreground window matches; input isn't collected.
            try:
                self.assertTrue(session.ready.wait(3))
                self.assertFalse(session.error,session.error)
                self.assertTrue(session.hwnd)
            finally:
                session.stop();session.thread.join(3)
            self.assertFalse(session.thread.is_alive())
            self.assertIsNone(session.hwnd)


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(MouseTests))
    report={'test_groups':result.testsRun,'passed':result.wasSuccessful(),'physical_dpi_writes':'not performed','atk_hardware_validation':'not available'}
    (Path(__file__).resolve().parent.parent/'release'/'mouse-verification-v17.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    raise SystemExit(not result.wasSuccessful())
