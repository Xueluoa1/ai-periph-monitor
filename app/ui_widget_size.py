"""Preset and validated manual size controls in the existing settings style."""
import tkinter as tk
from tkinter import ttk
import ui_theme as ui
from widget_size import PRESETS, CUSTOM, parse_size


class WidgetSizeSettings(ttk.Frame):
    def __init__(self, parent, cfg, on_save):
        super().__init__(parent, padding=(0, 10))
        self.on_save = on_save
        self.width = tk.StringVar(self, str(cfg.get('widget_width', 340)))
        self.height = tk.StringVar(self, str(cfg.get('widget_height', 240)))
        current = '自动适应内容'
        if cfg.get('widget_size_mode') == 'fixed':
            dimensions = (cfg.get('widget_width'), cfg.get('widget_height'))
            current = next((label for label, size in PRESETS.items() if size == dimensions), CUSTOM)
        self.mode = tk.StringVar(self, current)
        row = ttk.Frame(self); row.pack(fill='x')
        ttk.Label(row, text='挂件尺寸 / 分辨率').pack(side='left')
        self.choice = ttk.Combobox(row, textvariable=self.mode, state='readonly',
                                  values=[*PRESETS, CUSTOM], width=21)
        row.pack_forget()
        ttk.Label(self, text='挂件尺寸 / 分辨率', style='Group.TLabel').pack(anchor='w')
        self.choice.pack_forget()
        row.pack(fill='x', pady=(8,0))
        for child in row.winfo_children():
            if isinstance(child, ttk.Label): child.pack_forget()
        self.choice.pack(fill='x', expand=True)
        self.choice.bind('<<ComboboxSelected>>', self.select)
        presets = ttk.Frame(self)
        presets.pack(fill='x', pady=(10,0))
        self.preset_buttons = {}
        for index, (label, dimensions) in enumerate((item for item in PRESETS.items() if item[1])):
            button = ttk.Button(presets, text=f'{dimensions[0]} × {dimensions[1]}',
                                command=lambda value=label:self.choose_preset(value))
            button.grid(row=index//2, column=index%2, sticky='ew',
                        padx=(0,6) if index%2 == 0 else (0,0), pady=(0,6))
            self.preset_buttons[label] = button
        presets.columnconfigure((0,1), weight=1, uniform='sizes')
        inputs = ttk.Frame(self); inputs.pack(fill='x', pady=(12, 6))
        ttk.Label(inputs, text='宽').pack(side='left')
        self.width_entry = ttk.Entry(inputs, textvariable=self.width, width=7)
        self.width_entry.pack(side='left', padx=(6, 10))
        ttk.Label(inputs, text='× 高').pack(side='left')
        self.height_entry = ttk.Entry(inputs, textvariable=self.height, width=7)
        self.height_entry.pack(side='left', padx=(6, 6))
        ttk.Label(inputs, text='像素').pack(side='left')
        self.apply_button = ttk.Button(self, text='应用尺寸', command=self.apply, style='Accent.TButton')
        self.apply_button.pack(fill='x', pady=(2,8))
        for entry in (self.width_entry, self.height_entry):
            entry.bind('<Return>', self.apply)
        self.feedback = ttk.Label(self, style='Muted.TLabel', wraplength=480, justify='left')
        self.feedback.pack(anchor='w', fill='x')
        self.bind('<Configure>', lambda e: self.feedback.configure(wraplength=max(100, e.width)))
        self.update_controls()

    def update_controls(self):
        custom = self.mode.get() == CUSTOM
        for control in (self.width_entry, self.height_entry, self.apply_button):
            control.configure(state='normal' if custom else 'disabled')
        for label, button in self.preset_buttons.items():
            button.configure(style='Accent.TButton' if self.mode.get()==label else 'TButton')
        self.feedback.configure(foreground=ui.MUTED,
            text='自定义需点击应用。内容过多时可滚动；超出屏幕的尺寸会自动限制。' if custom else
                 '自动随设备数量调整高度。' if self.mode.get() == '自动适应内容' else
                 '固定窗口像素尺寸，设备列表可滚动；紧凑模式仅调整内容密度。')

    def select(self, _event=None):
        self.update_controls()
        label = self.mode.get()
        if label == CUSTOM:
            self.width_entry.focus_set()
            return
        size = PRESETS[label]
        if size is None:
            self.on_save({'widget_size_mode': 'auto'})
        else:
            self.width.set(str(size[0])); self.height.set(str(size[1]))
            self.on_save({'widget_size_mode': 'fixed', 'widget_width': size[0], 'widget_height': size[1]})

    def choose_preset(self, label):
        self.mode.set(label)
        self.select()

    def apply(self, _event=None):
        if self.mode.get() != CUSTOM:
            return 'break'
        try:
            width, height = parse_size(self.width.get(), self.height.get())
        except ValueError as error:
            self.feedback.configure(text=str(error), foreground=ui.RED)
            return 'break'
        self.width.set(str(width)); self.height.set(str(height))
        self.on_save({'widget_size_mode': 'fixed', 'widget_width': width, 'widget_height': height})
        self.feedback.configure(text=f'已应用 {width} × {height} 像素，超过屏幕大小时自动限制。', foreground=ui.ACCENT)
        return 'break'
