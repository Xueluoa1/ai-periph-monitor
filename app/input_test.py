"""Foreground-only Windows Raw Input test session, no input injection or files."""
import ctypes as c
from ctypes import wintypes as w
from collections import Counter, deque
import threading
import time

u = c.WinDLL('user32',use_last_error=True)
k = c.WinDLL('kernel32',use_last_error=True)
imm = c.WinDLL('imm32',use_last_error=True)
imm.ImmAssociateContext.argtypes=[w.HWND,w.HANDLE]
imm.ImmAssociateContext.restype=w.HANDLE
LRESULT = c.c_ssize_t
WNDPROC = c.WINFUNCTYPE(LRESULT,w.HWND,w.UINT,w.WPARAM,w.LPARAM)

class WindowClass(c.Structure):
    _fields_=[('style',w.UINT),('proc',WNDPROC),('cls_extra',c.c_int),('wnd_extra',c.c_int),
              ('instance',w.HINSTANCE),('icon',w.HICON),('cursor',w.HANDLE),('brush',w.HBRUSH),
              ('menu',w.LPCWSTR),('name',w.LPCWSTR)]

class RawDevice(c.Structure):
    _fields_=[('page',w.USHORT),('usage',w.USHORT),('flags',w.DWORD),('target',w.HWND)]

class RawHeader(c.Structure):
    _fields_=[('kind',w.DWORD),('size',w.DWORD),('device',w.HANDLE),('param',w.WPARAM)]

u.DefWindowProcW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM];u.DefWindowProcW.restype=LRESULT
u.CreateWindowExW.argtypes=[w.DWORD,w.LPCWSTR,w.LPCWSTR,w.DWORD,c.c_int,c.c_int,c.c_int,c.c_int,w.HWND,w.HMENU,w.HINSTANCE,c.c_void_p]
u.CreateWindowExW.restype=w.HWND
u.RegisterClassW.argtypes=[c.POINTER(WindowClass)];u.RegisterClassW.restype=w.ATOM
u.UnregisterClassW.argtypes=[w.LPCWSTR,w.HINSTANCE]
u.DestroyWindow.argtypes=[w.HWND]
u.PostMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM]
u.RegisterRawInputDevices.argtypes=[c.POINTER(RawDevice),w.UINT,w.UINT]
u.GetRawInputData.argtypes=[w.HANDLE,w.UINT,c.c_void_p,c.POINTER(w.UINT),w.UINT];u.GetRawInputData.restype=w.UINT
u.GetRawInputDeviceInfoW.argtypes=[w.HANDLE,w.UINT,c.c_void_p,c.POINTER(w.UINT)];u.GetRawInputDeviceInfoW.restype=w.UINT
u.GetForegroundWindow.restype=w.HWND
u.GetFocus.restype=w.HWND
u.GetAncestor.argtypes=[w.HWND,w.UINT];u.GetAncestor.restype=w.HWND
u.MapVirtualKeyW.argtypes=[w.UINT,w.UINT];u.MapVirtualKeyW.restype=w.UINT
u.GetMessageW.argtypes=[c.POINTER(w.MSG),w.HWND,w.UINT,w.UINT]
u.DispatchMessageW.argtypes=[c.POINTER(w.MSG)];u.DispatchMessageW.restype=LRESULT
k.GetModuleHandleW.argtypes=[w.LPCWSTR];k.GetModuleHandleW.restype=w.HMODULE


class Metrics:
    def __init__(self):
        self.names={}
        self.pressed=set()
        self.seen=set()
        self.clicks=Counter()
        self.moves={}
        self.wheel=0
        self.last='等待输入'
        self.peak_keys=0

    def mouse(self,device,flags,wheel,x,y,now):
        if x or y:
            self.moves.setdefault(device,deque()).append(now)
        for i,label in enumerate(('左键','右键','中键','侧键 1','侧键 2')):
            key=(device,label)
            if flags & (1<<(i*2)):
                self.pressed.add(key);self.clicks[label]+=1;self.last=label+' 按下'
            if flags & (2<<(i*2)):
                self.pressed.discard(key);self.last=label+' 释放'
        if flags & 0xC00:
            self.wheel+=wheel/120
            self.last=('水平滚轮' if flags&0x800 else '滚轮')+f' {wheel/120:+g}'

    def keyboard(self,device,scan,flags,vkey):
        # Raw break/make events, not translated text; typematic isn't a new key.
        label=('E0 ' if flags&2 else '')+f'{scan:02X}'
        key=(device,label)
        if flags&1:
            self.pressed.discard(key)
        else:
            self.pressed.add(key);self.seen.add(label)
        self.peak_keys=max(self.peak_keys,sum(label not in ('左键','右键','中键','侧键 1','侧键 2') for _,label in self.pressed))
        names={8:'退格',9:'Tab',13:'Enter',16:'Shift',17:'Ctrl',18:'Alt',27:'Esc',32:'空格',37:'←',38:'↑',39:'→',40:'↓'}
        name=names.get(vkey,chr(vkey) if 48<=vkey<=90 else f'F{vkey-111}' if 112<=vkey<=135 else f'VK {vkey:02X}')
        self.last=f'{name} · 扫描码 {label} · '+('释放' if flags&1 else '按下')

    def snapshot(self,now):
        rates={}
        for device,events in self.moves.items():
            while events and events[0]<=now-1:
                events.popleft()
            rates[device]=len(events)
        return dict(names=dict(self.names),pressed=sorted({name for _,name in self.pressed}),
                    seen=len(self.seen),clicks=dict(self.clicks),wheel=self.wheel,last=self.last,
                    peak_keys=self.peak_keys,rates=rates)


