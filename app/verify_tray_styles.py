"""Style selection, native cache, persistence and live tray update regressions."""
import copy
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

import tray_common as tc
import tray_app as tray
import tray_pixels
import win_icon
import ui_theme as ui
from tray_styles import STYLES
from ui_tray_style import TrayStyleSettings


class StyleTests(unittest.TestCase):
    def test_render_all_styles_at_small_sizes_and_states(self):
        for size in (16, 24, 32):
            variants = set()
            for style in STYLES:
                for light in (False, True):
                    for value in ('0', '8', '48', '100', '--'):
                        raw = tray_pixels.render(value, size, style=style, light=light, transport='usb')
                        self.assertEqual(len(raw), size*size*4)
                        self.assertTrue(any(raw[3::4]))
                        self.assertIn(0, raw[3::4])
                        variants.add(raw)
                if style != 'classic':
                    normal = tray_pixels.render('76', size, style=style, transport='usb')
                    charging = tray_pixels.render('76', size, style=style, transport='usb', state='charging')
                    self.assertNotEqual(normal, charging)
            self.assertEqual(len(variants), len(STYLES)*10)
        for invalid in ('bad-style', None, []):
            self.assertEqual(tray_pixels.render('48', 24, style=invalid), tray_pixels.render('48', 24))

    def test_style_persists_and_old_config_defaults(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(tc, 'CONFIG_PATH', str(Path(folder)/'config.json')):
            self.assertEqual(tc.load_config()['taskbar_icon_style'], 'classic')
            cfg = tc.load_config()
            cfg.update(taskbar_icon_style='ring', taskbar_primary='BT:test')
            tc.save_config(cfg)
            loaded = tc.load_config()
            self.assertEqual(loaded['taskbar_icon_style'], 'ring')
            self.assertEqual(loaded['taskbar_primary'], 'BT:test')

    def test_native_handles_include_style_and_charging(self):
        win_icon.destroy_all()
        try:
            with patch.object(win_icon, 'system_uses_light_taskbar', return_value=False):
                handles = [win_icon.make_hicon('48', (0, 0, 0), style=style) for style in STYLES]
                self.assertTrue(all(handles))
                self.assertEqual(len(set(handles)), len(STYLES))
                self.assertEqual(handles[2], win_icon.make_hicon('48', (0, 0, 0), style='ring'))
                self.assertNotIn(win_icon.make_hicon('48', (0, 0, 0), style='ring', state='charging'), handles)
        finally:
            win_icon.destroy_all()

    def test_reload_style_updates_icons_without_device_query(self):
        app = tray.TrayApp.__new__(tray.TrayApp)
        app.cfg = copy.deepcopy(tc.DEFAULT_CONFIG)
        app.update_icons, app.hide_hover, app.refresh_now = Mock(), Mock(), Mock()
        new = dict(app.cfg, taskbar_icon_style='ring')
        with patch.object(tc, 'load_config', return_value=new), patch.object(tc, 'sync_widget_state'):
            app.apply_config()
        app.update_icons.assert_called_once()
        app.refresh_now.assert_not_called()
        app.hwnd, app.active_uids = 1, set()
        with patch.object(win_icon, 'make_hicon', return_value=123) as icon, \
             patch.object(tray.SHELL32, 'Shell_NotifyIconW', return_value=True):
            for merge in (False, True):
                app.cfg['taskbar_merge'] = merge
                app._apply_icon(tray.NIM_MODIFY, 1, '鼠标', 48, '', False, 'usb')
                self.assertEqual(icon.call_args.kwargs['style'], 'ring')

    def test_preview_selection_and_reopen_do_not_write_on_initialization(self):
        root = tk.Tk()
        root.withdraw()
        ui.configure(root)
        saved = Mock()
        try:
            panel = TrayStyleSettings(root, {'taskbar_icon_style': 'bar'}, saved)
            panel.pack(fill='x')
            root.update_idletasks()
            saved.assert_not_called()
            self.assertEqual(len(panel.images), 10)
            for index, style in enumerate(STYLES):
                panel.choice.current(index)
                panel.choice.event_generate('<<ComboboxSelected>>')
                root.update()
                saved.assert_called_with(style)
                self.assertEqual(panel.style_id, style)
                ui.apply_theme(root, 'dark')
                ui.apply_theme(root, 'light')
                self.assertEqual(len(panel.images), 10)
            panel.destroy()
            saved.reset_mock()
            panel = TrayStyleSettings(root, {'taskbar_icon_style': 'segments'}, saved)
            self.assertEqual(panel.value.get(), '分段电量条')
            saved.assert_not_called()
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()
