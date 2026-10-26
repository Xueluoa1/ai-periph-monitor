"""Hidden Tk navigation tests; no physical device commands."""
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch
import mouse_settings as ms
import dpi_adapters as da
import peripheral_controls as pc
from ui_mouse_pages import MouseSettingsPage
from ui_peripheral_controls import PeripheralEditor


class DpiUITests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw();self.addCleanup(self.root.destroy)
        self.page=MouseSettingsPage(self.root)
    def buttons(self,parent):
        result=[]
        for w in parent.winfo_children():
            if isinstance(w,ttk.Button):result.append(w)
            result.extend(self.buttons(w))
        return result
    def test_extra_selection_offers_independent_reads_without_write(self):
        m=ms.Mouse('test','LAMZU','373E:001C','extra',(0x373E,0x1C))
        self.page.devices=[m];self.page.combo.configure(values=['LAMZU']);self.page.combo.current(0)
        with patch.object(ms,'read') as read,patch.object(pc,'apply') as apply:
            self.page.load_selected();read.assert_not_called();apply.assert_not_called()
            buttons=self.buttons(self.page.form);self.assertEqual([b.cget('text') for b in buttons],['读取 DPI 档位','读取回报率与灯光'])
            with patch.object(self.page,'run') as run:
                buttons[0].invoke();run.call_args.args[0]();read.assert_called_once_with(m)
    def test_no_active_index_does_not_mark_first_current_or_offer_switch(self):
        m=ms.Mouse('test','Endgame','','dpi',(0x3367,0x1972));base=pc.State(m,'Endgame',None,(),0,m.identity)
        c=da.Config(base,'egg',((800,800),(1600,1600)), -1,50,26000,50,note='当前档位未上报',can_activate=False)
        with patch.object(ms,'apply') as apply:
            self.page.render(da.view(m,c));apply.assert_not_called()
            self.assertFalse(self.page.apply_button.winfo_manager())
            self.page.edit_stage(1)
            with patch.object(self.page,'run') as run:
                self.page.editors[1][1].set('2400');self.page.confirm_edit(1);run.call_args.args[0]()
            self.assertEqual(apply.call_args.args[1:],(1,2400,False))
    def test_controls_to_dpi_navigation_and_busy(self):
        m=ms.Mouse('test','LAMZU','','extra',(0x373E,0x1C));state=pc.State(m,'LAMZU',1000,(125,1000),1,m.identity)
        editor=PeripheralEditor(self.page,state)
        with patch.object(self.page,'run') as run:
            self.page.set_busy(True);editor.load_dpi();run.assert_not_called()
            self.page.set_busy(False);editor.load_dpi();self.assertEqual(run.call_args.args[1],self.page.render)
        c=da.Config(state,'page',((800,800),),0,50,30000,50)
        self.page.render(da.view(m,c))
        self.assertIn('读取回报率与灯光',[b.cget('text') for b in self.buttons(self.page.extra)])


if __name__=='__main__':unittest.main()
