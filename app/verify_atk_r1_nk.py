"""Replay run 46d63078; all writes use mocks, never physical devices."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import atk_r1_nk as nk
import mouse_settings as ms
import mouse_controls as mc
import wireless_battery as wb
import battery as b

ROWS = ('09090043','12120031','2525000b','4b4b00bf')
COLORS = ('ff000056','00ff0056','0000ff56','ff00ff57')


class R1NkTests(unittest.TestCase):
    def setUp(self):
        self.iface = SimpleNamespace(path='r1-nk-fixture',vid=0x373B,pid=0x10C9,
            usage_page=0xFF02,usage=2,input_len=17,output_len=17,feature_len=0,
            vid_pid='373B:10C9',product='Nearlink Mouse Dongle',manufacturer='Compx')
        self.mouse = ms.Mouse(self.iface.path,nk.MODEL,self.iface.vid_pid,'atk',nk.IDENTITY,interface=self.iface)
        self.state = ms.DpiState(self.mouse,[(v,v) for v in (400,800,1600,3200)],1,200,18000,50,
                                 colors=[bytes.fromhex(c)[:3] for c in COLORS])

    def test_captured_identity_and_battery(self):
        self.assertEqual(nk.decode_identity(bytes.fromhex('08100000000202430000000000000000f6')),nk.IDENTITY)
        self.assertEqual(wb.decode_compx(bytes.fromhex('08040000000255000fa40000000000003f')),
                         (85,'discharging',None))
        self.assertIsNone(nk.decode_identity(bytes(17)))

    def test_battery_dynamic_name_and_missing_identity_fallback(self):
        response=((85,'discharging',None),bytes.fromhex('08040000000255000fa40000000000003f'))
        for identity in ((nk.IDENTITY,b''),None):
            with patch.object(wb.hw,'HidDevice'),patch.object(wb,'query',return_value=response),patch.object(wb,'_input',return_value=identity):
                result=wb.read(self.iface,b.BatteryResult)
            self.assertTrue(result.ok)
            self.assertEqual(result.level,85)
            if identity:self.assertEqual(b.resolve_full_model_name(self.iface,result),nk.MODEL)
            else:self.assertNotIn('model_name',result.extra)

    def test_unknown_keyboard_receiver_appears_without_guessed_queries(self):
        unknown=SimpleNamespace(**(vars(self.iface)|dict(vid=0x25A7,pid=0xFA70,feature_len=8,
            vid_pid='25A7:FA70',product='2.4G Wireless Receiver')))
        self.assertTrue(b._is_candidate_interface(unknown,[]))
        self.assertIn('RK-G98',b.resolve_full_model_name(unknown))
        self.assertIn('用户确认',b.resolve_full_model_name(unknown))
        with patch.object(b,'HidDevice') as device,patch.object(b,'strategy_vendor_protocol') as probe:
            result=b._read_battery_unlocked(unknown)
            device.assert_not_called();probe.assert_not_called()
        self.assertFalse(result[0].ok)
        self.assertIsNone(result[0].level)
        unknown.usage_page=1
        self.assertFalse(b.is_unidentified_logged_receiver(unknown))

    def test_hidden_ui_has_only_readonly_stage_colors(self):
        import tkinter as tk
        import ui_mouse_pages as pages
        root=tk.Tk();root.withdraw()
        try:
            page=pages.MouseSettingsPage(root)
            state=mc.Controls(self.state,1000,None,b'')
            with patch.object(mc,'apply') as apply,patch.object(ms,'apply') as dpi_apply:
                page.render(self.state);page.render_controls(state)
                apply.assert_not_called();dpi_apply.assert_not_called()
            def walk(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from walk(child)
            texts=[str(w.cget('text')) for w in walk(page.extra) if 'text' in w.keys()]
            self.assertIn('档位 2  #00FF00',texts)
            self.assertNotIn('应用灯光',texts)
            self.assertIn('应用回报率',texts)
        finally:root.destroy()

    def test_exact_receiver_and_identity_required(self):
        self.assertTrue(mc.supported(self.mouse))
        with patch.object(ms.hw,'enumerate_hid',return_value=[self.iface]),patch.object(ms.hw,'HidDevice'),patch.object(ms,'atk_identity',return_value=nk.IDENTITY):
            found=ms.discover()
        self.assertEqual((found[0].name,found[0].protocol),(nk.MODEL,'atk'))
        for change in ({'pid':0x10C8},{'output_len':20,'input_len':20},{'usage_page':0xFF04}):
            wrong=SimpleNamespace(**(vars(self.iface)|change))
            self.assertIsNone(ms.atk_model(nk.IDENTITY,wrong))
            self.assertFalse(mc.supported(replace(self.mouse,interface=wrong)))
        for length in (20,8):
            self.assertFalse(wb.candidate(SimpleNamespace(**(vars(self.iface)|dict(input_len=length,output_len=length)))))

    def test_golden_rows_and_full_supported_range(self):
        for raw,dpi in zip(ROWS,(400,800,1600,3200)):
            self.assertEqual(ms.atk_decode(bytes.fromhex(raw),'paw3395se'),(dpi,dpi))
            self.assertEqual(ms.atk_encode(dpi,dpi,'paw3395se').hex(),raw)
        for dpi in (*range(200,10001,50),*range(10100,18001,100)):
            self.assertEqual(nk.decode_axis(*nk.encode_axis(dpi)),dpi)
        for value in (None,'800',True,150,10050,18050):
            with self.assertRaises(ValueError):nk.encode_axis(value)
        for code,mode in ((7,0),(18,1),(18,2),(0,0),(236,0)):
            with self.assertRaises(ValueError):nk.decode_axis(code,mode)

    def test_read_captured_configuration(self):
        replies=[bytes.fromhex(s) for s in ('015404510154',*ROWS,*COLORS)]
        with patch.object(ms.hw,'enumerate_hid',return_value=[self.iface]),patch.object(ms.hw,'HidDevice'),patch.object(ms,'atk_identity',return_value=nk.IDENTITY),patch.object(ms,'atk_read',side_effect=replies),patch.object(ms,'atk_profile',return_value=1):
            result=ms.read(self.mouse)
        self.assertEqual(result,self.state)

    def test_read_rate_and_stored_colors_without_inventing_lighting(self):
        with patch.object(ms,'_read',return_value=self.state),patch.object(mc,'_interface',return_value=self.iface),patch.object(ms.hw,'HidDevice'),patch.object(ms,'atk_read',return_value=bytes.fromhex('01540451015400550055')),patch.object(ms,'atk_profile',return_value=1):
            result=mc.read(self.mouse)
        self.assertEqual(result.rate,1000)
        self.assertIsNone(result.light)
        self.assertEqual(result.dpi.colors,self.state.colors)

    def test_invalid_or_stale_configuration_never_writes(self):
        with patch.object(ms,'_read') as read:
            with self.assertRaises(ValueError):ms.apply(self.state,0,10050,False)
            read.assert_not_called()
        for changed in (replace(self.state,profile=2),replace(self.state,colors=[b'\0\0\0']*4)):
            with patch.object(ms,'_read',return_value=changed),patch.object(ms.hw,'HidDevice') as device:
                with self.assertRaises(ValueError):ms.apply(self.state,0,1000,False)
                device.assert_not_called()

    def test_dpi_write_is_read_back_with_colors_preserved(self):
        updated=replace(self.state,stages=[(800,800),*self.state.stages[1:]])
        with patch.object(ms,'_read',side_effect=[self.state,updated]),patch.object(ms.hw,'enumerate_hid',return_value=[self.iface]),patch.object(ms.hw,'HidDevice') as device,patch.object(ms.time,'sleep'):
            self.assertEqual(ms.apply(self.state,0,800,False),updated)
        device.return_value.__enter__.return_value.write_output.assert_called_once_with(
            ms.atk_frame(7,(0,0,12,4,*bytes.fromhex(ROWS[1]))),use_hidd=False)

    def test_polling_write_and_readback_failure(self):
        old=mc.Controls(self.state,1000,None,bytes.fromhex('0451015400550055'))
        new=replace(old,rate=500)
        for confirmed,success in ((new,True),(old,False)):
            with patch.object(mc,'_read',side_effect=[old,confirmed]),patch.object(mc,'_interface',return_value=self.iface),patch.object(ms.hw,'HidDevice') as device,patch.object(mc.time,'sleep'):
                if success:self.assertEqual(mc.apply(old,rate=500),new)
                else:
                    with self.assertRaises(RuntimeError):mc.apply(old,rate=500)
                device.return_value.__enter__.return_value.write_output.assert_called_once_with(
                    ms.atk_frame(7,(0,0,0,2,2,0x53)),use_hidd=False)
        with patch.object(mc,'_read') as read:
            with self.assertRaises(ValueError):mc.apply(old,rate=8000)
            with self.assertRaises(ValueError):mc.apply(old,light=mc.Light(0,50,(255,0,0)))
            read.assert_not_called()


if __name__=='__main__':unittest.main()
