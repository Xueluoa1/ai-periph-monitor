"""Device filters operate on the latest snapshot, without restarting discovery."""
from tkinter import ttk
import tkinter as tk
import ui_theme as ui
from ui_cards import DeviceCard
from tray_pixels import transport_for_key


class DeviceList(ttk.Frame):
    FILTERS = (('all','全部'), ('usb','USB / 2.4G'), ('bluetooth','蓝牙'))

    def __init__(self,parent,on_model_change=None):
        super().__init__(parent)
        self.on_model_change=on_model_change
        self.factor=ui.scale(parent)
        self.readings=[]
        self.cards={}
        self.visible_cards=[]
        self.layout_signature=None
        self.selected=tk.StringVar(value='all')
        tabs=ttk.Frame(self)
        tabs.pack(fill='x',pady=(0,12))
        self.buttons={}
        for value,label in self.FILTERS:
            button=ttk.Radiobutton(tabs,text=label,variable=self.selected,value=value,
                                   style='Filter.TRadiobutton',command=self._select)
            button.pack(side='left',padx=(0,6))
            button.bind('<Left>',lambda e,v=value:self._step(v,-1))
            button.bind('<Right>',lambda e,v=value:self._step(v,1))
            self.buttons[value]=button
        self.scroll=ui.ScrollFrame(self)
        self.scroll.pack(fill='both',expand=True)
        self.empty=ttk.Frame(self.scroll.body,padding=(16,36))
        self.empty_title=ttk.Label(self.empty,style='H1.TLabel')
        self.empty_title.pack(anchor='center')
        ttk.Label(self.empty,text='连接并唤醒设备后点击“立即刷新”，或切换“全部”查看其他设备。',
                  style='Muted.TLabel',wraplength=380,justify='center').pack(pady=(12,0))
        self.scroll.canvas.bind('<Configure>',self._arrange,add='+')

    def _step(self,current,direction):
        keys=list(self.buttons)
        next_key=keys[(keys.index(current)+direction)%len(keys)]
        self.buttons[next_key].invoke()
        self.buttons[next_key].focus_set()
        return 'break'

    def _select(self):
        self._show_filtered()
        self.scroll.canvas.yview_moveto(0)

    def set_readings(self,readings):
        self.readings=list(readings)
        ids=[r.key or f'placeholder:{i}' for i,r in enumerate(self.readings)]
        for key in set(self.cards)-set(ids):
            self.cards.pop(key).frame.destroy()
        for key,r in zip(ids,self.readings):
            if key not in self.cards:
                self.cards[key]=DeviceCard(self.scroll.body,r.title,self.on_model_change)
            self.cards[key].update(r)
        for kind,label in self.FILTERS:
            count=sum(bool(r.key) and (kind=='all' or transport_for_key(r.key)==kind) for r in self.readings)
            self.buttons[kind].configure(text=f'{label}  {count}')
        self._show_filtered()

    def _show_filtered(self):
        selected=self.selected.get()
        self.visible_cards=[]
        for i,r in enumerate(self.readings):
            card=self.cards[r.key or f'placeholder:{i}']
            if selected=='all' or (r.key and transport_for_key(r.key)==selected):
                self.visible_cards.append(card)
            else:
                card.frame.grid_remove()
        self.empty.grid_remove()
        if not self.visible_cards:
            label=dict(self.FILTERS)[selected]
            self.empty_title.configure(text=f'暂无{label if selected!="all" else ""}设备读数')
        self.layout_signature=None
        self._arrange()

    def _arrange(self,_event=None):
        width=self.scroll.canvas.winfo_width()
        columns=3 if width>=1080*self.factor else 2 if width>=700*self.factor else 1
        signature=(columns,tuple(id(card) for card in self.visible_cards))
        if signature==self.layout_signature:
            return
        self.layout_signature=signature
        body=self.scroll.body
        for col in range(3):
            body.columnconfigure(col,weight=1 if col<columns else 0,uniform='cards' if col<columns else '')
        for i,card in enumerate(self.visible_cards):
            row,col=divmod(i,columns)
            card.frame.grid(row=row,column=col,sticky='nsew',
                padx=(0,round(14*self.factor) if col<columns-1 else 0),pady=(0,round(14*self.factor)))
        if not self.visible_cards:
            self.empty.grid(row=0,column=0,columnspan=columns,sticky='ew')
