"""Non-activating tray hover window. No discovery, disk access or device queries.

The native tray thread sends immutable display data through a queue. All Tk
objects are created, used and disposed on the dedicated presentation thread.
"""
import ctypes
from ctypes import wintypes
import queue
import threading
import time

from tray_merge import popup_position

USER32 = ctypes.WinDLL('user32', use_last_error=True)
USER32.GetParent.argtypes = [wintypes.HWND]
USER32.GetParent.restype = wintypes.HWND
USER32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
USER32.GetWindowLongW.restype = wintypes.LONG
USER32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
USER32.SetWindowLongW.restype = wintypes.LONG
USER32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, wintypes.UINT]
USER32.SetWindowPos.restype = wintypes.BOOL
USER32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
USER32.GetCursorPos.restype = wintypes.BOOL
USER32.MonitorFromRect.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD]
USER32.MonitorFromRect.restype = wintypes.HANDLE


class MonitorInfo(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('monitor', wintypes.RECT),
                ('work', wintypes.RECT), ('flags', wintypes.DWORD)]


USER32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
USER32.GetMonitorInfoW.restype = wintypes.BOOL


def work_area(anchor):
    rect = wintypes.RECT(*anchor)
    monitor = USER32.MonitorFromRect(ctypes.byref(rect), 2)
    info = MonitorInfo()
    info.cbSize = ctypes.sizeof(info)
    if not USER32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    return info.work.left, info.work.top, info.work.right, info.work.bottom


def contains(rect, x, y):
    return rect[0] <= x < rect[2] and rect[1] <= y < rect[3]


class HoverPanel:
    def __init__(self):
        import tkinter as tk
        import ui_theme as ui
        self.tk, self.ui = tk, ui
        ui.prepare_process()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title('DeviceBatteryTrayHover')
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        ui.configure(self.root)
        self.factor = ui.scale(self.root)
        self.frame = tk.Frame(self.root)
        self.frame.pack(fill='both', expand=True, padx=1, pady=1)
        self.canvas = tk.Canvas(self.frame, highlightthickness=0, width=1, height=1)
        self.bar = tk.Scrollbar(self.frame, orient='vertical', command=self.canvas.yview)
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.body = tk.Frame(self.canvas)
        self.item = self.canvas.create_window(0, 0, anchor='nw', window=self.body)
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self.item, width=e.width))
        self.body.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.root.bind('<MouseWheel>', self.wheel)
        self.root.bind('<Escape>', lambda e: self.hide())
        self.visible = False
        self.signature = None
        self.bounds = self.anchor = (0, 0, 0, 0)
        self.last_inside = 0.0

    def wheel(self, event):
        if self.body.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(-int(event.delta / 120), 'units')
        return 'break'

    def render(self, rows, theme):
        from ui_connection import reading_color
        tk, ui = self.tk, self.ui
        signature = (tuple(rows), theme)
        if signature == self.signature:
            return
        self.signature = signature
        if theme != ui.CURRENT_THEME:
            ui.apply_theme(self.root, theme)
        self.root.configure(bg=ui.LINE)
        for w in (self.frame, self.canvas, self.body):
            w.configure(bg=ui.PANEL)
        for child in self.body.winfo_children():
            child.destroy()
        for index, item in enumerate(rows):
            if index:
                tk.Frame(self.body, bg=ui.LINE, height=1).pack(fill='x', padx=8)
            row = tk.Frame(self.body, bg=ui.PANEL, padx=8, pady=4)
            row.pack(fill='x')
            row.columnconfigure(1, weight=1)
            ui.DeviceGlyph(row, kind=ui.device_kind(item.title, item.headset),
                           size=14, bg=ui.PANEL, color=ui.MUTED).grid(row=0, column=0, padx=(0, 6))
            tk.Label(row, text=item.title, font=('Microsoft YaHei UI', 8),
                     bg=ui.PANEL, fg=ui.FG, anchor='w', justify='left',
                     wraplength=round(165 * self.factor)).grid(row=0, column=1, sticky='ew')
            tk.Label(row, text=f'{item.level}%' if item.level is not None else '--',
                     font=('Segoe UI', 10, 'bold'), bg=ui.PANEL,
                     fg=reading_color(item.level, item.key), anchor='e', width=5
                     ).grid(row=0, column=2, padx=(5, 0), sticky='e')
        self.root.update_idletasks()

    def show(self, rows, theme, anchor):
        if not rows:
            self.hide()
            return
        self.render(rows, theme)
        self.anchor = anchor
        area = work_area(anchor)
        width = min(round(270 * self.factor), area[2] - area[0])
        self.canvas.itemconfigure(self.item, width=width - 2)
        self.root.update_idletasks()
        natural = self.body.winfo_reqheight() + 2
        height = min(natural, round(260 * self.factor), area[3] - area[1] - 12)
        if natural > height:
            self.bar.pack(side='right', fill='y')
        else:
            self.bar.pack_forget()
        x, y = popup_position(anchor, area, width, height)
        # Set native coordinates after mapping; Tk negative geometry offsets
        # otherwise refer to the primary monitor's right/bottom edge.
        self.root.geometry(f'{width}x{height}')
        self.root.update_idletasks()
        hwnd = USER32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
        style = USER32.GetWindowLongW(hwnd, -20)
        USER32.SetWindowLongW(hwnd, -20, (style | 0x08000080) & ~0x00040000)
        self.root.deiconify()
        USER32.SetWindowPos(hwnd, -1, x, y, width, height, 0x0010 | 0x0040)
        self.bounds = x, y, x + width, y + height
        self.visible = True
        self.last_inside = time.monotonic()

    def hide(self):
        self.root.withdraw()
        self.visible = False

    def check_pointer(self):
        if not self.visible:
            return
        point = wintypes.POINT()
        if not USER32.GetCursorPos(ctypes.byref(point)):
            self.hide()
        elif contains(self.bounds, point.x, point.y) or contains(self.anchor, point.x, point.y):
            self.last_inside = time.monotonic()
        elif time.monotonic() - self.last_inside >= 0.35:
            self.hide()


