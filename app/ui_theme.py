"""Shared Tkinter presentation primitives. No hardware or configuration writes."""
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk
from ui_switch_pixels import render as render_switch

BG = '#f7f8fa'
PANEL = '#ffffff'
RAISED = '#f0f3f5'
LINE = '#e3e7ec'
FG = '#202731'
MUTED = '#707985'
ACCENT = '#00b88a'
GREEN = '#00875f'
YELLOW = '#9b6800'
RED = '#d93443'
FONT = ('Microsoft YaHei UI', 10)
THEMES = {
    'light': dict(BG=BG, PANEL=PANEL, RAISED=RAISED, LINE=LINE, FG=FG, MUTED=MUTED,
                  ACCENT=ACCENT, GREEN=GREEN, YELLOW=YELLOW, RED=RED,
                  SELECTED='#e6f8f1', HOVER='#e7f5f0', DISABLED='#f2f3f5',
                  DISABLED_FG='#929aa4', ACCENT_FG='#ffffff', ACCENT_HOVER='#009f78',
                  ACCENT_DISABLED='#c5ded5', ACCENT_DISABLED_FG='#657b73', SWITCH_OFF='#c5cad1'),
    'dark': dict(BG='#1b1d21', PANEL='#24272c', RAISED='#2c3036', LINE='#3d424a',
                 FG='#e7eaf0', MUTED='#a6afbc', ACCENT='#51d3ad', GREEN='#67dbab',
                 YELLOW='#e5bf72', RED='#ff7782', SELECTED='#233e36', HOVER='#30463e',
                 DISABLED='#272b31', DISABLED_FG='#858e9b', ACCENT_FG='#12392e',
                 ACCENT_HOVER='#79e2c1', ACCENT_DISABLED='#365a4e', ACCENT_DISABLED_FG='#b2c4bd',
                 SWITCH_OFF='#59616d'),
}
CURRENT_THEME = 'light'


def select_theme(name):
    global CURRENT_THEME
    CURRENT_THEME = name if name in THEMES else 'light'
    globals().update(THEMES[CURRENT_THEME])


select_theme('light')


def apply_theme(root, name):
    """Recolor existing controls in place, preserving focus, input and scroll state."""
    previous = dict(THEMES[CURRENT_THEME])
    old_light = CURRENT_THEME == 'light'
    select_theme(name)
    # Shared token colors and connection-state colors are the only colors remapped.
    colors = {value: THEMES[CURRENT_THEME][key] for key, value in previous.items()
              if key != 'ACCENT_FG'}
    from tray_pixels import number_color
    for value in (None, 0, 80):
        for transport in ('usb', 'bluetooth', 'unknown'):
            before = '#%02x%02x%02x' % number_color(value, transport, old_light)[:3]
            colors[before] = '#%02x%02x%02x' % number_color(value, transport, CURRENT_THEME == 'light')[:3]
    def recolor(widget):
        options = widget.keys()
        for option in ('background', 'foreground', 'highlightbackground', 'highlightcolor',
                       'activebackground', 'activeforeground', 'disabledforeground',
                       'selectbackground', 'selectforeground', 'insertbackground', 'troughcolor'):
            if option in options:
                color = str(widget.cget(option))
                if color in colors:
                    widget.configure(**{option: colors[color]})
        if isinstance(widget, tk.Canvas):
            for item in widget.find_all():
                for option in ('fill', 'outline'):
                    if option in widget.itemconfigure(item):
                        color = widget.itemcget(item, option)
                        if color in colors:
                            widget.itemconfigure(item, **{option: colors[color]})
        for child in widget.winfo_children():
            recolor(child)
        if isinstance(widget, Switch):
            widget.draw()
        callback = getattr(widget, 'refresh_theme', None)
        if callback:
            callback()
        if isinstance(widget, (tk.Tk, tk.Toplevel)):
            theme_caption(widget)
    recolor(root)
    configure(root)
    # Reconfigure existing combobox popdowns, which live outside winfo_children().
    def popdowns(widget):
        if isinstance(widget, ttk.Combobox):
            path = str(widget.tk.call('ttk::combobox::PopdownWindow', str(widget))) + '.f.l'
            widget.tk.call(path, 'configure', '-background', RAISED, '-foreground', FG,
                           '-selectbackground', SELECTED, '-selectforeground', FG)
        for child in widget.winfo_children():
            popdowns(child)
    popdowns(root)


