"""Control-only devices share the existing settings page and job queue."""
import tkinter as tk
from tkinter import ttk

import ui_theme as ui
import peripheral_controls as pc
from control_models import MODELS
from ui_setting_fields import ColorPicker,IntegerSlider


class PeripheralEditor:
    def __init__(self,host,state):
        self.host=host;self.state=state
        self.model=MODELS[state.mouse.identity]
        host.snapshot=None;host.clear_form();host.control_snapshot=state
        host.details.configure(text=f'{state.name} · {state.mouse.hardware}'+(f' · 板载配置 {state.profile}' if state.profile else ''))
        host.message.configure(text='已读取设备设置。新增型号按公开协议适配，尚待本机实测；点击应用后保存。')
        self.body=host.form
        if self.model.family != 'dock':
            ttk.Button(self.body,text='读取 DPI 档位',command=self.load_dpi).pack(anchor='w',pady=(0,16))
        if state.rate is not None:
            self.heading('回报率')
            row=self.row('设备设定值')
            self.rate=tk.StringVar(value=str(state.rate))
            ttk.Combobox(row,textvariable=self.rate,values=[str(v) for v in state.rates],state='readonly',width=10).pack(side='left')
            self.label(row,'Hz').pack(side='left',padx=8)
            ttk.Button(row,text='应用回报率',command=self.apply_rate).pack(side='right')
            self.note('实际输入频率可在“设备测试”查看。')
        if state.light is None:
            self.note('灯光读取失败：'+state.light_error if state.light_error else '该型号的可控灯光未接入；不支持的项目不会发送设置命令。')
            return
        ttk.Separator(self.body).pack(fill='x',pady=16)
        self.heading('充电底座灯光' if self.model.family=='dock' else '鼠标整体灯光' if self.model.family=='razer' else 'DPI 指示灯' if self.model.family=='dareu' else '接收器指示灯')
        self.modes={'razer':pc.rz.MODES,'dock':pc.DOCK_MODES,'incott':pc.vc.INCOTT_MODES,'dareu':pc.vc.DAREU_MODES}.get(
            self.model.family,pc.LUNA_MODES if self.model.lighting=='luna' else {0:'关闭',1:'开启'})
        self.mode=tk.StringVar(value=self.modes.get(state.light.mode,''))
        ttk.Combobox(self.row('灯效'),textvariable=self.mode,values=list(self.modes.values()),state='readonly',width=18).pack(side='left')
        if self.model.family in ('dock','razer','dareu'):
            maximum={'dock':4,'razer':100,'dareu':10}[self.model.family]
            minimum=1 if self.model.family=='dareu' else 0
            self.brightness=tk.StringVar(value=str(state.light.brightness))
            IntegerSlider(self.row(f'亮度 {minimum}–{maximum}'),self.brightness,minimum,maximum).pack(side='left',fill='x',expand=True)
            if self.model.family!='dareu':
                self.color=tk.StringVar(value='#%02X%02X%02X'%state.light.rgb)
                ColorPicker(self.row('颜色'),self.color).pack(side='left')
            if self.model.family in ('dock','dareu'):
                self.speed=tk.StringVar(value=str(state.light.speed))
                speed_max=5 if self.model.family=='dareu' else 4
                IntegerSlider(self.row(f'速度 {minimum}–{speed_max}'),self.speed,minimum,speed_max).pack(side='left',fill='x',expand=True)
                self.note('使用设备原生亮度和速度档位；非颜色灯效由设备生成。')
            else:
                self.note('设备不提供当前灯效与颜色查询。请选择要应用的灯效；仅亮度可读回确认，颜色用于常亮。')
        ttk.Button(self.body,text='应用灯光',command=self.apply_light).pack(anchor='e',pady=(12,0))

    def load_dpi(self):
        if self.host.busy:return
        import mouse_settings as ms
        self.host.run(lambda:ms.read(self.state.mouse),self.host.render,'正在读取 DPI 档位…')

    def label(self,parent,text):return tk.Label(parent,text=text,bg=ui.PANEL,fg=ui.FG)

    def heading(self,text):
        tk.Label(self.body,text=text,font=('Microsoft YaHei UI',12,'bold'),bg=ui.PANEL,fg=ui.FG).pack(anchor='w')

    def row(self,title):
        row=tk.Frame(self.body,bg=ui.PANEL);row.pack(fill='x',pady=(12,0))
        self.label(row,title).pack(side='left',padx=(0,16));return row

    def note(self,text):
        tk.Label(self.body,text=text,bg=ui.PANEL,fg=ui.MUTED,wraplength=570,justify='left').pack(anchor='w',pady=(10,0))

    def apply_rate(self):
        if self.host.busy:return
        try:rate=int(self.rate.get())
        except ValueError:self.host.message.configure(text='请选择有效的回报率。');return
        self.apply(rate=rate)

    def apply_light(self):
        if self.host.busy:return
        try:
            mode=next(k for k,v in self.modes.items() if v==self.mode.get())
            brightness=int(self.brightness.get()) if hasattr(self,'brightness') else 0
            speed=int(self.speed.get()) if hasattr(self,'speed') else 0
            color=self.color.get().strip().removeprefix('#') if hasattr(self,'color') else '000000'
            if len(color)!=6:raise ValueError()
            rgb=tuple(int(color[i:i+2],16) for i in (0,2,4))
        except (ValueError,StopIteration):
            self.host.message.configure(text='请选择灯效，并填写有效的整数和六位颜色。');return
        self.apply(light=pc.Light(mode,brightness,speed,rgb))

    def apply(self,**changes):
        def done(state):
            self.host.peripheral_editor=PeripheralEditor(self.host,state)
            message='设置已保存，设备读回确认成功。'
            if self.model.family=='razer' and 'light' in changes:
                message='灯效命令已获设备应答，亮度已读回确认；实际灯效和颜色请观察鼠标。'
            self.host.message.configure(text=message)
        self.host.run(lambda:pc.apply(self.state,**changes),done,'正在保存设置并确认设备响应…')
