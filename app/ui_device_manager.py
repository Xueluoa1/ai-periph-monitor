"""Edit local names and global visibility without changing device protocols."""
import tkinter as tk
from tkinter import ttk
import tray_common as tc
import ui_theme as ui
from device_visibility import device_options


class DeviceManager(tk.Toplevel):
    def __init__(self, parent, readings, on_saved):
        super().__init__(parent)
        self.withdraw()
        self.title('管理设备')
        self.configure(bg=ui.BG)
        self.transient(parent)
        ui.center_dialog(self, parent, 660, 540)
        self.minsize(round(460 * ui.scale(parent)), round(360 * ui.scale(parent)))
        self.on_saved = on_saved
        cfg = tc.load_config()
        self.options = device_options(readings, cfg)
        self.variables = {}
        top = ttk.Frame(self, padding=(24, 20, 24, 12))
        top.pack(fill='x')
        ttk.Label(top, text='管理设备', style='H1.TLabel').pack(anchor='w')
        ttk.Label(top, text='取消“显示”可隐藏不属于你的设备，同时关闭它的电量提醒。\n隐藏项保留在这里，可随时恢复；不会卸载或禁用 Windows 设备。',
                  style='Muted.TLabel', wraplength=550, justify='left').pack(anchor='w', pady=(8, 0))
        bottom = ttk.Frame(self, padding=(24, 12, 24, 20))
        bottom.pack(side='bottom', fill='x')
        self.feedback = ttk.Label(bottom, style='Muted.TLabel', wraplength=550)
        self.feedback.pack(anchor='w', pady=(0, 10))
        actions = ttk.Frame(bottom)
        actions.pack(fill='x')
        ttk.Button(actions, text='全部恢复显示', command=self.restore_all).pack(side='left')
        ttk.Button(actions, text='保存', style='Accent.TButton', command=self.save).pack(side='right')
        ttk.Button(actions, text='取消', command=self.close).pack(side='right', padx=8)
        scroll = ui.ScrollFrame(self)
        scroll.pack(fill='both', expand=True, padx=24)
        hidden = set(cfg.get('hidden_devices') or ())
        models = cfg.get('custom_models') or {}
        for key, title, detected in self.options:
            row = ttk.Frame(scroll.body, padding=(0, 10))
            row.pack(fill='x')
            show = tk.BooleanVar(self, value=key not in hidden)
            name = tk.StringVar(self, value=models.get(key, ''))
            self.variables[key] = (show, name)
            ttk.Checkbutton(row, text='显示', variable=show, command=self.update_summary).pack(side='left', anchor='n', padx=(0, 12))
            body = ttk.Frame(row)
            body.pack(side='left', fill='x', expand=True)
            title_label = ttk.Label(body, text=title.lstrip('🖱️⌨️🎧 '), wraplength=450)
            title_label.pack(anchor='w')
            detail = ttk.Label(body, text=f'{"本次发现" if detected else "未连接 · 保留的设备"} · {key}',
                               style='Muted.TLabel', wraplength=450)
            detail.pack(anchor='w', pady=(4, 8))
            ttk.Label(body, text='自定义名称（留空恢复自动识别）', style='Muted.TLabel').pack(anchor='w')
            ttk.Entry(body, textvariable=name).pack(fill='x', pady=(4, 0))
            body.bind('<Configure>', lambda e, a=title_label, b=detail:
                      [w.configure(wraplength=max(100, e.width)) for w in (a, b)])
            ttk.Separator(scroll.body).pack(fill='x')
        if not self.options:
            ttk.Label(scroll.body, text='暂无设备。连接接收器并刷新后再来管理。', style='Muted.TLabel').pack(pady=24)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda e: self.close())
        self.update_summary()
        self.deiconify()
        self.grab_set()

    def update_summary(self):
        count = sum(not show.get() for show, _ in self.variables.values())
        self.feedback.configure(text=f'{len(self.variables)} 台设备 · {count} 台隐藏（保存后生效）')

    def restore_all(self):
        for show, _ in self.variables.values():
            show.set(True)
        self.update_summary()

    def save(self):
        if any(len(name.get().strip()) > 120 for _, name in self.variables.values()):
            self.feedback.configure(text='设备名称最多 120 个字符，请缩短后保存。')
            return
        cfg = tc.load_config()
        hidden = set(cfg.get('hidden_devices') or ())
        models = dict(cfg.get('custom_models') or {})
        names = dict(cfg.get('device_names') or {})
        for key, title, _ in self.options:
            show, name = self.variables[key]
            if show.get():
                hidden.discard(key)
            else:
                hidden.add(key)
            value = name.get().strip()
            if value:
                models[key] = value
            else:
                models.pop(key, None)
            names[key] = value or title
        cfg.update(hidden_devices=sorted(hidden), custom_models=models, device_names=names)
        tc.save_config(cfg)
        saved = tc.load_config()
        if any(saved.get(key) != cfg[key] for key in ('hidden_devices', 'custom_models', 'device_names')):
            self.feedback.configure(text='保存失败，请检查配置目录是否可写后重试。')
            return
        tc.post_to_tray(tc.MSG_RELOAD)
        self.close()
        self.on_saved()

    def close(self):
        self.grab_release()
        self.destroy()
