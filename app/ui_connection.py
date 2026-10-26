"""Connection badge for cards; uses the discovery key, independent of names."""
import tkinter as tk
import ui_theme as ui
from tray_pixels import TRANSPORT_LABELS, transport_for_key, number_color

def connection_colors():
    return {'bluetooth': '#6db5ff' if ui.CURRENT_THEME == 'dark' else '#1e68c8',
            'usb': ui.GREEN, 'unknown': ui.MUTED}


def reading_color(value,key):
    return '#%02x%02x%02x' % number_color(value,transport_for_key(key),light=ui.CURRENT_THEME == 'light')[:3]


class ConnectionBadge(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=ui.PANEL)
        self.transport = None
        self.factor = ui.scale(parent)
        self.icon = tk.Canvas(self, width=round(18*self.factor), height=round(20*self.factor),
                              bg=ui.PANEL, highlightthickness=0)
        self.icon.pack(side='left', padx=(0,4))
        self.label = tk.Label(self, bg=ui.PANEL, font=('Microsoft YaHei UI',8), bd=0)
        self.label.pack(side='left')

    def show(self, key):
        self.key = key
        transport = transport_for_key(key)
        if transport == self.transport:
            return
        self.transport = transport
        colors = connection_colors()
        self.icon.delete('all')
        self.label.configure(text='' if transport=='unknown' else TRANSPORT_LABELS[transport],
                             fg=colors[transport])
        s=self.factor
        def line(*points):
            self.icon.create_line(*[p*s for p in points],fill=colors[transport],
                                  width=max(1.5,1.5*s),capstyle='round',joinstyle='round')
        if transport == 'bluetooth':
            line(8,2,14,6,4,14)
            line(4,6,14,14,8,18,8,2)
        elif transport == 'usb':
            # A simple USB plug, rather than a tiny detailed connector drawing.
            line(6,2,6,6)
            line(12,2,12,6)
            line(4,6,14,6,14,12,11,15,7,15,4,12,4,6)
            line(9,15,9,19)

    def refresh_theme(self):
        self.transport = None
        self.show(getattr(self, 'key', ''))
