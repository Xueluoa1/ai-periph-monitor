"""Independent control-only adapters. Discovery never changes device settings."""
from dataclasses import dataclass, replace
import time

import hid_win as hw
import razer_controls as rz
import vendor_controls as vc
from device_io import device_session
from control_models import MODELS, MCHOSE_MICE, match

RATE_CODES = {125:8,250:4,500:2,1000:1,2000:0x20,4000:0x40,8000:0x80}
DECODE_RATES = {v:k for k,v in RATE_CODES.items()} | {0x10:1000}
DOCK_MODES = {-1:'关闭',0:'常亮',1:'呼吸',2:'闪耀',3:'循环',4:'流动',5:'音乐同步'}
# Preserve official numeric names for receiver effects whose appearance varies by firmware.
LUNA_MODES = {0:'关闭',2:'灯效 2',6:'灯效 6',11:'灯效 11'}


@dataclass(frozen=True)
class Light:
    mode: int
    brightness: int = 0
    speed: int = 0
    rgb: tuple = (0,0,0)


@dataclass(frozen=True)
class State:
    mouse: object
    name: str
    rate: int | None
    rates: tuple
    profile: int
    identity: tuple
    light: Light | None = None
    raw: bytes = b''
    light_error: str = ''


def discover(ifaces):
    from mouse_settings import Mouse
    return [Mouse(d.path,m.name,d.vid_pid,'extra',(d.vid,d.pid),interface=d)
            for d in ifaces if (m:=match(d)) is not None]


def interface(mouse):
    d = next((d for d in hw.enumerate_hid() if d.path==mouse.path),None)
    if d is None:raise OSError('设备已断开')
    if (d.vid,d.pid)!=mouse.identity or match(d) is None:
        raise ValueError('配置接口已变化，请重新读取')
    return d


def page(dev,target,page_id,command,length,args=(),*,full=False):
    if not 0<=length<=58 or len(args)>58:raise ValueError('页面请求长度错误')
    packet=bytes([0,0,0,target,length,page_id,command,*args]).ljust(65,b'\0')
    dev.set_feature_report(packet)
    for _ in range(18):
        time.sleep(.04)
        raw=dev.get_feature_report(0,65)
        if len(raw)!=65 or raw[0]!=0:continue
        body=raw[1:]
        if (body[2],body[4],body[5])!=(target,page_id,command):continue
        if body[0]==0xA2:raise ValueError('固件不支持该查询或设置')
        if body[0]==0xA1:
            n=body[3]
            if not 0<=n<=58:raise ValueError('页面响应长度错误')
            return bytes(body[6:] if full else body[6:6+n])
    raise TimeoutError('设备未回复页面命令，请唤醒设备')


def _profile(dev,target):
    raw=page(dev,target,0,0x85,1)
    if len(raw)!=1 or not 1<=raw[0]<=5:raise ValueError('板载配置编号无效')
    return raw[0]


def _page_read(dev,d,mouse,m):
    ident=(d.vid,d.pid)
    if ident==(0x36A7,0xA882):
        pair=page(dev,1,0,0x8B,6,(2,))
        if len(pair)!=6:raise ValueError('接收器配对信息不完整')
        paired=int.from_bytes(pair[-2:],'big')
        if (0x36A7,paired) not in MODELS or paired==0xA882:
            raise ValueError('尚未确认此接收器配对鼠标的型号')
        ident+= (paired,)
    profile=_profile(dev,m.target)
    raw=page(dev,m.target,1,0x80,2,(profile,))
    if len(raw)!=2 or raw[0]!=profile or raw[1] not in DECODE_RATES:
        raise ValueError('回报率响应无效')
    rate=DECODE_RATES[raw[1]]
    if rate not in m.rates:raise ValueError('固件回报率超出此连接的已适配范围')
    light=None;error=''
    if m.lighting:
        try:
            if m.lighting=='toggle':
                value=page(dev,0,2,0x84,2,(profile,))
                if len(value)!=2 or value[0]!=profile or value[1] not in (0,1):
                    raise ValueError('指示灯状态无效')
                light=Light(value[1])
            else:
                value=page(dev,0 if d.pid==0x33 else 1,2,0x80,6,(1,))
                if len(value)<3 or value[0]!=1 or value[2] not in LUNA_MODES:
                    raise ValueError('接收器灯效状态无效')
                light=Light(value[2])
        except (OSError,ValueError) as exc:error=str(exc)
    if _profile(dev,m.target)!=profile:raise ValueError('读取期间配置层已切换')
    return State(mouse,m.name,rate,m.rates,profile,ident,light,light_error=error)


