"""Hidden Tk K1 controls; no hardware access."""
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch
from dataclasses import replace
from ui_mouse_pages import MouseSettingsPage
from ui_k1_controls import K1Editor
from ui_setting_fields import IntegerSlider,ColorPicker
from verify_k1 import snapshot
import gravastar_k1 as k


class UITests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw();self.addCleanup(self.root.destroy)
        self.page=MouseSettingsPage(self.root)

    def widgets(self,parent,kind):
        return [w for child in parent.winfo_children() for w in ([child] if isinstance(child,kind) else [])+self.widgets(child,kind)]

    def test_render_and_selection_do_not_write(self):
        with patch.object(k,'apply') as apply:
            editor=K1Editor(self.page,snapshot());editor.mode.set('灯效 03');editor.fields();apply.assert_not_called()
        labels=[str(w.cget('text')) for w in self.widgets(self.page.form,tk.Label)]
        self.assertFalse(any('回报率' in t or 'DPI' in t for t in labels))
        self.assertEqual(len(self.widgets(self.page.form,IntegerSlider)),2)
        self.assertIsNone(editor.rgb)

    def test_apply_values_and_busy(self):
        editor=K1Editor(self.page,snapshot())
        self.assertEqual(len(self.widgets(self.page.form,ColorPicker)),1)
        with patch.object(self.page,'run') as run:
            self.page.set_busy(True);editor.apply();run.assert_not_called()
            self.assertTrue(all(w.instate(['disabled']) for w in self.widgets(self.page.form,ttk.Scale)))
            self.page.set_busy(False);editor.brightness.set('3');editor.speed.set('1');editor.rgb.set('#112233');editor.apply()
            with patch.object(k,'apply') as apply:
                run.call_args.args[0]();self.assertEqual(apply.call_args.args[1:],(2,3,1,(17,34,51)))

    def test_custom_mode_is_not_overwritten_by_render(self):
        s=snapshot();raw=bytearray(s.raw);raw[10]=21;raw[9]=1
        editor=K1Editor(self.page,replace(s,raw=bytes(raw)))
        with patch.object(self.page,'run') as run:editor.apply();run.assert_not_called()

    def test_k1_page_route(self):
        self.page.devices=[snapshot().mouse];self.page.combo.configure(values=['K1']);self.page.combo.current(0)
        with patch.object(self.page,'run') as run,patch.object(k,'read') as read:
            self.page.load_selected();run.call_args.args[0]();read.assert_called_once_with(snapshot().mouse)


if __name__=='__main__':unittest.main()
