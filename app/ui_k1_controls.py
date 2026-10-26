"""K1 uses the existing job queue, sliders, color picker and page theme."""
import tkinter as tk
from tkinter import ttk
import ui_theme as ui
import gravastar_k1 as k1
from ui_setting_fields import IntegerSlider, ColorPicker


class K1Editor:
    def __init__(self, host, state):
        self.host=host;self.state=state
        host.snapshot=None;host.clear_form();host.control_snapshot=state
        host.details.configure(text=f'{k1.SIGNATURES[state.signature]} · 2.4G · {state.mouse.hardware}')
        host.message.configure(text='已读取当前灯光。此适配依据官方驱动，尚待实机验证；点击应用后读回确认。')
        self.body=host.form
        current='自定义逐键灯效（保留）' if state.mode==21 else dict(k1.MODES)[state.mode]
        self.note('当前设备灯效：'+current+'。电量在“我的设备”中自动刷新。')
        self.mode=tk.StringVar(value=current if state.mode!=21 else '')
        combo=ttk.Combobox(self.row('灯效'),textvariable=self.mode,values=[v for _,v in k1.MODES],state='readonly',width=24)
        combo.pack(side='left');combo.bind('<<ComboboxSelected>>',lambda e:self.fields())
        self.fields_body=tk.Frame(self.body,bg=ui.PANEL);self.fields_body.pack(fill='x')
        self.fields()
        self.note('灯效编号对应设备内置顺序。亮度和速度为 0–4 五档；颜色为该效果调色板的第一个颜色，其他颜色保持不变。')
        ttk.Button(self.body,text='应用灯光并确认',command=self.apply).pack(anchor='e',pady=(16,0))

    def row(self, title, parent=None):
        row=tk.Frame(parent or self.body,bg=ui.PANEL);row.pack(fill='x',pady=(12,0))
        tk.Label(row,text=title,bg=ui.PANEL,fg=ui.FG).pack(side='left',padx=(0,16))
        return row

    def note(self, text, parent=None):
        tk.Label(parent or self.body,text=text,bg=ui.PANEL,fg=ui.MUTED,wraplength=570,justify='left').pack(anchor='w',pady=(10,0))

    def selected_mode(self):
        return next((k for k,v in k1.MODES if v==self.mode.get()),None)

    def fields(self):
        for w in self.fields_body.winfo_children():w.destroy()
        self.rgb=None;self.brightness=None;self.speed=None
        mode=self.selected_mode()
        if mode is None:
            self.note('当前为自定义逐键灯效。选择内置灯效后可调整；不会改写逐键灯光数据。',self.fields_body)
            return
        bright,speed,flag,rgb=self.state.fields(mode)
        self.brightness=tk.StringVar(value=str(bright));self.speed=tk.StringVar(value=str(speed))
        IntegerSlider(self.row('亮度 0–4',self.fields_body),self.brightness,0,4).pack(side='left',fill='x',expand=True)
        if mode!=1:
            IntegerSlider(self.row('速度 0–4',self.fields_body),self.speed,0,4).pack(side='left',fill='x',expand=True)
        if mode in k1.COLOR_MODES and flag==0 and rgb is not None:
            self.rgb=tk.StringVar(value='#%02X%02X%02X'%rgb)
            ColorPicker(self.row('第一个颜色',self.fields_body),self.rgb).pack(side='left')
        elif self.state.color_error:
            self.note('颜色表读取失败，暂不可调整颜色；灯效和亮度可单独调整。',self.fields_body)
        else:
            self.note('此效果或当前颜色模式不开放单色编辑，保留设备原有颜色。',self.fields_body)

    def apply(self):
        if self.host.busy:return
        try:
            mode=self.selected_mode()
            if mode is None:raise ValueError()
            brightness=int(self.brightness.get());speed=int(self.speed.get());rgb=None
            if self.rgb is not None:
                value=self.rgb.get().strip().removeprefix('#')
                if len(value)!=6:raise ValueError()
                rgb=tuple(int(value[i:i+2],16) for i in (0,2,4))
            k1.changes(self.state,mode,brightness,speed,rgb)
        except ValueError:
            self.host.message.configure(text='请选择有效的灯效、0–4 档亮度和速度，以及六位颜色。');return
        def done(state):
            self.host.k1_editor=K1Editor(self.host,state)
            self.host.message.configure(text='K1 灯光已保存，设备配置读回一致。')
        self.host.run(lambda:k1.apply(self.state,mode,brightness,speed,rgb),done,'正在保存 K1 灯光并读回确认…')
