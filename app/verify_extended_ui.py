"""Hidden Tk controls: no discovery, device reads, or setting writes."""
from dataclasses import replace
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch
import dpi_adapters as da
import extended_controls as ec
from verify_extended_controls import fixture
from ui_mouse_pages import MouseSettingsPage
from ui_extended_controls import ExtendedEditor
from ui_setting_fields import IntegerSlider,ColorPicker


class ExtendedUITests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw();self.addCleanup(self.root.destroy)
        self.page=MouseSettingsPage(self.root)

    def widgets(self,parent,kind):
        result=[]
        for w in parent.winfo_children():
            if isinstance(w,kind):result.append(w)
            result.extend(self.widgets(w,kind))
        return result

    def test_dpi_to_controls_navigation_and_no_render_writes(self):
        config=fixture('rk');self.page.render(da.view(config.base.mouse,config))
        with patch.object(self.page,'run') as run,patch.object(ec,'read') as read:
            self.page.load_extended_controls();run.call_args.args[0]();read.assert_called_with(config.base.mouse)
        z=ec.Zone(1,'滚轮',0,((0,'常亮'),(1,'呼吸')),(255,0,0),50,100)
        with patch.object(ec,'apply') as apply:
            editor=ExtendedEditor(self.page,ec.State(config,1000,((125,0),(1000,3)),(z,)))
            editor.fields[1]['rgb'].set('#00FFFF');editor.fields[1]['brightness'].set('70');editor.rate.set('125')
            apply.assert_not_called()
        self.assertEqual(len(self.widgets(self.page.form,ColorPicker)),1)
        self.assertEqual(len(self.widgets(self.page.form,IntegerSlider)),1)

    def test_apply_uses_user_values_and_busy_blocks_actions(self):
        z=ec.Zone(1,'滚轮',0,((0,'常亮'),(1,'呼吸')),(255,0,0),50,100)
        state=ec.State(fixture('rog_more'),1000,((125,0),(1000,3)),(z,))
        editor=ExtendedEditor(self.page,state)
        with patch.object(self.page,'run') as run:
            self.page.set_busy(True);editor.apply_rate();editor.apply_zone(z);editor.load_dpi();run.assert_not_called()
            self.assertTrue(all(w.instate(['disabled']) for w in self.widgets(self.page.form,ttk.Combobox)))
            self.page.set_busy(False)
            editor.fields[1]['rgb'].set('#112233');editor.fields[1]['brightness'].set('80')
            editor.apply_zone(z)
            with patch.object(ec,'apply') as apply:
                run.call_args.args[0]()
                self.assertEqual(apply.call_args.kwargs['zone'],replace(z,rgb=(17,34,51),brightness=80))

    def test_write_only_remains_unknown_and_reports_ack_honestly(self):
        z=ec.Zone(0,'RGB',-1,((0,'关闭'),(1,'常亮')),(-1,-1,-1),speed=-1,speeds=(1000,5000),
                  aux=b'\x0c\x00\x00\x01\x01',write_only=True,channel=0x8071)
        state=ec.State(fixture('logitech'),zones=(z,))
        editor=ExtendedEditor(self.page,state)
        self.assertEqual(editor.fields[0]['rgb'].get(),'')
        labels=[str(w.cget('text')) for w in self.widgets(self.page.form,tk.Label)]
        self.assertTrue(any('仅能设置' in text for text in labels))
        with patch.object(self.page,'run') as run:
            editor.apply_zone(z);run.assert_not_called()
            editor.fields[0]['mode'].set('关闭');editor.apply_zone(z)
            run.call_args.args[1](state)
        self.assertIn('不提供读回',self.page.message.cget('text'))
        self.assertEqual(self.page.extended_editor.state.zones[0].mode,-1)


if __name__=='__main__':unittest.main()
