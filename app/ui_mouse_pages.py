"""Mouse settings and input tests using the existing square-card Tk theme."""
import math
import queue
import threading
import tkinter as tk
from tkinter import ttk

import ui_theme as ui
import mouse_settings as ms
import mouse_controls as mc
import peripheral_controls as pc
import extended_controls as ec
from ui_extended_controls import ExtendedEditor
from ui_peripheral_controls import PeripheralEditor
from ui_am_controls import AmLightEditor
from ui_k1_controls import K1Editor
import gravastar_k1 as k1
from ui_setting_fields import ColorPicker, IntegerSlider
from input_test import InputSession, u, imm


class NavigationButton(ttk.Button):
    def __init__(self,parent,text,icon,active=False):
        self.icon=icon
        super().__init__(parent,text=text,compound='top',style='NavActive.TButton' if active else 'Nav.TButton')
        self.refresh_theme()

    def select(self,active):
        self.configure(style='NavActive.TButton' if active else 'Nav.TButton')
        self.refresh_theme()

    def refresh_theme(self):
        active=self.cget('style')=='NavActive.TButton'
        fg=ui.GREEN if active else ui.MUTED
        bg=ui.SELECTED if active else ui.PANEL
        paths={
            'devices':[[(3,3),(9,3),(9,9),(3,9),(3,3)],[(15,3),(21,3),(21,9),(15,9),(15,3)],[(3,15),(9,15),(9,21),(3,21),(3,15)],[(15,15),(21,15),(21,21),(15,21),(15,15)]],
            'mouse':[[(6,10),(6,7),(8,3),(12,2),(16,3),(18,7),(18,17),(16,21),(12,22),(8,21),(6,17),(6,10)],[(6,10),(18,10)],[(12,2),(12,10)]],
            'test':[[(2,12),(6,12),(9,5),(13,20),(17,9),(19,12),(22,12)]],
            'settings':[[(3,6),(21,6)],[(3,12),(21,12)],[(3,18),(21,18)],[(8,3),(8,9)],[(16,9),(16,15)],[(10,15),(10,21)]],
        }[self.icon]
        n=round(20*ui.scale(self))
        image=tk.PhotoImage(master=self,width=n,height=n)
        f=tuple(int(fg[i:i+2],16) for i in (1,3,5));b=tuple(int(bg[i:i+2],16) for i in (1,3,5))
        segments=[(a,z) for path in paths for a,z in zip(path,path[1:])]
        rows=[]
        for y in range(n):
            row=[]
            for x in range(n):
                px=(x+.5)*24/n;py=(y+.5)*24/n
                distance=99
                for (ax,ay),(bx,by) in segments:
                    t=max(0,min(1,((px-ax)*(bx-ax)+(py-ay)*(by-ay))/max(.001,(bx-ax)**2+(by-ay)**2)))
                    distance=min(distance,math.hypot(px-ax-t*(bx-ax),py-ay-t*(by-ay)))
                alpha=max(0,min(1,(1.2-distance)*n/24))
                row.append('#%02x%02x%02x'%tuple(round(b[i]+(f[i]-b[i])*alpha) for i in range(3)))
            rows.append('{'+ ' '.join(row)+'}')
        image.put(' '.join(rows));self._image=image;self.configure(image=image)