def prepare_process():
    """Give our own windows the battery app identity, including source launches."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('DeviceBatteryViewer.UI')
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def asset_path(name):
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / name


def set_icon(window):
    try:
        window.iconbitmap(str(asset_path('app_icon.ico')))
    except tk.TclError:
        pass
    window.after_idle(lambda: theme_caption(window))


def theme_caption(window):
    """Ask Windows to use a matching title bar when supported."""
    try:
        import ctypes
        if not window.winfo_exists() or window.overrideredirect():
            return
        user = ctypes.windll.user32
        user.GetParent.argtypes = [ctypes.c_void_p]
        user.GetParent.restype = ctypes.c_void_p
        hwnd = user.GetParent(window.winfo_id()) or window.winfo_id()
        enabled = ctypes.c_int(CURRENT_THEME == 'dark')
        ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 20,
                                                 ctypes.byref(enabled), ctypes.sizeof(enabled))
    except (AttributeError, OSError, tk.TclError):
        pass


def scale(widget):
    return max(1.0, float(widget.winfo_fpixels('1i')) / 96.0)


def device_kind(title, headset=False):
    title = title.lower()
    if headset or any(k in title for k in ('headset', 'headphone', '耳机', 'earphone', 'buds', 'darkrim')):
        return 'headset'
    if any(k in title for k in ('keyboard', '键盘', 'cyberboard', 'azoth', 'falchion', 'scope')):
        return 'keyboard'
    return 'mouse'


def level_color(value):
    return MUTED if value is None else GREEN if value >= 60 else YELLOW if value >= 30 else RED


def configure_selection_indicator(root, style):
    """Square outline with an inset filled square; retain native focus/keyboard behavior."""
    owner=root._root()
    images=getattr(owner,'_selection_images',None)
    if images is None:
        images={key:tk.PhotoImage(master=owner) for key in ('off','on','disabled_off','disabled_on')}
        owner._selection_images=images
        style.element_create('SquareSelection.indicator','image',images['off'],
                             ('disabled','selected',images['disabled_on']),
                             ('disabled',images['disabled_off']),('selected',images['on']),
                             border=0,sticky='')
    size=round(18*scale(root));stroke=max(2,round(2*scale(root)));inset=round(5*scale(root))
    for key,img in images.items():
        img.configure(width=size,height=size);img.blank()
        border=DISABLED_FG if key.startswith('disabled') else MUTED
        img.put(border,to=(0,0,size,stroke));img.put(border,to=(0,size-stroke,size,size))
        img.put(border,to=(0,0,stroke,size));img.put(border,to=(size-stroke,0,size,size))
        if key.endswith('on'):
            img.put(DISABLED_FG if key.startswith('disabled') else ACCENT,to=(inset,inset,size-inset,size-inset))
    for control in ('Checkbutton','Radiobutton'):
        style.layout('T'+control,[(control+'.padding',{'sticky':'nswe','children':[
            ('SquareSelection.indicator',{'side':'left','sticky':''}),
            (control+'.focus',{'side':'left','sticky':'w','children':[
                (control+'.label',{'sticky':'nswe'})]})]})])


def configure(root):
    style = ttk.Style(root)
    if style.theme_use() != 'clam':
        style.theme_use('clam')
    configure_selection_indicator(root,style)
    style.configure('.', background=BG, foreground=FG, font=FONT)
    for name, bg in [('TFrame', BG), ('Card.TFrame', PANEL), ('Panel.TFrame', PANEL)]:
        style.configure(name, background=bg)
    for name, bg, fg, font in [
        ('TLabel', BG, FG, FONT), ('H1.TLabel', BG, FG, ('Microsoft YaHei UI', 19, 'bold')),
        ('Muted.TLabel', BG, MUTED, ('Microsoft YaHei UI', 9)),
        ('CardTitle.TLabel', PANEL, FG, ('Microsoft YaHei UI', 10)),
        ('CardSub.TLabel', PANEL, MUTED, ('Microsoft YaHei UI', 9)),
        ('CardBig.TLabel', PANEL, GREEN, ('Segoe UI', 28)),
        ('Group.TLabel', BG, ACCENT, ('Microsoft YaHei UI', 9, 'bold')),
    ]:
        style.configure(name, background=bg, foreground=fg, font=font)
    style.configure('TButton', background=RAISED, foreground=FG, padding=(14, 9),
                    borderwidth=1, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE,
                    focusthickness=1, focuscolor=ACCENT)
    style.map('TButton', background=[('active', HOVER), ('disabled', DISABLED)],
              foreground=[('disabled', DISABLED_FG)])
    style.configure('Accent.TButton', background=ACCENT, foreground=ACCENT_FG,
                    bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT)
    style.map('Accent.TButton', background=[('active', ACCENT_HOVER), ('disabled', ACCENT_DISABLED)],
              foreground=[('disabled', ACCENT_DISABLED_FG)])
    style.configure('Quiet.TButton', background=BG, foreground=MUTED, padding=(10, 8))
    style.configure('Card.TButton', background=PANEL, foreground=MUTED, padding=(8, 8))
    style.layout('Filter.TRadiobutton',style.layout('Toolbutton'))
    style.configure('Filter.TRadiobutton',background=BG,foreground=MUTED,padding=(14,9),
                    borderwidth=0,focusthickness=2,focuscolor=ACCENT)
    style.map('Filter.TRadiobutton',background=[('selected',SELECTED),('active',RAISED)],
              foreground=[('selected',GREEN),('active',GREEN)])
    style.configure('Nav.TButton', background=PANEL, foreground=MUTED, padding=(0,10),
                    borderwidth=0)
    style.configure('NavActive.TButton', background=SELECTED, foreground=GREEN,
                    padding=(0,10), borderwidth=0)
    style.configure('TCheckbutton', background=BG, foreground=FG, padding=6)
    style.map('TCheckbutton', background=[('active', RAISED)])
    style.configure('TRadiobutton',background=BG,foreground=FG,padding=6)
    style.configure('Stage.TRadiobutton',background=PANEL,foreground=FG,padding=(0,8,10,8))
    style.map('Stage.TRadiobutton',background=[('active',PANEL)],foreground=[('selected',GREEN)])
    style.configure('TCombobox', fieldbackground=RAISED, background=RAISED, foreground=FG,
                    arrowcolor=FG, padding=7, selectbackground=RAISED, selectforeground=FG,
                    bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)
    style.map('TCombobox', fieldbackground=[('readonly', RAISED)], foreground=[('readonly', FG)])
    style.configure('TEntry', fieldbackground=RAISED, foreground=FG, insertcolor=FG, padding=8,
                    bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)
    style.map('TEntry', fieldbackground=[('disabled',DISABLED)], foreground=[('disabled',DISABLED_FG)])
    style.configure('TSeparator', background=LINE, lightcolor=LINE, darkcolor=LINE)
    # Keep the requested white track and green thumb in both app themes.
    style.layout('Vertical.TScrollbar', [
        ('Vertical.Scrollbar.trough', {'sticky': 'ns', 'children': [
            ('Vertical.Scrollbar.thumb', {'expand': '1', 'sticky': 'nswe'})
        ]})
    ])
    style.configure('Vertical.TScrollbar', background='#00b88a', troughcolor='#ffffff',
                    bordercolor='#ffffff', lightcolor='#00b88a', darkcolor='#00b88a',
                    borderwidth=0, relief='flat', width=round(12 * scale(root)),
                    gripcount=0, arrowsize=round(12 * scale(root)))
    for option in ('background', 'lightcolor', 'darkcolor'):
        style.map('Vertical.TScrollbar', **{option: [
            ('pressed', '#008f6b'), ('active', '#009f78'), ('disabled', '#ffffff')
        ]})
    root.option_add('*TCombobox*Listbox.background', RAISED)
    root.option_add('*TCombobox*Listbox.foreground', FG)
    root.option_add('*TCombobox*Listbox.selectBackground', SELECTED)
    set_icon(root)
    return style


class Surface(tk.Canvas):
    """Square card with a native child frame, sized from its requested content."""
    def __init__(self, parent, padding=18, **kwargs):
        self.factor = scale(parent)
        self.pad = round(padding * self.factor)
        super().__init__(parent, bg=BG, highlightthickness=0, borderwidth=0, **kwargs)
        self.body = tk.Frame(self, bg=PANEL)
        self.item = self.create_window(self.pad, self.pad, anchor='nw', window=self.body)
        self.bind('<Configure>', self._resize)
        self.body.bind('<Configure>', self._content)

    def _content(self, _event=None):
        height = self.body.winfo_reqheight() + 2 * self.pad
        if int(float(self.cget('height'))) != height:
            self.configure(height=height)

    def _resize(self, event):
        self.itemconfigure(self.item, width=max(1, event.width - 2*self.pad))
        self.delete('surface')
        w, h = event.width-1, event.height-1
        self.create_rectangle(1, 1, w, h, fill=PANEL, outline=LINE, tags='surface')
        self.tag_lower('surface')


class DeviceGlyph(tk.Canvas):
    def __init__(self, parent, kind='mouse', size=36, bg=None, color=None):
        self.logical_size = size
        self.factor = scale(parent)
        n = round(size*self.factor)
        super().__init__(parent, width=n, height=n, bg=bg or PANEL, highlightthickness=0)
        self.draw(kind, color)

    def draw(self, kind, color=None):
        color = color or FG
        self.delete('all')
        s = self.logical_size*self.factor/24
        def line(points):
            self.create_line(*[v*s for v in points], fill=color, width=max(1.5,1.7*s),
                             capstyle='round', joinstyle='round')
        if kind == 'keyboard':
            self.create_rectangle(2*s,5*s,22*s,19*s,outline=color,width=1.6*s)
            for y in (9,12):
                for x in (6,11,16): line((x,y,x+1,y))
            line((7,16,17,16))
        elif kind == 'headset':
            self.create_arc(3*s,2*s,21*s,20*s,start=0,extent=180,style='arc',outline=color,width=2*s)
            for x in (3,17):
                self.create_rectangle(x*s,11*s,(x+4)*s,20*s,outline=color,width=1.7*s)
        else:
            self.create_arc(5*s,2*s,19*s,16*s,start=0,extent=180,style='arc',outline=color,width=1.7*s)
            line((5,9,5,11,19,11,19,9))
            line((12,2,12,11))
            self.create_rectangle(5*s,11*s,19*s,15*s,fill=color,outline='')
            self.create_arc(5*s,8*s,19*s,22*s,start=180,extent=180,fill=color,outline=color)


class Switch(tk.Canvas):
    def __init__(self, parent, variable, command, bg=None):
        self.variable, self.command = variable, command
        self.factor = scale(parent)
        super().__init__(parent,width=round(44*self.factor),height=round(40*self.factor),
                         bg=bg or PANEL,highlightthickness=0,takefocus=True,cursor='hand2')
        self.bind('<Button-1>', self.toggle)
        self.bind('<space>', self.toggle)
        self.bind('<Return>', self.toggle)
        self.bind('<FocusIn>', lambda e:self.draw())
        self.bind('<FocusOut>', lambda e:self.draw())
        self.trace = variable.trace_add('write', lambda *args:self.draw())
        self.bind('<Destroy>', self._destroy)
        self.draw()

    def _destroy(self, event):
        if event.widget is self:
            self.variable.trace_remove('write',self.trace)

    def toggle(self, _event=None):
        self.focus_set()
        self.variable.set(not self.variable.get())
        self.command()
        return 'break'

    def draw(self):
        if not self.winfo_exists(): return
        self.delete('all')
        s=self.factor
        color=ACCENT if self.variable.get() else SWITCH_OFF
        def rgb(value):
            return tuple(channel // 257 for channel in self.winfo_rgb(value))
        pixels = render_switch(s, bool(self.variable.get()), rgb(self.cget('bg')), rgb(color))
        self._image = tk.PhotoImage(master=self, data=pixels, format='PPM')
        self.create_image(0, 0, anchor='nw', image=self._image)
        if self.focus_get() is self:
            self.create_rectangle(1,1,43*s,39*s,outline=ACCENT,width=1)


class ScrollFrame(ttk.Frame):
    def __init__(self, parent, content_gap=0):
        super().__init__(parent)
        self.canvas=tk.Canvas(self,bg=BG,highlightthickness=0)
        self.bar=ttk.Scrollbar(self,orient='vertical',command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.bar.pack(side='right',fill='y',padx=(round(content_gap * scale(parent)),0))
        self.canvas.pack(side='left',fill='both',expand=True)
        self.body=ttk.Frame(self.canvas)
        self.item=self.canvas.create_window(0,0,window=self.body,anchor='nw')
        self.body.bind('<Configure>',lambda e:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>',lambda e:self.canvas.itemconfigure(self.item,width=e.width))
        self.winfo_toplevel().bind('<MouseWheel>',self._wheel,add='+')

    def _wheel(self,event):
        widget=self.winfo_containing(event.x_root,event.y_root)
        while widget is not None:
            if widget is self:
                self.canvas.yview_scroll(-int(event.delta/120),'units')
                return 'break'
            widget=getattr(widget,'master',None)


def center_dialog(dialog, parent, width=540, height=460):
    s=scale(parent)
    width=min(round(width*s),parent.winfo_screenwidth()-60)
    height=min(round(height*s),parent.winfo_screenheight()-100)
    x=max(0,min(parent.winfo_x()+(parent.winfo_width()-width)//2,parent.winfo_screenwidth()-width))
    y=max(0,min(parent.winfo_y()+(parent.winfo_height()-height)//2,parent.winfo_screenheight()-height))
    dialog.geometry(f'{width}x{height}+{x}+{y}')
    set_icon(dialog)