class HoverController:
    def __init__(self):
        self.commands = queue.Queue()
        self.thread = None

    def show(self, rows, theme, anchor):
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._run, daemon=True, name='TrayHover')
            self.thread.start()
        self.commands.put(('show', tuple(rows), theme, anchor))

    def hide(self):
        if self.thread is not None and self.thread.is_alive():
            self.commands.put(('hide',))

    def update(self, rows, theme):
        if self.thread is not None and self.thread.is_alive():
            self.commands.put(('update', tuple(rows), theme))

    def close(self):
        if self.thread is not None and self.thread.is_alive():
            self.commands.put(('close',))
            self.thread.join(timeout=1)

    def _run(self):
        panel = HoverPanel()
        def process_commands():
            while True:
                try:
                    command = self.commands.get_nowait()
                except queue.Empty:
                    break
                if command[0] == 'close':
                    panel.root.quit()
                    return
                if command[0] == 'hide':
                    panel.hide()
                elif command[0] == 'show':
                    panel.show(*command[1:])
                elif command[0] == 'update' and panel.visible:
                    last_inside = panel.last_inside
                    panel.show(*command[1:], panel.anchor)
                    panel.last_inside = last_inside
            panel.check_pointer()
            return True
        def poll():
            try:
                keep_running = process_commands()
            except Exception:
                import traceback
                traceback.print_exc()
                panel.hide()
                keep_running = True
            if keep_running:
                panel.root.after(75 if panel.visible else 200, poll)
        panel.root.after(0, poll)
        try:
            panel.root.mainloop()
        finally:
            panel.root.destroy()
            # Tk's interpreter must be finalized by its owning thread.
            del poll
            del process_commands
            del panel
            import gc
            gc.collect()