def mchose_packet(rid,command,payload=b''):
    size=20 if rid==0x11 else 64
    tokens=bytes([command])+payload
    if len(tokens)>size:raise ValueError('迈从设置长度错误')
    body=bytes(v^255 for v in tokens.ljust(size,b'\0'))
    return bytes([rid])+body.ljust(64,b'\0')


def mchose_query(dev,rid,command):
    previous=None
    for _ in range(6):
        dev.set_feature_report(mchose_packet(rid,command))
        time.sleep(.06)
        raw=dev.get_feature_report(rid,65)
        if len(raw)!=65 or raw[0]!=rid or raw[1]!=(command^255):continue
        value=bytes(v^255 for v in raw[2:])
        if value==previous:return value
        previous=value
    raise TimeoutError('未读取到稳定的迈从配置')


def _mchose_read(dev,d,mouse,m):
    battery=mchose_query(dev,0x11,6)
    vid=int.from_bytes(battery[:2],'little');pid=int.from_bytes(battery[2:4],'little')
    if vid!=0x3837 or pid not in MCHOSE_MICE or not battery[8]&0x10:
        raise ValueError('未确认已连接的 A7 V2 型号')
    raw=mchose_query(dev,0x12,0x67)
    wired=d.pid in MCHOSE_MICE
    slot=1 if wired else 2
    if raw[0]>2 or not 1<=raw[16]<=6 or raw[slot]&15>=raw[16] or raw[slot]>>4>=len(m.rates):
        raise ValueError('迈从配置编号、DPI 档位或回报率无效')
    if not 50<=int.from_bytes(raw[4:6],'little')<=42000:raise ValueError('迈从配置内容无效')
    identity=(vid,pid,slot)
    return State(mouse,'MCHOSE '+MCHOSE_MICE[pid],m.rates[raw[slot]>>4],m.rates,raw[0]+1,identity,raw=raw)


def dock_packet(command,payload=b''):
    return bytes([0,0xAA,command,0,0,0,len(payload)])+payload+bytes(57-len(payload))


def _dock_read(dev,d,mouse,m):
    dev.drain();dev.write_output(dock_packet(7),use_hidd=False)
    deadline=time.monotonic()+1
    while time.monotonic()<deadline:
        raw=dev.read_input(timeout_ms=100)
        if not raw or len(raw)<37 or raw[:3]!=b'\x00\xaa\x07' or raw[3] not in (0,2):continue
        if raw[6]<30 or raw[6]>57:continue
        p=raw[7:]
        if p[0] not in (0,1) or p[1] not in range(6) or p[3]>4 or p[4]>4 or p[5] not in (0,1):
            raise ValueError('底座灯光配置无效')
        light=Light(p[1] if p[0] else -1,p[4],p[3],tuple(p[6:9]))
        # Reserved read fields are not present in the compact write format.
        preserved=bytes([p[1],p[2],p[5],p[29]])
        return State(mouse,m.name,None,(),0,(d.vid,d.pid),light,preserved)
    raise TimeoutError('充电底座未返回灯光设置')


def _read(mouse):
    d=interface(mouse);m=match(d)
    with hw.HidDevice(d.path,write=True) as dev:
        if m.family=='razer':return rz.read(dev,d,mouse)
        return {'page':_page_read,'mchose':_mchose_read,'dock':_dock_read,
                'incott':vc.incott_read,'vaxee':vc.vaxee_read,'dareu':vc.dareu_read}[m.family](dev,d,mouse,m)


def read(mouse):
    with device_session():return _read(mouse)


def fingerprint(s):
    return s.name,s.rate,s.rates,s.profile,s.identity,s.light,s.raw,s.light_error