class MouseSettingsPage(ttk.Frame):
    def __init__(self,parent):
        super().__init__(parent)
        self.devices=[];self.snapshot=None;self.busy=False;self.jobs=queue.Queue();self.generation=0
        head=ttk.Frame(self,padding=(24,22,24,16));head.pack(fill='x')
        ttk.Label(head,text='外设设置',style='H1.TLabel').pack(anchor='w')
        ttk.Label(head,text='读取设备可用设置，调整后确认设备响应',style='Muted.TLabel').pack(anchor='w',pady=(5,0))
        bar=ttk.Frame(self,padding=(24,0,24,12));bar.pack(fill='x')
        self.combo=ttk.Combobox(bar,state='readonly');self.combo.pack(side='left',fill='x',expand=True)
        self.combo.bind('<<ComboboxSelected>>',lambda e:self.load_selected())
        self.reload=ttk.Button(bar,text='重新读取设备',command=self.on_show);self.reload.pack(side='right',padx=(12,0))
        self.message=ttk.Label(self,text='打开页面后自动读取。',style='Muted.TLabel',wraplength=650)
        self.message.pack(fill='x',padx=24,pady=(0,10))
        self.scroll=ui.ScrollFrame(self,content_gap=20);self.scroll.pack(fill='both',expand=True,padx=24,pady=(0,18))
        self.details=ttk.Label(self.scroll.body,text='',style='Muted.TLabel',wraplength=640)
        self.details.pack(anchor='w',pady=(0,12))
        self.card=ui.Surface(self.scroll.body,padding=16);self.card.pack(fill='x')
        self.form=self.card.body
        self.rows=[]
        self.poll_id=self.after(100,self.poll)
        self.bind('<Destroy>',self.destroyed,add='+')

    def destroyed(self,event):
        if event.widget is self:
            self.generation+=1
            try:self.after_cancel(self.poll_id)
            except tk.TclError:pass

    def on_hide(self):pass

    def on_show(self):
        if self.busy:return
        self.snapshot=None;self.clear_form('正在读取接收器和外设信息…')
        self.run(ms.discover,self.discovered,'正在识别设备…')

    def clear_form(self,text=''):
        self.control_snapshot=None
        for child in self.form.winfo_children():child.destroy()
        self.rows=[]
        if text:tk.Label(self.form,text=text,bg=ui.PANEL,fg=ui.MUTED,wraplength=570,justify='left').pack(anchor='w')

    def set_busy(self,value):
        self.busy=value
        self.reload.configure(state='disabled' if value else 'normal')
        self.combo.configure(state='disabled' if value else 'readonly')
        def controls(parent):
            for child in parent.winfo_children():
                if isinstance(child,(ttk.Button,ttk.Radiobutton,ttk.Entry,ttk.Combobox,ttk.Scale)):
                    child.state(['disabled'] if value else ['!disabled'])
                controls(child)
        controls(self.form)

    def run(self,work,done,message):
        self.set_busy(True);self.message.configure(text=message)
        token=self.generation
        def worker():
            try:result=work();error=None
            except Exception as exc:result=None;error=str(exc)
            self.jobs.put((token,done,result,error))
        threading.Thread(target=worker,daemon=True).start()

    def poll(self):
        try:
            while True:
                token,done,result,error=self.jobs.get_nowait()
                if token!=self.generation:continue
                self.set_busy(False)
                if error:
                    self.snapshot=None
                    self.message.configure(text=error+'。请重新读取后再操作。')
                    self.clear_form('未能确认设备配置，已暂停调整。')
                else:done(result)
        except queue.Empty:pass
        self.poll_id=self.after(100,self.poll)

    def discovered(self,devices):
        self.devices=devices
        self.combo.configure(values=[f'{d.name} · {d.hardware} · 设备 {i+1}' for i,d in enumerate(devices)])
        if not devices:
            self.combo.set('未发现可识别的外设配置接口')
            self.message.configure(text='连接 USB 接收器或数据线并唤醒外设，再点击重新读取设备。')
            self.details.configure(text='按具体型号提供 DPI、回报率或灯光控制；完整范围见随包型号清单。')
            self.clear_form('此页面需要可识别的配置接口。请使用已适配的 USB 或 2.4G 连接。')
            return
        self.combo.current(0);self.load_selected()

    def load_selected(self):
        if self.busy:return
        index=self.combo.current()
        if not 0<=index<len(self.devices):return
        mouse=self.devices[index];self.snapshot=None
        self.details.configure(text=f'硬件标识 {mouse.hardware} · 型号以硬件识别结果为准')
        self.clear_form('正在读取 DPI 档位…')
        if mouse.protocol=='k1':
            def k1_done(state):self.k1_editor=K1Editor(self,state)
            self.clear_form('正在读取 K1 当前灯光…')
            self.run(lambda:k1.read(mouse),k1_done,'正在核对 K1 型号并读取当前灯光…')
            return
        if mouse.protocol=='extra':
            from control_models import MODELS
            if MODELS[mouse.identity].family!='dock':
                self.clear_form()
                ttk.Button(self.form,text='读取 DPI 档位',command=lambda:self.run(lambda:ms.read(mouse),self.render,'正在读取 DPI 档位…')).pack(anchor='w')
                def controls_done(state):self.peripheral_editor=PeripheralEditor(self,state)
                ttk.Button(self.form,text='读取回报率与灯光',command=lambda:self.run(lambda:pc.read(mouse),controls_done,'正在读取回报率与灯光…')).pack(anchor='w',pady=(12,0))
                self.message.configure(text='选择要读取的设置；读取和切换页面不会修改设备。')
                return
            def done(state):self.peripheral_editor=PeripheralEditor(self,state)
            self.run(lambda:pc.read(mouse),done,'正在读取回报率与灯光…')
            return
        if not mouse.protocol:
            self.clear_form(mouse.reason);self.message.configure(text='已发现设备，当前型号暂不开放 DPI 调整。');return
        self.run(lambda:ms.read(mouse),self.render,'正在读取当前档位和 DPI 数值…')

    def render(self,state,selected=None):
        self.snapshot=state;self.clear_form()
        self.message.configure(text='点击数值后的修改可设置 DPI；保存后读取设备确认。'+(' 新增协议尚待对应设备实测。' if state.mouse.protocol in ('atk','extra','dpi') else ''))
        model=getattr(getattr(state.protocol_data,'base',None),'name',state.mouse.name)
        self.details.configure(text=f'{model} · {state.mouse.hardware}\n'+(f'板载配置 {state.profile} · ' if state.profile else '')+f'{len(state.stages)} 个读取值 · 范围 {state.minimum}–{state.maximum} DPI'+(' · 固件 '+state.firmware if state.firmware else ''))
        self.selected_stage=tk.IntVar(value=max(0,state.active) if selected is None else selected)
        can_activate=getattr(state.protocol_data,'can_activate',True)
        self.editors={};self.edit_buttons={};self.dpi_labels={};self.stage_controls=[]
        tk.Label(self.form,text='DPI 档位',font=('Microsoft YaHei UI',12,'bold'),bg=ui.PANEL,fg=ui.FG).pack(anchor='w')
        note=getattr(state.protocol_data,'note','')
        if state.mouse.protocol=='atk' and state.mouse.identity==ms.r1nk.IDENTITY:
            note='DPI：200–10000 步进 50；10100–18000 步进 100。此型号使用专用传感器编码。'
        if any(x!=y for x,y in state.stages):
            if state.mouse.protocol in ('atk','am35'):
                note+=' 修改数值会同时设置此档位的 X/Y。'
            else:
                note+=(' 此型号暂不支持修改分轴不同的档位。' if state.mouse.protocol=='rog' or getattr(state.protocol_data,'family','') in ('rog_more','atlantis','scyrox') else ' X/Y 不同时，修改只改变 X，保留 Y。')
        if note:tk.Label(self.form,text=note,bg=ui.PANEL,fg=ui.MUTED,wraplength=540,justify='left').pack(anchor='w',pady=(8,0))
        for index,(x,y) in enumerate(state.stages):
            row=tk.Frame(self.form,bg=ui.PANEL);row.pack(fill='x',pady=(12,0))
            select=ttk.Radiobutton(row,text=f' 档位 {index+1}',variable=self.selected_stage,value=index,style='Stage.TRadiobutton')
            select.pack(side='left');self.stage_controls.append(select)
            value_label=tk.Label(row,text=f'{x} DPI' if x==y else f'X {x} / Y {y}',bg=ui.PANEL,fg=ui.FG,font=('Segoe UI',13))
            value_label.pack(side='left',padx=(12,8));self.dpi_labels[index]=value_label
            edit=ttk.Button(row,text='修改',style='Card.TButton',command=lambda i=index:self.edit_stage(i))
            edit.pack(side='left',padx=(0,10));self.edit_buttons[index]=edit
            if index==state.active:
                tk.Label(row,text='当前',bg=ui.PANEL,fg=ui.GREEN).pack(side='right')
            editor=tk.Frame(self.form,bg=ui.PANEL)
            value=tk.StringVar(value=str(x))
            entry=ttk.Entry(editor,textvariable=value,width=10);entry.pack(side='left',padx=(0,8))
            tk.Label(editor,text='DPI',bg=ui.PANEL,fg=ui.MUTED).pack(side='left',padx=(0,12))
            ttk.Button(editor,text='确认修改',style='Accent.TButton',command=lambda i=index:self.confirm_edit(i)).pack(side='left')
            ttk.Button(editor,text='取消',command=lambda i=index:self.cancel_edit(i)).pack(side='left',padx=8)
            entry.bind('<Return>',lambda e,i=index:self.confirm_edit(i))
            entry.bind('<Escape>',lambda e,i=index:self.cancel_edit(i))
            self.editors[index]=(editor,value,entry,row)
        ttk.Separator(self.form).pack(fill='x',pady=16)
        tk.Label(self.form,text='确认修改后保存该档 DPI，并读取设备结果。'+('应用只切换到选中的档位。' if can_activate else '此通道不支持软件切换档位。'),bg=ui.PANEL,fg=ui.MUTED,wraplength=540,justify='left').pack(anchor='w',pady=(0,10))
        self.apply_button=ttk.Button(self.form,text='应用',style='Accent.TButton',command=self.apply)
        if can_activate:self.apply_button.pack(anchor='e')
        ttk.Separator(self.form).pack(fill='x',pady=16)
        self.extra=tk.Frame(self.form,bg=ui.PANEL);self.extra.pack(fill='x')
        if state.mouse.protocol=='extra':
            ttk.Button(self.extra,text='读取回报率与灯光',command=self.load_extra_controls).pack(anchor='w')
        elif ec.supported(state.mouse):
            ttk.Button(self.extra,text='读取回报率与灯光',command=self.load_extended_controls).pack(anchor='w')
        elif mc.supported(state.mouse):
            ttk.Button(self.extra,text='读取回报率与灯光',command=self.load_controls).pack(anchor='w')
            tk.Label(self.extra,text='读取后显示当前设置；点击对应的应用按钮才会保存。',bg=ui.PANEL,fg=ui.MUTED,wraplength=540,justify='left').pack(anchor='w',pady=(8,0))
        else:
            tk.Label(self.extra,text='该型号或接收器的回报率、灯光尚未适配。',bg=ui.PANEL,fg=ui.MUTED).pack(anchor='w')

    def load_extended_controls(self):
        if self.busy or not self.snapshot:return
        mouse=self.snapshot.mouse
        def done(state):self.extended_editor=ExtendedEditor(self,state)
        self.run(lambda:ec.read(mouse),done,'正在读取回报率与灯光…')

    def load_extra_controls(self):
        if self.busy or not self.snapshot:return
        mouse=self.snapshot.mouse
        def done(state):self.peripheral_editor=PeripheralEditor(self,state)
        self.run(lambda:pc.read(mouse),done,'正在读取回报率与灯光…')

    def load_controls(self):
        if self.busy or not self.snapshot:return
        mouse=self.snapshot.mouse
        def done(controls):
            self.render(controls.dpi)
            self.render_controls(controls)
            self.message.configure(text='已读取当前设置。选择数值后点击对应的应用按钮。')
        self.run(lambda:mc.read(mouse),done,'正在读取回报率与灯光…')

    def render_controls(self,controls):
        self.control_snapshot=controls
        for child in self.extra.winfo_children():child.destroy()
        tk.Label(self.extra,text='回报率',font=('Microsoft YaHei UI',12,'bold'),bg=ui.PANEL,fg=ui.FG).pack(anchor='w')
        row=tk.Frame(self.extra,bg=ui.PANEL);row.pack(fill='x',pady=(10,0))
        self.rate_value=tk.StringVar(value=str(controls.rate))
        rates=(controls.dpi.protocol_data.rates if controls.dpi.mouse.protocol=='xiaomi2'
               else mc.ac.RATES if controls.am_state else mc.RATES)
        ttk.Combobox(row,textvariable=self.rate_value,values=[str(v) for v in rates],state='readonly',width=9).pack(side='left')
        tk.Label(row,text='Hz',bg=ui.PANEL,fg=ui.MUTED).pack(side='left',padx=8)
        ttk.Button(row,text='应用回报率',command=self.apply_rate).pack(side='right')
        tk.Label(self.extra,text='这是设备设定回报率；实际输入频率可在“设备测试”查看。',bg=ui.PANEL,fg=ui.MUTED,wraplength=540,justify='left').pack(anchor='w',pady=(8,0))
        if controls.am_state:
            self.am_editor=AmLightEditor(self,controls.am_state)
            return
        if controls.light is None:
            if controls.dpi.mouse.protocol=='xiaomi2':
                ttk.Separator(self.extra).pack(fill='x',pady=16)
                tk.Label(self.extra,text='DPI 档位指示色（设备存储值）',bg=ui.PANEL,fg=ui.FG).pack(anchor='w')
                for i,color in enumerate(controls.dpi.colors):
                    row=tk.Frame(self.extra,bg=ui.PANEL);row.pack(fill='x',pady=5)
                    tk.Label(row,text=f'档位 {i+1}',bg=ui.PANEL,fg=ui.FG).pack(side='left',padx=(0,10))
                    var=tk.StringVar(value='#'+color.hex().upper())
                    ColorPicker(row,var).pack(side='left')
                    ttk.Button(row,text='应用颜色',command=lambda i=i,var=var:self.apply_xiaomi_color(i,var)).pack(side='right')
                tk.Label(self.extra,text='这是档位指示色，不是 RGB 动态灯效。回报率选项受当前性能模式限制；切换模式请使用官方驱动。新协议尚待实机验证。',
                         bg=ui.PANEL,fg=ui.MUTED,wraplength=540,justify='left').pack(anchor='w',pady=(8,0))
                return
            if controls.dpi.mouse.identity==ms.r1nk.IDENTITY and controls.dpi.colors:
                ttk.Separator(self.extra).pack(fill='x',pady=16)
                tk.Label(self.extra,text='已保存的 DPI 档位指示色',bg=ui.PANEL,fg=ui.FG).pack(anchor='w')
                for i,color in enumerate(controls.dpi.colors):
                    tk.Label(self.extra,text=f'档位 {i+1}  #{color.hex().upper()}',bg=ui.PANEL,fg=ui.FG).pack(anchor='w',pady=3)
                tk.Label(self.extra,text='这些是设备返回的档位颜色，不代表此时灯效；此连接的颜色与灯效写入尚未验证。',
                         bg=ui.PANEL,fg=ui.MUTED,wraplength=540,justify='left').pack(anchor='w',pady=(8,0))
                return
            tk.Label(self.extra,text='该型号灯光尚未适配。',bg=ui.PANEL,fg=ui.MUTED).pack(anchor='w',pady=(16,0));return
        ttk.Separator(self.extra).pack(fill='x',pady=16)
        tk.Label(self.extra,text='滚轮灯光',font=('Microsoft YaHei UI',12,'bold'),bg=ui.PANEL,fg=ui.FG).pack(anchor='w')
        self.light_mode=tk.StringVar(value=mc.LIGHT_MODES[controls.light.mode])
        self.light_brightness=tk.StringVar(value=str(controls.light.brightness))
        self.light_color=tk.StringVar(value='#%02X%02X%02X'%controls.light.rgb)
        for label,var,values in [('灯效',self.light_mode,list(mc.LIGHT_MODES.values())),('亮度 0–100',self.light_brightness,None),('颜色 #RRGGBB',self.light_color,None)]:
            row=tk.Frame(self.extra,bg=ui.PANEL);row.pack(fill='x',pady=(10,0))
            tk.Label(row,text=label,width=15,anchor='w',bg=ui.PANEL,fg=ui.FG).pack(side='left')
            if values:field=ttk.Combobox(row,textvariable=var,values=values,state='readonly',width=16)
            elif var is self.light_brightness:field=IntegerSlider(row,var,0,100)
            else:field=ColorPicker(row,var)
            field.pack(side='left')
        tk.Label(self.extra,text='颜色用于常亮、呼吸和按键响应；循环与电量指示由设备决定颜色。',bg=ui.PANEL,fg=ui.MUTED,wraplength=540,justify='left').pack(anchor='w',pady=(10,0))
        ttk.Button(self.extra,text='应用灯光',command=self.apply_light).pack(anchor='e',pady=(12,0))

    def apply_rate(self):
        if self.busy or not self.control_snapshot:return
        self.apply_controls(rate=int(self.rate_value.get()))

    def apply_xiaomi_color(self,stage,var):
        if self.busy or not self.control_snapshot:return
        try:
            value=var.get().strip().removeprefix('#')
            if len(value)!=6:raise ValueError()
            rgb=tuple(bytes.fromhex(value))
        except ValueError:
            self.message.configure(text='请输入六位十六进制颜色，例如 #FF6E00。');return
        snapshot=self.control_snapshot.dpi
        def done(dpi):
            self.render(dpi);self.render_controls(mc.Controls(dpi,dpi.protocol_data.rate,None,b''))
            self.message.configure(text='档位指示色已保存，设备读回确认成功。')
        self.run(lambda:ms.xm.apply(snapshot,stage=stage,rgb=rgb),done,'正在保存档位指示色并读回…')

    def apply_light(self):
        if self.busy or not self.control_snapshot:return
        try:
            color=self.light_color.get().strip().removeprefix('#')
            if len(color)!=6:raise ValueError()
            rgb=tuple(int(color[i:i+2],16) for i in (0,2,4))
            mode=next(k for k,v in mc.LIGHT_MODES.items() if v==self.light_mode.get())
            light=mc.Light(mode,int(self.light_brightness.get()),rgb)
            mc.validate_light(light)
        except (ValueError,StopIteration):
            self.message.configure(text='请填写 0–100 的整数亮度和六位十六进制颜色，例如 #FF6E00。');return
        self.apply_controls(light=light)

    def apply_controls(self,**changes):
        snapshot=self.control_snapshot
        def done(controls):
            self.render(controls.dpi);self.render_controls(controls)
            self.message.configure(text='设置已保存，设备读回确认成功。')
        self.run(lambda:mc.apply(snapshot,**changes),done,'正在保存设置并读回确认…')

    def edit_stage(self,index):
        if self.busy:return
        for other in self.editors:
            if other!=index:self.cancel_edit(other)
        editor,value,entry,row=self.editors[index]
        value.set(str(self.snapshot.stages[index][0]))
        editor.pack(after=row,fill='x',padx=(28,0),pady=(8,4))
        self.edit_buttons[index].state(['disabled'])
        self.apply_button.state(['disabled'])
        entry.focus_set();entry.selection_range(0,'end')

    def cancel_edit(self,index):
        if self.busy:return 'break'
        self.editors[index][0].pack_forget()
        self.edit_buttons[index].state(['!disabled'])
        self.apply_button.state(['!disabled'])
        return 'break'

    def confirm_edit(self,index):
        if self.busy or not self.snapshot:return 'break'
        try:value=int(self.editors[index][1].get().strip())
        except ValueError:
            self.message.configure(text='请输入整数 DPI。');return 'break'
        snapshot=self.snapshot;selected=self.selected_stage.get()
        if not snapshot.minimum<=value<=snapshot.maximum or value%snapshot.step:
            self.message.configure(text=f'DPI 范围 {snapshot.minimum}–{snapshot.maximum}，步进 {snapshot.step}。');return 'break'
        def done(state):
            self.render(state,selected);self.message.configure(text=f'档位 {index+1} 已修改为 {value} DPI，设备读回确认成功。')
        self.run(lambda:ms.apply(snapshot,index,value,False),done,'正在保存 DPI 并读回确认…')
        return 'break'

    def apply(self):
        if self.busy or not self.snapshot:return
        if any(editor.winfo_manager() for editor,_,_,_ in self.editors.values()):return
        stage=self.selected_stage.get();snapshot=self.snapshot
        def done(state):self.render(state);self.message.configure(text=f'已切换到档位 {stage+1}，设备读回确认成功。')
        self.run(lambda:ms.apply(snapshot,stage,None,True),done,'正在切换档位并读回确认…')


