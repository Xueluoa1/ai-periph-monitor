"""Hidden UI interaction tests: edits stage values, never send device commands."""
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

import ui_setting_fields as fields


class SettingFieldTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def test_color_dialog_cancel_accept_disable_and_trace_cleanup(self):
        value=tk.StringVar(value='#FF6E00');picker=fields.ColorPicker(self.root,value)
        with patch.object(fields.colorchooser,'askcolor',return_value=(None,None)):
            picker.choose();self.assertEqual(value.get(),'#FF6E00')
        with patch.object(fields.colorchooser,'askcolor',return_value=((1,2,3),'#010203')) as dialog:
            picker.choose();self.assertEqual(value.get(),'#010203')
            picker.choose_button.state(['disabled']);picker.choose();dialog.assert_called_once()
        value.set('bad');picker.refresh()  # invalid typing must not crash preview
        picker.destroy();self.assertEqual(value.trace_info(),[])

    def test_palette_add_edit_remove_clear_and_limit(self):
        value=tk.StringVar(value='');picker=fields.PalettePicker(self.root,value)
        with patch.object(fields.colorchooser,'askcolor',return_value=((255,0,0),'#ff0000')):
            picker.add();picker.add()
        self.assertEqual(value.get(),'#FF0000,#FF0000')
        with patch.object(fields.colorchooser,'askcolor',return_value=((0,255,0),'#00ff00')):
            picker.edit(0)
        picker.remove(1);self.assertEqual(value.get(),'#00FF00')
        picker.clear_button.invoke();self.assertEqual(value.get(),'')
        value.set(','.join(['#FF0000']*8))
        with patch.object(fields.colorchooser,'askcolor') as dialog:
            picker.add();dialog.assert_not_called()
        picker.add_button.state(['disabled'])
        with patch.object(fields.colorchooser,'askcolor') as dialog:
            picker.edit(0);picker.remove(0);dialog.assert_not_called()
            self.assertEqual(len(picker.colors()),8)

    def test_integer_slider_rounds_syncs_bounds_and_handles_invalid_entry(self):
        value=tk.StringVar(value='37');slider=fields.IntegerSlider(self.root,value,1,100)
        self.assertEqual(slider.position.get(),37)
        slider.slid('44.7');self.assertEqual(value.get(),'45')
        value.set('100');slider.step(1);self.assertEqual(value.get(),'100')
        slider.endpoint(1);slider.step(-1);self.assertEqual(value.get(),'1')
        value.set('');self.assertEqual(slider.position.get(),1)
        value.set('101');self.assertEqual(value.get(),'101')  # caller validates, no silent clamp
        value.set('52');self.assertEqual(slider.position.get(),52)
        slider.scale.state(['disabled']);slider.slid('77');slider.step(1)
        self.assertEqual(value.get(),'52')
        slider.destroy();self.assertEqual(value.trace_info(),[])

    def test_page_disables_nested_sliders_and_color_buttons_without_apply(self):
        from verify_mouse_controls import ControlTests
        from ui_mouse_pages import MouseSettingsPage
        fixture=ControlTests();fixture.setUp()
        page=MouseSettingsPage(self.root);page.render(fixture.dpi);page.render_controls(fixture.state)
        def walk(parent):
            for child in parent.winfo_children():
                yield child
                yield from walk(child)
        slider=next(w for w in walk(page.extra) if isinstance(w,fields.IntegerSlider))
        color=next(w for w in walk(page.extra) if isinstance(w,fields.ColorPicker))
        with patch.object(page,'apply_controls') as apply:
            slider.slid('25.2')
            with patch.object(fields.colorchooser,'askcolor',return_value=((0,0,255),'#0000ff')):color.choose()
            apply.assert_not_called()
            self.assertEqual((page.light_brightness.get(),page.light_color.get()),('25','#0000FF'))
            page.set_busy(True)
            self.assertTrue(slider.scale.instate(['disabled']))
            self.assertTrue(color.choose_button.instate(['disabled']))
            page.set_busy(False);self.assertTrue(slider.scale.instate(['!disabled']))
            page.apply_light();apply.assert_called_once()


if __name__=='__main__':unittest.main()
