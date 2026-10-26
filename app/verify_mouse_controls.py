"""Captured read replies and simulated writes. Never changes physical settings."""
from dataclasses import replace
from types import SimpleNamespace
import copy
import tkinter as tk
import unittest
from unittest.mock import patch

import mouse_settings as ms
import mouse_controls as mc


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.iface=SimpleNamespace(path='test',vid=0x0B05,pid=0x1ACE,usage_page=0xFF01,usage=1,output_len=64)
        self.mouse=ms.Mouse('test','Harpe','0B05:1ACE','rog',(0x1A94,3),interface=self.iface)
        self.dpi=ms.DpiState(self.mouse,[(400,400),(800,800),(1600,1600),(3200,3200)],3,50,36000,50)
        self.settings=bytes.fromhex('0312040000ffffffffffffffff03000200000000')
        self.lighting=bytes.fromhex('03120300000264ff6e000000')
        rate,light,other=mc.decode_rog(self.settings,self.lighting)
        self.state=mc.Controls(self.dpi,rate,light,other)

    def test_captured_rog_has_one_scrollwheel(self):
        self.assertEqual(self.state.rate,1000)
        self.assertEqual(self.state.light,mc.Light(2,100,(255,110,0)))
        with patch.object(mc,'_interface',return_value=self.iface),patch.object(ms,'_read',return_value=self.dpi),patch.object(ms.hw,'HidDevice'),patch.object(ms,'rog_query',side_effect=[self.settings,self.lighting]) as query:
            self.assertEqual(mc.read(self.mouse),self.state)
            self.assertEqual([c.args[3] for c in query.call_args_list],[(0x12,4,0),(0x12,3,0)])

    def test_rog_rate_packet_save_and_readback(self):
        after=replace(self.state,rate=500)
        with patch.object(mc,'_read',side_effect=[self.state,after]),patch.object(mc,'_interface',return_value=self.iface),patch.object(ms.hw,'HidDevice'),patch.object(ms,'rog_query') as query:
            self.assertEqual(mc.apply(self.state,rate=500),after)
            self.assertEqual([c.args[3] for c in query.call_args_list],[(0x51,0x31,4,0,2),(0x50,3)])

    def test_rog_light_uses_write_zone_one_and_off_f0(self):
        light=mc.Light(0xF0,50,(255,0,0));after=replace(self.state,light=light)
        with patch.object(mc,'_read',side_effect=[self.state,after]),patch.object(mc,'_interface',return_value=self.iface),patch.object(ms.hw,'HidDevice'),patch.object(ms,'rog_query') as query:
            mc.apply(self.state,light=light)
            self.assertEqual(query.call_args_list[0].args[3],(0x51,0x28,1,0,0xF0,50,255,0,0,0,0,0))

    def test_no_change_has_no_writes(self):
        with patch.object(mc,'_read',return_value=self.state),patch.object(ms.hw,'HidDevice') as device:
            mc.apply(self.state,rate=1000)
            device.assert_not_called()

    def test_invalid_inputs_rejected_before_io(self):
        for changes in [dict(rate=8000),dict(rate=True),dict(light=mc.Light(255,100,(1,2,3))),dict(light=mc.Light(0,101,(1,2,3))),dict(light=mc.Light(0,50,(-1,2,3)))]:
            with self.subTest(changes=changes),patch.object(mc,'_read') as read:
                with self.assertRaises(ValueError):mc.apply(self.state,**changes)
                read.assert_not_called()

    def test_stale_dpi_and_settings_block_before_write(self):
        changed=copy.deepcopy(self.state);changed.dpi.active=0
        for current in [changed,replace(self.state,rate=500),replace(self.state,other_settings=b'changed')]:
            with patch.object(mc,'_read',return_value=current),patch.object(ms.hw,'HidDevice') as device:
                with self.assertRaisesRegex(ValueError,'已变化'):mc.apply(self.state,rate=250)
                device.assert_not_called()

    def test_readback_mismatch_and_unrelated_change_fail(self):
        for after in [self.state,replace(self.state,rate=500,other_settings=b'changed')]:
            with patch.object(mc,'_read',side_effect=[self.state,after]),patch.object(mc,'_interface',return_value=self.iface),patch.object(ms.hw,'HidDevice'),patch.object(ms,'rog_query'):
                with self.assertRaisesRegex(RuntimeError,'不一致'):mc.apply(self.state,rate=500)

    def test_timeout_never_retries_write_or_reports_success(self):
        with patch.object(mc,'_read',return_value=self.state),patch.object(mc,'_interface',return_value=self.iface),patch.object(ms.hw,'HidDevice'),patch.object(ms,'rog_query',side_effect=TimeoutError('timeout')) as query:
            with self.assertRaisesRegex(RuntimeError,'部分改动可能已生效'):mc.apply(self.state,rate=500)
            self.assertEqual(query.call_count,1)

    def test_unknown_models_and_changed_interfaces_rejected(self):
        self.mouse.identity=(0xffff,3)
        with self.assertRaises(ValueError):mc.read(self.mouse)
        self.mouse.identity=(0x1A94,3);self.iface.pid=0xffff
        with patch.object(ms.hw,'enumerate_hid',return_value=[self.iface]):
            with self.assertRaises(ValueError):mc.read(self.mouse)

    def test_atk_captured_system_and_two_byte_write(self):
        iface=SimpleNamespace(path='atk',vid=0x373B,pid=0x101A,usage_page=0xFF02,usage=2,output_len=17)
        mouse=ms.Mouse('atk','X1 Ultra','373B:101A','atk',(2,43),interface=iface)
        dpi=replace(self.dpi,mouse=mouse)
        with patch.object(mc,'_interface',return_value=iface),patch.object(ms,'_read',return_value=dpi),patch.object(ms.hw,'HidDevice'),patch.object(ms,'atk_read',return_value=bytes.fromhex('01540451035200550055')):
            before=mc.read(mouse)
        self.assertEqual(before.rate,1000);self.assertIsNone(before.light)
        after=replace(before,rate=125)
        with patch.object(mc,'_read',side_effect=[before,after]),patch.object(mc,'_interface',return_value=iface),patch.object(ms.hw,'HidDevice') as device:
            mc.apply(before,rate=125)
            device.return_value.__enter__.return_value.write_output.assert_called_once_with(ms.atk_frame(7,(0,0,0,2,8,0x4d)),use_hidd=False)
        iface.pid=0x101B
        self.assertFalse(mc.supported(mouse))

    def test_hidden_ui_render_busy_validation_and_submit(self):
        import ui_mouse_pages as pages
        root=tk.Tk();root.withdraw()
        try:
            page=pages.MouseSettingsPage(root);page.render(self.dpi);page.render_controls(self.state)
            self.assertEqual(page.rate_value.get(),'1000')
            page.set_busy(True)
            def walk(w):
                for child in w.winfo_children():
                    yield child
                    yield from walk(child)
            combos=[w for w in walk(page.extra) if isinstance(w,pages.ttk.Combobox)]
            self.assertTrue(all(w.instate(['disabled']) for w in combos))
            page.set_busy(False)
            self.assertTrue(all(w.instate(['readonly','!disabled']) for w in combos))
            with patch.object(page,'run') as run:
                page.light_color.set('bad');page.apply_light();run.assert_not_called()
                page.light_color.set('#FF6E00');page.apply_light();run.assert_called_once()
            page.render(self.dpi)
            self.assertIsNone(page.control_snapshot)
        finally:root.destroy()


if __name__=='__main__':unittest.main()
