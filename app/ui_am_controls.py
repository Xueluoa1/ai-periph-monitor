"""AM .97 lighting editors, shown only from actual device read results."""
import colorsys
import math
import tkinter as tk
from tkinter import ttk

import am_controls as ac
import ui_theme as ui
from ui_setting_fields import ColorPicker, PalettePicker, IntegerSlider


def rgb(text):
    value=text.strip().removeprefix('#')
    if len(value)!=6:raise ValueError('颜色请填写 #RRGGBB，例如 #FF6E00')
    try:return tuple(int(value[i:i+2],16) for i in (0,2,4))
    except ValueError:raise ValueError('颜色请填写六位十六进制数') from None


def hue_color(hue):
    # AM's color wheel uses six 255-step segments, rather than degrees.
    return '#%02X%02X%02X'%tuple(math.floor(v*255+.5) for v in colorsys.hsv_to_rgb((hue%1530)/1530,1,1))


class AmLightEditor:
    def __init__(self,page,state):
        self.page,self.state=page,state
        self.body=page.extra
        self.mouse_fields={};self.dongle_fields={}
        if state.settings is not None:self.mouse_section()
        else:self.note('鼠标灯光未读取成功，暂不能调整。')
        if state.dongle is not None:self.dongle_section()
        elif page.snapshot.mouse.identity[1]==0x0880:
            self.note('接收器灯光：请在设备列表中选择怒喵 2.4G 接收器。')
        else:self.note('接收器灯光未读取成功，暂不能调整。')
        for error in state.errors:self.note(error)

    def note(self,text):
        tk.Label(self.body,text=text,bg=ui.PANEL,fg=ui.MUTED,wraplength=540,justify='left').pack(anchor='w',pady=(8,0))

    def title(self,text):
        ttk.Separator(self.body).pack(fill='x',pady=16)
        tk.Label(self.body,text=text,font=('Microsoft YaHei UI',12,'bold'),bg=ui.PANEL,fg=ui.FG).pack(anchor='w')

    def field(self,label,value,options=None,*,limits=None,color=False,palette=False):
        row=tk.Frame(self.body,bg=ui.PANEL);row.pack(fill='x',pady=(9,0))
        tk.Label(row,text=label,width=17,anchor='w',bg=ui.PANEL,fg=ui.FG).pack(side='left')
        var=tk.StringVar(value=value)
        if color:widget=ColorPicker(row,var)
        elif palette:widget=PalettePicker(row,var)
        elif limits:widget=IntegerSlider(row,var,*limits)
        elif options:widget=ttk.Combobox(row,textvariable=var,values=list(options),state='readonly',width=23)
        else:widget=ttk.Entry(row,textvariable=var,width=26)
        widget.pack(side='left')
        return var

    def mouse_section(self):
        s=self.state.settings;f=self.mouse_fields
        self.title('鼠标本体灯光')
        f['enabled']=self.field('灯光开关','开启' if s[0] else '关闭',('开启','关闭'))
        f['mode']=self.field('灯效',ac.MOUSE_MODES[s[1]],ac.MOUSE_MODES.values())
        f['speed']=self.field('速度 1–10',str(s[2]),limits=(1,10))
        f['colors']=self.field('颜色','',palette=True)
        self.note('不添加颜色时保留原图案。修改灯效或速度时请先选择颜色；常亮、呼吸用一个颜色，循环可添加 1–8 个颜色。')
        if self.state.pattern is None:self.note('现有图案未完整读回，目前仅可改变灯光开关。')
        ttk.Button(self.body,text='应用鼠标灯光',command=self.apply_mouse).pack(anchor='e',pady=(12,0))

    def dongle_section(self):
        s=self.state.dongle;f=self.dongle_fields
        self.title('接收器灯光')
        f['enabled']=self.field('灯光开关','开启' if s[0] else '关闭',('开启','关闭'))
        f['mode']=self.field('灯效',ac.DONGLE_MODES[s[1]],ac.DONGLE_MODES.values())
        f['speed']=self.field('速度 1–20',str(s[11]-80),limits=(1,20))
        f['brightness']=self.field('亮度 1–255',str(s[12]),limits=(1,255))
        for number,offset in enumerate((2,5,8),1):
            color=hue_color(int.from_bytes(s[offset:offset+2],'big'))
            f[f'color{number}']=self.field(f'颜色 {number}（色相）',color,color=True)
            f[f'saturation{number}']=self.field(f'饱和度 {number} 1–100',str(s[offset+2]),limits=(1,100))
        self.note('各灯效使用的颜色数量由接收器决定。颜色设置色相，饱和度与整体亮度单独调整。')
        ttk.Button(self.body,text='应用接收器灯光',command=self.apply_dongle).pack(anchor='e',pady=(12,0))

    def apply_mouse(self):
        if self.page.busy:return
        try:
            f=self.mouse_fields
            colors=tuple(rgb(c) for c in f['colors'].get().replace('，',',').split(',') if c.strip())
            value=ac.MouseLight(f['enabled'].get()=='开启',next(k for k,v in ac.MOUSE_MODES.items() if v==f['mode'].get()),int(f['speed'].get()),colors)
            ac.expected(self.state,light=value)
        except (ValueError,StopIteration) as exc:
            self.page.message.configure(text=str(exc) or '请检查灯光参数');return
        self.page.apply_controls(am_light=value)

    def apply_dongle(self):
        if self.page.busy:return
        try:
            f=self.dongle_fields;s=self.state.dongle;out=bytearray(s)
            out[0]=int(f['enabled'].get()=='开启')
            out[1]=next(k for k,v in ac.DONGLE_MODES.items() if v==f['mode'].get())
            speed=int(f['speed'].get());brightness=int(f['brightness'].get())
            if not 1<=speed<=20 or not 1<=brightness<=255:raise ValueError('速度范围 1–20，亮度范围 1–255')
            out[11],out[12]=speed+80,brightness
            for number,offset in enumerate((2,5,8),1):
                text=f[f'color{number}'].get().strip().upper()
                if text!=hue_color(int.from_bytes(s[offset:offset+2],'big')):
                    h=colorsys.rgb_to_hsv(*(v/255 for v in rgb(text)))[0]
                    out[offset:offset+2]=math.floor(h*1530+.5).to_bytes(2,'big')
                saturation=int(f[f'saturation{number}'].get())
                if not 1<=saturation<=100:raise ValueError('饱和度范围 1–100')
                out[offset+2]=saturation
            ac.expected(self.state,dongle=bytes(out))
        except (ValueError,StopIteration) as exc:
            self.page.message.configure(text=str(exc) or '请检查灯光参数');return
        self.page.apply_controls(am_dongle=bytes(out))
