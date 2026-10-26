import tkinter as tk
import unittest
from unittest.mock import Mock
import ui_switch_pixels as pixels
import ui_theme as ui


class SwitchRasterTests(unittest.TestCase):
    def test_edges_are_blended_at_common_scales_and_themes(self):
        for scale in (1, 1.25, 1.5, 2):
            for bg in ((255, 255, 255), (30, 30, 30)):
                for on in (False, True):
                    track = (0, 184, 138) if on else (88, 88, 88)
                    ppm = pixels.render(scale, on, bg, track)
                    header, dimensions, maximum, data = ppm.split(b'\n', 3)
                    width, height = map(int, dimensions.split())
                    self.assertEqual((width, height), (round(44*scale), round(40*scale)))
                    self.assertEqual(len(data), width*height*3)
                    colors = {tuple(data[i:i+3]) for i in range(0, len(data), 3)}
                    self.assertGreater(len(colors - {bg, track, (255, 255, 255)}), 5)
                    self.assertEqual(tuple(data[:3]), bg)
                    self.assertEqual(tuple(data[-3:]), bg)
                    x, y = round((33 if on else 13)*scale), round(20*scale)
                    self.assertEqual(tuple(data[(y*width+x)*3:(y*width+x)*3+3]), (255,255,255))

    def test_cache_reuses_pixels_and_keys_background(self):
        a = pixels.render(1, False, (20,20,20), (88,88,88))
        self.assertIs(a, pixels.render(1, False, (20,20,20), (88,88,88)))
        self.assertNotEqual(a, pixels.render(1, False, (255,255,255), (88,88,88)))

    def test_tk_image_trace_keyboard_and_cleanup(self):
        root = tk.Tk();root.withdraw()
        try:
            var = tk.BooleanVar(root, False);command = Mock()
            switch = ui.Switch(root, var, command)
            self.assertEqual(switch.type(switch.find_all()[0]), 'image')
            before = switch._image
            var.set(True)
            self.assertIsNot(switch._image, before)
            command.assert_not_called()
            self.assertEqual(switch.toggle(), 'break')
            self.assertFalse(var.get());command.assert_called_once()
            self.assertTrue(switch.bind('<space>'));self.assertTrue(switch.bind('<Return>'))
            self.assertEqual(switch.cget('takefocus'), '1')
            switch.configure(bg='#141414');switch.draw()
            self.assertEqual(switch._image.get(0,0), (20,20,20))
            switch.destroy()
            self.assertFalse(var.trace_info())
            var.set(True)
        finally:root.destroy()


if __name__ == '__main__':unittest.main()
