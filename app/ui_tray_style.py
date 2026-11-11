"""Settings selector with the exact native tray renderer, no imaging dependency."""
import struct
import zlib
import tkinter as tk
from tkinter import ttk
import ui_theme as ui
from tray_styles import STYLES, normalize_style
from tray_pixels import render


def rgba_png(pixels, size):
    def chunk(kind, data):
        return (struct.pack('>I', len(data))+kind+data+
                struct.pack('>I', zlib.crc32(kind+data)))
    rows = b''.join(b'\0'+pixels[y*size*4:(y+1)*size*4] for y in range(size))
    return (b'\x89PNG\r\n\x1a\n'+
            chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0))+
            chunk(b'IDAT', zlib.compress(rows))+chunk(b'IEND', b''))


class TrayStyleSettings(ttk.Frame):
    def __init__(self, parent, cfg, on_save):
        super().__init__(parent, padding=(0, 10))
        self.on_save = on_save
        self.style_id = normalize_style(cfg.get('taskbar_icon_style'))
        self.value = tk.StringVar(self, STYLES[self.style_id])
        row = ttk.Frame(self)
        row.pack(fill='x')
        ttk.Label(row, text='任务栏图标样式').pack(side='left')
        self.choice = ttk.Combobox(row, textvariable=self.value, state='readonly',
                                  values=list(STYLES.values()), width=18)
        self.choice.pack(side='right', padx=(12, 0))
        self.choice.bind('<<ComboboxSelected>>', self.select)
        self.hint = ttk.Label(self, style='Muted.TLabel',
            text='选择后立即生效；独立显示与合并显示共用此样式。', wraplength=450)
        self.hint.pack(anchor='w', fill='x', pady=(6, 6))
        self.canvas = tk.Canvas(self, height=round(125*ui.scale(self)),
                                highlightthickness=0, bd=0, bg=ui.BG)
        self.canvas.pack(fill='x')
        self.canvas.bind('<Configure>', self.draw)
        self.images = []
        self.refresh_theme()

    def select(self, _event=None):
        selected = next((key for key, label in STYLES.items() if label == self.value.get()), 'classic')
        self.style_id = selected
        self.draw()
        self.on_save(selected)

    def refresh_theme(self):
        self.canvas.configure(bg=ui.BG)
        self.draw()

    def draw(self, _event=None):
        from win_icon import user32
        canvas = self.canvas
        canvas.delete('all')
        self.images.clear()
        scale = ui.scale(self)
        width = max(280, canvas.winfo_width())
        self.hint.configure(wraplength=max(200, width))
        size = max(16, min(64, user32.GetSystemMetrics(49)))
        cases = [('2.4G', '48', 'usb', 'online'), ('蓝牙', '100', 'bluetooth', 'online'),
                 ('低电量', '8', 'usb', 'online'), ('充电', '76', 'usb', 'charging'),
                 ('未知', '--', 'unknown', 'online')]
        label_width = 56*scale
        step = (width-label_width)/len(cases)
        for index, (label, text, transport, state) in enumerate(cases):
            x = label_width+step*(index+.5)
            canvas.create_text(x, 10*scale, text=label, fill=ui.MUTED, font=('Segoe UI', 9))
            for row, light in enumerate((True, False)):
                top = (25+row*44)*scale
                bg, fg = ('#f3f6f8', '#566575') if light else ('#17212b', '#acb8c7')
                if index == 0:
                    canvas.create_rectangle(0, top, width, top+40*scale, fill=bg, outline='')
                    canvas.create_text(8*scale, top+20*scale, text='浅色' if light else '深色',
                                       anchor='w', fill=fg, font=('Segoe UI', 9))
                rgba = render(text, size, transport=transport, light=light,
                              state=state, style=self.style_id)
                icon = tk.PhotoImage(master=self, data=rgba_png(rgba, size))
                self.images.append(icon)
                canvas.create_image(x, top+20*scale, image=icon)
        canvas.create_text(0, 118*scale, text='示例电量 · 图标按当前 Windows 任务栏尺寸绘制',
                           anchor='w', fill=ui.MUTED, font=('Segoe UI', 9))