class InputSession:
    def __init__(self,foreground):
        self.foreground=foreground
        self.lock=threading.Lock()
        self.metrics=Metrics()
        self.stop_event=threading.Event()
        self.ready=threading.Event()
        self.error=''
        self.hwnd=None
        self.thread=threading.Thread(target=self._run,daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.hwnd:u.PostMessageW(self.hwnd,0x0010,0,0)

    def snapshot(self):
        foreground=u.GetForegroundWindow()==self.foreground
        with self.lock:
            if not foreground:
                self.metrics.pressed.clear();self.metrics.moves.clear()
            result=self.metrics.snapshot(time.perf_counter())
        result.update(error=self.error,ready=self.ready.is_set(),foreground=foreground)
        return result

    def _name(self,handle):
        size=w.UINT(0)
        u.GetRawInputDeviceInfoW(handle,0x20000007,None,c.byref(size))
        if not 0<size.value<4096:return '输入设备'
        buf=c.create_unicode_buffer(size.value+1)
        if u.GetRawInputDeviceInfoW(handle,0x20000007,buf,c.byref(size))==0xFFFFFFFF:return '输入设备'
        import re
        ids=re.search(r'VID_([0-9A-F]{4}).*?PID_([0-9A-F]{4})',buf.value,re.I)
        return ':'.join(ids.groups()).upper() if ids else '系统输入设备'

    def _input(self,handle):
        if self.stop_event.is_set():return
        if u.GetForegroundWindow()!=self.foreground:
            with self.lock:self.metrics.pressed.clear();self.metrics.moves.clear()
            return
        size=w.UINT(0)
        u.GetRawInputData(handle,0x10000003,None,c.byref(size),c.sizeof(RawHeader))
        if not c.sizeof(RawHeader)<=size.value<=65536:return
        buf=c.create_string_buffer(size.value)
        if u.GetRawInputData(handle,0x10000003,buf,c.byref(size),c.sizeof(RawHeader))==0xFFFFFFFF:return
        header=RawHeader.from_buffer_copy(buf)
        data=buf.raw[c.sizeof(RawHeader):]
        device=str(header.device or 0)
        with self.lock:
            if device not in self.metrics.names:self.metrics.names[device]=self._name(header.device)
            if header.kind==0 and len(data)>=24:
                flags=int.from_bytes(data[4:6],'little')
                wheel=int.from_bytes(data[6:8],'little',signed=True)
                x=int.from_bytes(data[12:16],'little',signed=True)
                y=int.from_bytes(data[16:20],'little',signed=True)
                self.metrics.mouse(device,flags,wheel,x,y,time.perf_counter())
            elif header.kind==1 and len(data)>=16:
                scan=int.from_bytes(data[:2],'little');flags=int.from_bytes(data[2:4],'little')
                vkey=int.from_bytes(data[6:8],'little')
                if not scan:
                    mapped=u.MapVirtualKeyW(vkey,4)
                    scan=mapped&255
                    if mapped&0xFF00==0xE000:flags|=2
                if scan!=255 and vkey!=255:self.metrics.keyboard(device,scan,flags,vkey)

    def _run(self):
        name=f'DeviceBatteryInputTest{ id(self) }'
        instance=k.GetModuleHandleW(None)
        @WNDPROC
        def proc(hwnd,msg,wp,lp):
            try:
                if msg==0xFF:self._input(lp)
                if msg==0x10:u.DestroyWindow(hwnd);return 0
                if msg==2:u.PostQuitMessage(0);return 0
            except Exception as exc:self.error=str(exc)
            return u.DefWindowProcW(hwnd,msg,wp,lp)
        wc=WindowClass();wc.proc=proc;wc.instance=instance;wc.name=name
        registered=False
        try:
            if not u.RegisterClassW(c.byref(wc)):raise c.WinError(c.get_last_error())
            self.hwnd=u.CreateWindowExW(0,name,'',0,0,0,0,0,w.HWND(-3),None,instance,None)
            if not self.hwnd:raise c.WinError(c.get_last_error())
            devices=(RawDevice*2)(RawDevice(1,2,0x100,self.hwnd),RawDevice(1,6,0x100,self.hwnd))
            if not u.RegisterRawInputDevices(devices,2,c.sizeof(RawDevice)):raise c.WinError(c.get_last_error())
            registered=True;self.ready.set()
            if self.stop_event.is_set():u.PostMessageW(self.hwnd,0x10,0,0)
            msg=w.MSG()
            while u.GetMessageW(c.byref(msg),None,0,0)>0:
                u.TranslateMessage(c.byref(msg));u.DispatchMessageW(c.byref(msg))
        except Exception as exc:self.error=str(exc);self.ready.set()
        finally:
            if registered:
                devices=(RawDevice*2)(RawDevice(1,2,1,None),RawDevice(1,6,1,None))
                u.RegisterRawInputDevices(devices,2,c.sizeof(RawDevice))
            if self.hwnd:u.DestroyWindow(self.hwnd)
            self.hwnd=None
            u.UnregisterClassW(name,instance)