class DeviceTestPage(ttk.Frame):
    def __init__(self,parent):
        super().__init__(parent)
        self.session=None;self.active=False;self.key_labels={};self.ime_context=None
        head=ttk.Frame(self,padding=(24,22,24,16));head.pack(fill='x')
        ttk.Label(head,text='设备测试',style='H1.TLabel').pack(anchor='w')
        ttk.Label(head,text='键鼠按键、滚轮与鼠标实际输入频率',style='Muted.TLabel').pack(anchor='w',pady=(5,0))
        bar=ttk.Frame(self,padding=(24,0,24,12));bar.pack(fill='x')
        self.start_button=ttk.Button(bar,text='开始测试',style='Accent.TButton',command=self.start);self.start_button.pack(side='left')
        ttk.Button(bar,text='停止测试',command=self.stop).pack(side='left',padx=10)
        self.status=ttk.Label(bar,text='尚未开始',style='Muted.TLabel');self.status.pack(side='left',padx=8)
        self.scroll=ui.ScrollFrame(self,content_gap=20);self.scroll.pack(fill='both',expand=True,padx=24,pady=(0,18))
        body=self.scroll.body
        self.rate=ttk.Label(body,text='— Hz',style='H1.TLabel');self.rate.pack(anchor='w')
        self.source=ttk.Label(body,text='移动鼠标后显示对应设备的输入频率',style='Muted.TLabel',wraplength=650);self.source.pack(anchor='w',pady=(4,10))
        ttk.Label(body,text='统计最近 1 秒收到的移动报告。请持续移动鼠标；这不是硬件设定回报率或精确延迟测试。',style='Muted.TLabel',wraplength=650).pack(anchor='w',pady=(0,12))
        surface=ui.Surface(body,padding=14);surface.pack(fill='x')
        self.mouse_buttons={}
        mouse_row=tk.Frame(surface.body,bg=ui.PANEL);mouse_row.pack(fill='x')
        for name in ('左键','中键','右键','侧键 1','侧键 2'):
            label=tk.Label(mouse_row,text=name+'\n0 次',bg=ui.PANEL,fg=ui.FG,padx=8,pady=8,highlightbackground=ui.LINE,highlightthickness=1)
            label.pack(side='left',fill='x',expand=True,padx=(0,6));self.mouse_buttons[name]=label
        self.events=tk.Label(surface.body,text='等待输入',bg=ui.PANEL,fg=ui.FG,anchor='w');self.events.pack(fill='x',pady=12)
        self.counts=tk.Label(surface.body,text='已测键位 0 · 同时按键峰值 0 · 滚轮累计 0',bg=ui.PANEL,fg=ui.MUTED,anchor='w');self.counts.pack(fill='x')
        keyboard=ui.Surface(body,padding=14);keyboard.pack(fill='x',pady=(12,0))
        tk.Label(keyboard.body,text='键盘按键 · 按下高亮',bg=ui.PANEL,fg=ui.FG,anchor='w').pack(fill='x',pady=(0,8))
        rows=[[(1,'Esc')]+[(59+i,f'F{i+1}') for i in range(10)]+[(87,'F11'),(88,'F12')],
              list(zip(range(2,14),'1 2 3 4 5 6 7 8 9 0 - ='.split()))+[(14,'退格')],
              [(15,'Tab')]+list(zip(range(16,28),'Q W E R T Y U I O P [ ]'.split())),
              [(58,'Caps')]+list(zip(range(30,41),"A S D F G H J K L ; '".split()))+[(28,'Enter')],
              [(42,'Shift')]+list(zip(range(44,54),'Z X C V B N M , . /'.split()))+[(54,'Shift')],
              [(29,'Ctrl'),(56,'Alt'),(57,'空格'),('E0 38','Alt'),('E0 1D','Ctrl'),('E0 4B','←'),('E0 48','↑'),('E0 50','↓'),('E0 4D','→')]]
        for values in rows:
            row=tk.Frame(keyboard.body,bg=ui.PANEL);row.pack(fill='x',pady=2)
            for scan,text in values:
                code=f'{scan:02X}' if isinstance(scan,int) else scan
                label=tk.Label(row,text=text,bg=ui.RAISED,fg=ui.FG,width=3,font=('Segoe UI',8),pady=5)
                label.pack(side='left',fill='x',expand=True,padx=2);self.key_labels[code]=label
        ttk.Label(body,text='仅当前测试页在前台时采集；切换页面自动停止。不保存输入内容。Fn 等未上报的键无法检测。',style='Muted.TLabel',wraplength=650).pack(anchor='w',pady=12)
        self.poll_id=self.after(100,self.poll)
        self.bind('<Destroy>',self.destroyed,add='+')

    def on_show(self):self.active=True

    def on_hide(self):self.active=False;self.stop()

    def start(self):
        if self.session or not self.active:return
        # Keep IME composition out of the test surface, restoring its own context on stop.
        self.scroll.canvas.focus_set()
        root_id=self.winfo_toplevel().winfo_id()
        targets={root_id,u.GetAncestor(root_id,2),self.scroll.canvas.winfo_id(),u.GetFocus()}
        self.ime_context=[(target,imm.ImmAssociateContext(target,None)) for target in targets if target]
        self.session=InputSession(u.GetAncestor(self.winfo_toplevel().winfo_id(),2))
        self.start_button.configure(state='disabled')
        self.status.configure(text='正在启动…')

    def stop(self):
        self.restore_ime()
        if self.session:
            self.session.stop()
            self.session.thread.join(timeout=1)
            if self.session.thread.is_alive():
                self.status.configure(text='正在停止…');return
            self.session=None
        self.start_button.configure(state='normal')
        self.status.configure(text='已停止 · 再次开始将清空统计')
        self.rate.configure(text='— Hz')
        self.refresh_theme()

    def restore_ime(self):
        if self.ime_context:
            for target,context in self.ime_context:
                imm.ImmAssociateContext(target,context)
            self.ime_context=None

    def refresh_theme(self):
        for label in self.key_labels.values():label.configure(bg=ui.RAISED,fg=ui.FG)
        for label in self.mouse_buttons.values():label.configure(bg=ui.PANEL,fg=ui.FG,highlightbackground=ui.LINE)

    def destroyed(self,event):
        if event.widget is self:
            self.restore_ime()
            if self.session:self.session.stop()
            try:self.after_cancel(self.poll_id)
            except tk.TclError:pass

    def poll(self):
        if self.session and self.session.stop_event.is_set():
            self.stop()
        if self.session:
            data=self.session.snapshot()
            if data['error']:
                error=data['error'];self.stop();self.status.configure(text='测试启动失败：'+error)
            else:
                foreground=data['foreground'] and self.active
                self.status.configure(text='测试中' if foreground else '窗口不在前台，已暂停采集')
                rates=data['rates'] if foreground else {}
                # Each device has its own rate; never add multiple mice together.
                device=max(rates,key=rates.get) if rates else None
                self.rate.configure(text=f'{rates[device]} Hz' if device else '— Hz')
                self.source.configure(text=' · '.join(f"{data['names'].get(d,'鼠标')} [{i+1}] {v} Hz" for i,(d,v) in enumerate(rates.items())) or '请持续移动鼠标')
                self.events.configure(text=data['last'])
                self.counts.configure(text=f"已测键位 {data['seen']} · 同时按键峰值 {data['peak_keys']} · 滚轮累计 {data['wheel']:g}")
                pressed=data['pressed'] if foreground else []
                for code,label in self.key_labels.items():label.configure(bg=ui.SELECTED if code in pressed else ui.RAISED,fg=ui.GREEN if code in pressed else ui.FG)
                for name,label in self.mouse_buttons.items():label.configure(text=f"{name}\n{data['clicks'].get(name,0)} 次",bg=ui.SELECTED if name in pressed else ui.PANEL)
        self.poll_id=self.after(100,self.poll)
