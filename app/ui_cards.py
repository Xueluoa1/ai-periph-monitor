"""Device-card presentation; persistence delegates to the original config API."""
import time
import tkinter as tk
from tkinter import ttk
import battery as bt
import tray_common as tc
import ui_theme as ui
from brand_icons import BrandIcon
from ui_connection import ConnectionBadge, reading_color


class DeviceCard:
    def __init__(self,parent,title,on_model_change=None):
        self.on_model_change=on_model_change
        self.current_key=''
        self.current_title=title
        self.frame=ui.Surface(parent,padding=14)
        body=self.frame.body
        top=tk.Frame(body,bg=ui.PANEL);top.pack(fill='x')
        self.icon=BrandIcon(top);self.icon.pack(side='left',padx=(0,12),anchor='n')
        self.connection=ConnectionBadge(top)
        self.connection.pack(side='right',anchor='n',padx=(10,0))
        names=tk.Frame(top,bg=ui.PANEL);names.pack(side='left',fill='x',expand=True)
        self.lab_title=ttk.Label(names,text=title,style='CardTitle.TLabel',wraplength=240,justify='left')
        self.lab_title.pack(anchor='w',fill='x')
        self.lab_sub=ttk.Label(names,style='CardSub.TLabel',wraplength=240,justify='left')
        self.lab_sub.pack(anchor='w',fill='x',pady=(3,0))
        names.bind('<Configure>',lambda e:[label.configure(wraplength=max(80,e.width-2)) for label in (self.lab_title,self.lab_sub)])
        number=tk.Frame(body,bg=ui.PANEL);number.pack(fill='x',pady=(10,6))
        self.lab_level=ttk.Label(number,text='--',style='CardBig.TLabel');self.lab_level.pack(side='left')
        self.lab_status=ttk.Label(number,style='CardSub.TLabel',wraplength=150);self.lab_status.pack(side='right')
        self.lab_cached=ttk.Label(number,style='CardSub.TLabel');self.lab_cached.pack(side='right',anchor='s',pady=8)
        self.bar=tk.Canvas(body,height=round(5*ui.scale(parent)),bg=ui.LINE,highlightthickness=0)
        self.bar.pack(fill='x',pady=(0,8));self.level=None
        self.bar.bind('<Configure>',lambda e:self._bar())
        self.lab_detail=ttk.Label(body,style='CardSub.TLabel',wraplength=290,justify='left')
        self.lab_detail.pack(anchor='w',fill='x')
        self.lab_extra=ttk.Label(body,style='CardSub.TLabel',wraplength=290,justify='left')
        self.lab_extra.pack(anchor='w',fill='x',pady=(3,6))
        self.separator=ttk.Separator(body);self.separator.pack(fill='x')
        bottom=tk.Frame(body,bg=ui.PANEL);bottom.pack(fill='x',pady=(5,0))
        self.time_label=ttk.Label(bottom,style='CardSub.TLabel');self.time_label.pack(side='left')
        self.btn_edit=ttk.Button(bottom,text='设置型号',style='Card.TButton',command=self._open_model_dialog)
        self.btn_edit.pack(side='right')
        body.bind('<Configure>',self._wrap,add='+')

    def _wrap(self,event):
        for label in (self.lab_detail,self.lab_extra):
            label.configure(wraplength=max(150,event.width-2))

    def _bar(self):
        self.bar.delete('all')
        if self.level is not None:
            self.bar.create_rectangle(0,0,self.bar.winfo_width()*max(0,min(100,self.level))/100,
                                      self.bar.winfo_height(),fill=reading_color(self.level,self.current_key),outline='')

    def update(self,r):
        self.current_key,self.current_title=r.key,r.title
        self.connection.show(r.key)
        if r.key:
            self.connection.pack(side='right',anchor='n',padx=(10,0),before=self.lab_title.master)
        else:
            self.connection.pack_forget()
        kind=ui.device_kind(r.title+' '+r.subtitle)
        self.icon.show(r.title,kind)
        self.lab_title.configure(text=r.title.lstrip('🖱️⌨️🎧 '))
        device_type={'mouse':'鼠标','keyboard':'键盘','headset':'耳机','controller':'手柄','speaker':'音箱'}[kind]
        self.lab_sub.configure(text=r.subtitle if device_type in r.subtitle else f'{device_type} · {r.subtitle}' if r.subtitle else device_type)
        self.btn_edit.configure(state='normal' if r.key else 'disabled')
        self.level=r.level
        sleep='休眠' in (r.status or '')
        status=r.status if r.status not in ('未知','状态未知') else ''
        if r.ok:
            self.lab_level.configure(text=f'{r.level}%',foreground=reading_color(r.level,r.key))
            self.lab_status.configure(text=status or ('电量偏低' if r.level<=10 else '已连接'),
                                      foreground=ui.YELLOW if sleep else reading_color(r.level,r.key))
            self.lab_cached.configure(text='上次电量' if sleep else '充电中' if '充电' in status else '')
            self.lab_detail.configure(text=r.detail)
            self.lab_extra.configure(text=r.extra)
        elif r.level_label:
            self.lab_level.configure(text=r.level_label,foreground=ui.FG)
            self.lab_status.configure(text='电量档位',foreground=ui.MUTED)
            self.lab_cached.configure(text='')
            self.lab_detail.configure(text=r.detail)
            self.lab_extra.configure(text=r.extra)
        else:
            self.lab_level.configure(text='--',foreground=ui.MUTED)
            self.lab_status.configure(text='暂无读数' if r.key else '',foreground=ui.MUTED)
            self.lab_cached.configure(text='')
            self.lab_detail.configure(text=r.error or '正在读取设备信息…')
            self.lab_extra.configure(text=r.extra)
        self.time_label.configure(text='系统缓存 · 上报时间未知' if '系统缓存' in status else '缓存读数' if sleep else f'{time.strftime("%H:%M:%S")} 更新' if r.ok or r.level_label else '等待更新')
        for label in (self.lab_detail,self.lab_extra):
            if label.cget('text'):
                label.pack(anchor='w',fill='x',pady=(0,5),before=self.separator)
            else:
                label.pack_forget()
        self._bar()

    def _open_model_dialog(self):
        if not self.current_key:return
        parent=self.frame.winfo_toplevel()
        dlg=tk.Toplevel(parent);dlg.withdraw();dlg.title('设置设备型号');dlg.configure(bg=ui.BG)
        dlg.transient(parent);ui.center_dialog(dlg,parent,560,370)
        dlg.resizable(True,False)
        box=ttk.Frame(dlg,padding=24);box.pack(fill='both',expand=True)
        ttk.Label(box,text='设置设备型号',style='H1.TLabel').pack(anchor='w')
        ttk.Label(box,text='仅修改显示名称，保留现有设备识别与电量读取方式。',style='Muted.TLabel').pack(anchor='w',pady=(8,20))
        ttk.Label(box,text='选择预设或输入完整型号').pack(anchor='w',pady=(0,8))
        cfg=tc.load_config();models=dict(cfg.get('custom_models') or {})
        var=tk.StringVar(value=models.get(self.current_key,self.current_title))
        combo=ttk.Combobox(box,textvariable=var,values=bt.PRESET_MODEL_OPTIONS)
        combo.pack(fill='x')
        error=ttk.Label(box,text='',foreground=ui.RED);error.pack(anchor='w',pady=(6,2))
        ttk.Label(box,text=f'硬件通道：{self.current_key}',style='Muted.TLabel',wraplength=480).pack(anchor='w')
        buttons=ttk.Frame(box);buttons.pack(side='bottom',fill='x',pady=(16,0))
        def close():
            dlg.grab_release();dlg.destroy();self.btn_edit.focus_set()
        def save(reset=False):
            value=var.get().strip()
            if not reset and not value:
                error.configure(text='请输入型号，或使用“恢复自动识别”。');combo.focus_set();return
            new_cfg=tc.load_config();new_models=dict(new_cfg.get('custom_models') or {})
            if reset or value=='自动识别（默认）':new_models.pop(self.current_key,None)
            else:new_models[self.current_key]=value
            new_cfg['custom_models']=new_models;tc.save_config(new_cfg);tc.post_to_tray(tc.MSG_RELOAD)
            close()
            if self.on_model_change:self.on_model_change()
        ttk.Button(buttons,text='恢复自动识别',style='Quiet.TButton',command=lambda:save(True)).pack(side='left')
        ttk.Button(buttons,text='保存型号',style='Accent.TButton',command=save).pack(side='right')
        ttk.Button(buttons,text='取消',command=close).pack(side='right',padx=8)
        dlg.protocol('WM_DELETE_WINDOW',close);dlg.bind('<Escape>',lambda e:close())
        dlg.bind('<Return>',lambda e:save())
        dlg.deiconify();dlg.grab_set();combo.focus_set()