def apply(snapshot,*,rate=None,light=None):
    m=MODELS[snapshot.mouse.identity]
    if rate is not None and (type(rate) is not int or rate not in snapshot.rates):
        raise ValueError('此型号或连接不支持该回报率')
    if light is not None:
        if not isinstance(light,Light) or snapshot.light is None:raise ValueError('请先成功读取灯光')
        allowed={'razer':rz.MODES,'dock':DOCK_MODES,'incott':vc.INCOTT_MODES,'dareu':vc.DAREU_MODES}.get(m.family,LUNA_MODES if m.lighting=='luna' else (0,1))
        if type(light.mode) is not int or light.mode not in allowed:raise ValueError('灯效无效')
        maximum=100 if m.family=='razer' else 10 if m.family=='dareu' else 4
        minimum=1 if m.family=='dareu' else 0
        speed_max=5 if m.family=='dareu' else 4
        if (type(light.brightness) is not int or not minimum<=light.brightness<=maximum or type(light.speed) is not int
            or not minimum<=light.speed<=speed_max or len(light.rgb)!=3 or any(type(v) is not int or not 0<=v<=255 for v in light.rgb)):
            raise ValueError('灯光亮度、速度或颜色无效')
        if m.family=='razer' and light.speed!=0:raise ValueError('此雷蛇灯效不提供速度调节')
        if m.family=='dareu' and light.rgb!=(0,0,0):raise ValueError('DPI 指示灯颜色不由该命令控制')
        if m.family not in ('razer','dareu') and m.lighting!='dock' and (light.brightness,light.speed,light.rgb)!=(0,0,(0,0,0)):
            raise ValueError('此接收器仅支持灯效选择')
    with device_session():
        current=_read(snapshot.mouse)
        if fingerprint(current)!=fingerprint(snapshot):raise ValueError('设备配置已变化，请重新读取后再应用')
        wanted=replace(current,rate=current.rate if rate is None else rate,light=current.light if light is None else light)
        if fingerprint(wanted)==fingerprint(current):return current
        d=interface(current.mouse)
        try:
            with hw.HidDevice(d.path,write=True) as dev:
                if m.family=='page':
                    if wanted.rate!=current.rate:
                        page(dev,m.target,1,0,2,(current.profile,RATE_CODES[wanted.rate]))
                    if wanted.light!=current.light:
                        if m.lighting=='toggle':page(dev,0,2,4,2,(current.profile,wanted.light.mode))
                        else:page(dev,0 if d.pid==0x33 else 1,2,0,6,(1,0,wanted.light.mode))
                elif m.family=='razer':
                    wanted=rz.write(dev,d,current,wanted.rate,light)
                elif m.family in ('incott','vaxee','dareu'):
                    wanted={'incott':vc.incott_write,'vaxee':vc.vaxee_write,'dareu':vc.dareu_write}[m.family](dev,d,current,wanted)
                elif m.family=='mchose':
                    raw=bytearray(current.raw);slot=current.identity[2]
                    raw[slot]=(m.rates.index(wanted.rate)<<4)|(raw[slot]&15)
                    dev.set_feature_report(mchose_packet(0x12,0x57,bytes(raw)))
                    wanted=replace(wanted,raw=bytes(raw));time.sleep(.4)
                else:
                    l=wanted.light;old=current.raw
                    effect=old[0] if l.mode==-1 else l.mode
                    sync=int(effect==5)
                    data=bytes([int(l.mode!=-1),effect,old[1],l.speed,l.brightness,sync,*l.rgb,old[3]])
                    dev.write_output(dock_packet(39,data),use_hidd=False)
                    wanted=replace(wanted,raw=bytes([effect,old[1],sync,old[3]]));time.sleep(.4)
            result=_read(current.mouse)
        except (OSError,ValueError) as exc:
            raise RuntimeError(f'设置未完整确认：{exc}；部分改动可能已生效，请重新读取。未自动重试') from exc
        if fingerprint(result)!=fingerprint(wanted):
            raise RuntimeError('读回值与提交设置不一致；部分改动可能已生效，请重新读取')
        return result
