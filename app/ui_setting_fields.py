"""Shared color and integer editors. Editing never performs device IO."""
import tkinter as tk
from tkinter import ttk, colorchooser

import ui_theme as ui


def valid_color(text):
    value=text.strip().removeprefix('#')
    return len(value)==6 and all(c in '0123456789abcdefABCDEF' for c in value)


class ColorPicker(tk.Frame):
    def __init__(self,parent,variable):
        super().__init__(parent,bg=ui.PANEL)
        self.variable=variable
        self.swatch=tk.Canvas(self,width=26,height=26,bg=ui.PANEL,highlightthickness=1,highlightbackground=ui.LINE)
        self.swatch.pack(side='left',padx=(0,8))
        self.entry=ttk.Entry(self,textvariable=variable,width=10)
        self.entry.pack(side='left')
        self.choose_button=ttk.Button(self,text='选择颜色…',command=self.choose)
        self.choose_button.pack(side='left',padx=(8,0))
        self.trace=variable.trace_add('write',self.refresh)
        self.bind('<Destroy>',self.destroyed,add='+');self.refresh()

    def refresh(self,*args):
        self.swatch.delete('all')
        color=self.variable.get()
        if valid_color(color):
            self.swatch.create_rectangle(1,1,27,27,fill='#'+color.strip().removeprefix('#'),outline='')
        else:self.swatch.create_line(3,23,23,3,fill=ui.MUTED,width=2)

    def choose(self):
        if self.choose_button.instate(['disabled']):return
        value=self.variable.get()
        initial='#'+value.strip().removeprefix('#') if valid_color(value) else '#FFFFFF'
        _,chosen=colorchooser.askcolor(initialcolor=initial,parent=self,title='选择灯光颜色')
        if chosen:self.variable.set(chosen.upper())

    def destroyed(self,event):
        if event.widget is self:self.variable.trace_remove('write',self.trace)


class PalettePicker(tk.Frame):
    """One to eight individually selectable colors; empty retains device data."""
    def __init__(self,parent,variable):
        super().__init__(parent,bg=ui.PANEL)
        self.variable=variable
        self.items=tk.Frame(self,bg=ui.PANEL);self.items.pack(fill='x')
        bar=tk.Frame(self,bg=ui.PANEL);bar.pack(anchor='w',pady=(5,0))
        self.add_button=ttk.Button(bar,text='添加颜色…',command=self.add)
        self.add_button.pack(side='left')
        self.clear_button=ttk.Button(bar,text='保留原图案',command=lambda:self.variable.set(''))
        self.clear_button.pack(side='left',padx=(8,0))
        self.trace=variable.trace_add('write',self.refresh)
        self.bind('<Destroy>',self.destroyed,add='+');self.refresh()

    def colors(self):return [c.strip() for c in self.variable.get().replace('，',',').split(',') if c.strip()]

    def refresh(self,*args):
        for child in self.items.winfo_children():child.destroy()
        values=self.colors()
        if not values:
            tk.Label(self.items,text='保留设备现有图案',bg=ui.PANEL,fg=ui.MUTED).pack(anchor='w')
        for index,color in enumerate(values):
            row=tk.Frame(self.items,bg=ui.PANEL);row.pack(anchor='w',pady=3)
            swatch=tk.Canvas(row,width=22,height=22,highlightthickness=1,highlightbackground=ui.LINE,bg=ui.PANEL)
            swatch.pack(side='left',padx=(0,8))
            if valid_color(color):swatch.create_rectangle(0,0,24,24,fill='#'+color.removeprefix('#'),outline='')
            ttk.Button(row,text=f'{index+1}  {color}',command=lambda i=index:self.edit(i)).pack(side='left')
            ttk.Button(row,text='移除',command=lambda i=index:self.remove(i)).pack(side='left',padx=(8,0))

    def edit(self,index):
        if self.add_button.instate(['disabled']):return
        values=self.colors()
        _,chosen=colorchooser.askcolor(initialcolor=values[index] if valid_color(values[index]) else '#FFFFFF',parent=self,title=f'选择颜色 {index+1}')
        if chosen:
            values[index]=chosen.upper();self.variable.set(','.join(values))

    def add(self):
        if self.add_button.instate(['disabled']):return
        values=self.colors()
        if len(values)>=8:return
        _,chosen=colorchooser.askcolor(initialcolor=values[-1] if values and valid_color(values[-1]) else '#FF6E00',parent=self,title='添加灯光颜色（最多 8 个）')
        if chosen:self.variable.set(','.join(values+[chosen.upper()]))

    def remove(self,index):
        if self.add_button.instate(['disabled']):return
        values=self.colors();values.pop(index);self.variable.set(','.join(values))

    def destroyed(self,event):
        if event.widget is self:self.variable.trace_remove('write',self.trace)


class IntegerSlider(tk.Frame):
    def __init__(self,parent,variable,minimum,maximum):
        super().__init__(parent,bg=ui.PANEL)
        self.variable,self.minimum,self.maximum=variable,minimum,maximum
        self.syncing=False
        self.position=tk.DoubleVar(value=minimum)
        self.scale=ttk.Scale(self,from_=minimum,to=maximum,variable=self.position,
                             command=self.slid,orient='horizontal',length=170,takefocus=True)
        self.scale.pack(side='left',fill='x',expand=True)
        self.entry=ttk.Entry(self,textvariable=variable,width=5,justify='right')
        self.entry.pack(side='left',padx=(10,0))
        for key,delta in (('Left',-1),('Down',-1),('Right',1),('Up',1)):
            self.scale.bind('<'+key+'>',lambda e,d=delta:self.step(d))
        self.scale.bind('<Home>',lambda e:self.endpoint(minimum))
        self.scale.bind('<End>',lambda e:self.endpoint(maximum))
        self.trace=variable.trace_add('write',self.refresh)
        self.bind('<Destroy>',self.destroyed,add='+');self.refresh()

    def slid(self,value):
        if self.syncing or self.scale.instate(['disabled']):return
        number=max(self.minimum,min(self.maximum,int(float(value)+.5)))
        self.variable.set(str(number))

    def refresh(self,*args):
        try:number=int(self.variable.get())
        except ValueError:return
        if not self.minimum<=number<=self.maximum:return
        self.syncing=True
        try:self.position.set(number)
        finally:self.syncing=False

    def step(self,delta):
        if not self.scale.instate(['disabled']):
            self.variable.set(str(max(self.minimum,min(self.maximum,round(self.position.get())+delta))))
        return 'break'

    def endpoint(self,value):
        if not self.scale.instate(['disabled']):self.variable.set(str(value))
        return 'break'

    def destroyed(self,event):
        if event.widget is self:self.variable.trace_remove('write',self.trace)
