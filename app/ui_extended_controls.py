"""Model-gated controls; selecting a value never writes to hardware."""
from dataclasses import replace
import tkinter as tk
from tkinter import ttk
import ui_theme as ui
import extended_controls as ec
import mouse_settings as ms
from ui_setting_fields import ColorPicker, IntegerSlider, valid_color


class ExtendedEditor:
    def __init__(self,page,state):
        self.page=page;self.state=state;self.fields={}
        page.clear_form();page.control_snapshot=state
        s=state.config
        page.details.configure(text=f'{s.base.name} · {s.base.mouse.hardware}')
        page.message.configure(text='已读取设备配置；修改后点击对应应用按钮。新增通道仍需对应型号实机验证。')
        self.label('回报率',heading=True)
        self.label(f'当前设定：{state.rate} Hz' if state.rate is not None else '当前设定：未读取到')
        self.rate=tk.StringVar(value=str(state.rate) if state.rate is not None else '')
        if state.codes:
            row=self.row()
            ttk.Combobox(row,textvariable=self.rate,values=[str(hz) for hz,_ in state.codes],state='readonly',width=12).pack(side='left')
            ttk.Button(row,text='应用回报率',command=self.apply_rate).pack(side='right')
        if state.rate_error:self.label(state.rate_error)
        ttk.Separator(page.form).pack(fill='x',pady=16)
        self.label('灯光与指示灯',heading=True)
        if state.light_error:self.label('未能读取灯光：'+state.light_error)
        elif not state.zones:self.label('此型号没有已接入的灯光控制。')
        for z in state.zones:self.zone(z)
        self.label('显示的是设备保存的灯效配置，不是循环动画瞬间的实际发光颜色。DPI 指示颜色与机身灯效分别标注。')
        ttk.Separator(page.form).pack(fill='x',pady=16)
        ttk.Button(page.form,text='返回 DPI 设置',command=self.load_dpi).pack(anchor='w')

    def label(self,text,heading=False):
        label=tk.Label(self.page.form,text=text,bg=ui.PANEL,fg=ui.FG if heading else ui.MUTED,
                       wraplength=550,justify='left',**({'font':('Microsoft YaHei UI',11,'bold')} if heading else {}))
        label.pack(anchor='w',pady=(6,6));return label

    def row(self):
        row=tk.Frame(self.page.form,bg=ui.PANEL);row.pack(fill='x',pady=6);return row

    def zone(self,z):
        self.label(z.name,heading=True);fields={}
        if self.state.config.family=='vxe' and z.key==100:
            self.label('呼吸模式沿用设备存储的亮度；调整亮度请先在常亮模式应用。')
        if z.write_only:self.label('当前灯效：设备未提供读取。此通道仅能设置，成功响应不代表已读回确认。')
        if z.modes:
            mode=tk.StringVar(value='请选择要设置的灯效' if z.mode==-1 else dict(z.modes).get(z.mode,f'当前灯效代码 {z.mode}（本版不提供设置）'))
            ttk.Combobox(self.row(),textvariable=mode,values=[label for _,label in z.modes],state='readonly',width=30).pack(side='left')
            fields['mode']=mode
        if z.rgb is not None:
            if z.channel:self.label('设置颜色（用于常亮、单色呼吸；其他模式没有固定颜色）')
            color=tk.StringVar(value='' if min(z.rgb)<0 else '#%02X%02X%02X'%z.rgb)
            ColorPicker(self.row(),color).pack(side='left');fields['rgb']=color
        if z.brightness is not None:
            self.label(f'亮度 0–{z.maximum}'+('（设备当前未保存亮度；启用常亮前请选择）' if z.brightness==-1 else ''))
            brightness=tk.StringVar(value='' if z.brightness==-1 else str(z.brightness))
            IntegerSlider(self.row(),brightness,0,z.maximum).pack(fill='x');fields['brightness']=brightness
        if z.speed is not None:
            self.label('动画周期（毫秒，越大越慢；循环与呼吸需要选择）' if z.channel else '速度档位 0 / 1 / 2')
            speed=tk.StringVar(value='' if z.speed==-1 else str(z.speed))
            ttk.Combobox(self.row(),textvariable=speed,values=[str(v) for v in z.speeds],state='readonly',width=10).pack(side='left')
            fields['speed']=speed
        self.fields[z.key]=fields
        ttk.Button(self.row(),text='应用此灯区',command=lambda:self.apply_zone(z)).pack(side='right')

    def done(self,state):
        self.page.extended_editor=ExtendedEditor(self.page,state)
        self.page.message.configure(text='已保存，并读回确认设置及其他配置。')

    def apply_rate(self):
        if self.page.busy:return
        try:
            rate=int(self.rate.get())
            if rate not in dict(self.state.codes):raise ValueError('请选择此连接支持的回报率')
        except ValueError as exc:self.page.message.configure(text=str(exc));return
        self.page.run(lambda:ec.apply(self.state,rate=rate),self.done,'正在保存并确认回报率…')

    def apply_zone(self,z):
        if self.page.busy:return
        try:
            changes={}
            for key,var in self.fields[z.key].items():
                text=var.get()
                if key=='mode':
                    reverse={label:mode for mode,label in z.modes}
                    if text not in reverse:raise ValueError('请选择本版支持的灯效后应用')
                    changes[key]=reverse[text]
                elif key=='rgb':
                    if z.channel and changes.get('mode',z.mode) not in (1,10):changes[key]=(0,0,0);continue
                    if not valid_color(text):raise ValueError('请输入有效颜色，或点击选择颜色')
                    text=text.strip().removeprefix('#');changes[key]=tuple(int(text[i:i+2],16) for i in (0,2,4))
                else:changes[key]=int(text) if text else -1
            wanted=replace(z,**changes);ec.validate_zone(z,wanted)
        except ValueError as exc:self.page.message.configure(text=str(exc));return
        def done(state):
            self.done(state)
            if z.write_only:self.page.message.configure(text='设备已接受灯效命令；此通道不提供读回，当前灯效仍标为未知。')
        self.page.run(lambda:ec.apply(self.state,zone=wanted),done,'正在保存灯光…' if z.write_only else '正在保存并确认灯光…')

    def load_dpi(self):
        if self.page.busy:return
        self.page.run(lambda:ms.read(self.state.config.base.mouse),self.page.render,'正在读取 DPI…')
