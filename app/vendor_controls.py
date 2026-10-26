"""Incott, VAXEE and Dareu controls from model-specific protocol records."""
from dataclasses import replace
import time

INCOTT_NAMES={1:'Ghero',2:'G23',3:'G24',6:'Zero 29',8:'G23V2',9:'Zero 39',14:'G23V2'}
INCOTT_RATES={0:1000,1:500,2:250,3:125,4:8000,5:4000,6:2000}
INCOTT_MODES={0:'连接与回报率',1:'电量状态',2:'低电量提醒'}
VAXEE_NAMES={0x1003:'XE Wireless',0x1004:'NP-01S Wireless',0x1005:'AX Wireless',
             0x1006:'NP-01 Wireless',0x1007:'XE-S Wireless',0x1008:'XE-S-L Wireless',
             0x1009:'VAXEE × Ninjutso Sora',0x1010:'E1 Wireless',0x1011:'NP-01S V2 Wireless',
             0x1012:'XE V2 Wireless',0x1013:'NP-01S Ergo Wireless',0x1014:'NP-01S V3 Wireless',0x1015:'NP-01 Ergo Wireless'}
VAXEE_RECEIVERS=(5,0x1001,0x1002,0x2001,0x2002)
DAREU_RATES={8:125,4:250,2:500,1:1000,16:2000,32:4000}
DAREU_MODES={0:'关闭',1:'灯效 1',2:'灯效 2'}


def incott_packet(cmd,value=0):return bytes([9,cmd,value]).ljust(9,b'\0')


def incott_query(dev,cmd):
    try:dev.get_feature_report(9,64)  # discard the previously latched response
    except OSError:pass
    dev.set_feature_report(incott_packet(cmd))
    for _ in range(10):
        time.sleep(.05);raw=dev.get_feature_report(9,64)
        if len(raw)>=9 and raw[:2]==bytes([9,cmd]):return raw
    raise TimeoutError('英科特未返回匹配的配置响应')


def incott_read(dev,d,mouse,m):
    from peripheral_controls import State,Light
    ident=incott_query(dev,0x8F)
    if ident[2]!=1 or ident[3] not in INCOTT_NAMES:raise ValueError('未确认英科特配对型号')
    rates=m.rates if d.pid==0x522C and ident[4]==2 else (125,250,500,1000)
    code=incott_query(dev,0x81)[2]
    if code not in INCOTT_RATES or INCOTT_RATES[code] not in rates:raise ValueError('英科特回报率无效')
    light=None;error=''
    if d.pid==0x522C:
        try:
            mode=incott_query(dev,0x88)[2]
            if mode not in INCOTT_MODES:raise ValueError('接收器灯光模式未知')
            light=Light(mode)
        except (OSError,ValueError) as exc:error=str(exc)
    name='Incott '+INCOTT_NAMES[ident[3]]+(' Pro' if 0xF1 in ident[5:7] else '')
    return State(mouse,name,INCOTT_RATES[code],rates,0,(d.vid,d.pid,bytes(ident[2:7])),light,light_error=error)


def incott_write(dev,d,current,wanted):
    if wanted.rate!=current.rate:
        code=next(k for k,v in INCOTT_RATES.items() if v==wanted.rate)
        dev.set_feature_report(incott_packet(1,code))
    if wanted.light!=current.light:dev.set_feature_report(incott_packet(8,wanted.light.mode))
    time.sleep(.1)
    return wanted


def vaxee_query(dev,cmd,length,values=(),write=False):
    dev.set_feature_report(bytes([14,0xA5,cmd,2 if write else 1,length,*values]).ljust(64,b'\0'))
    for _ in range(6):
        time.sleep(.1);raw=dev.get_feature_report(14,64)
        if len(raw)!=64 or raw[:4]!=bytes([14,0xA5,cmd,3]):continue
        if raw[4]!=1:raise ValueError('VAXEE 固件拒绝设置或查询')
        size=raw[5]
        if not (1 if write else length)<=size<=58:raise ValueError('VAXEE 响应长度错误')
        return raw[6:6+size]
    raise TimeoutError('VAXEE 未返回匹配的配置响应')


def vaxee_read(dev,d,mouse,m):
    from peripheral_controls import State
    paired=int.from_bytes(vaxee_query(dev,10,2)[:2],'little')
    if paired not in VAXEE_NAMES:raise ValueError('未确认 VAXEE 配对型号')
    rates=m.rates;raw=b''
    if max(rates)>1000:
        raw=vaxee_query(dev,8,2 if d.pid==0x2002 else 1)[:1]
        value=raw[0]
        if value not in ((0,1) if d.pid==0x2002 else (1,2,3,4)):raise ValueError('未知的 VAXEE 追踪模式')
        if (value==0 if d.pid==0x2002 else value in (2,4)):rates=(500,1000)
    code=vaxee_query(dev,7,1)[0];values=(0,500,1000,2000,4000,8000)
    if code not in range(1,6) or values[code] not in rates:raise ValueError('VAXEE 当前模式与回报率不一致')
    return State(mouse,'VAXEE '+VAXEE_NAMES[paired],values[code],rates,0,(d.vid,d.pid,paired),raw=raw)


def vaxee_write(dev,d,current,wanted):
    code=(0,500,1000,2000,4000,8000).index(wanted.rate)
    vaxee_query(dev,7,1,(code,),True)
    return wanted


def dareu_query(dev,d,cmd,payload=()):
    import mouse_settings as ms
    packet=ms.atk_frame(cmd,payload)
    raw=ms.exchange(dev,d,packet,lambda r:len(r)==17 and r[:2]==bytes([8,cmd])
                    and sum(r)&255==0x55 and (cmd!=8 or r[3:6]==packet[3:6]))
    if raw[2]!=0:raise ValueError('达尔优返回配置访问错误')
    return raw


def dareu_memory(dev,d,address,length):
    raw=dareu_query(dev,d,8,(0,address>>8,address&255,length))[6:6+length]
    if any(sum(raw[i:i+2])&255!=0x55 for i in range(0,length,2)):raise ValueError('达尔优设置校验失败')
    return raw


def dareu_read(dev,d,mouse,m):
    from peripheral_controls import State,Light
    if dareu_query(dev,d,3)[6]!=1:raise ValueError('达尔优鼠标尚未连接')
    profile=dareu_query(dev,d,14)[6]
    if profile>3:raise ValueError('达尔优配置层无效')
    raw=dareu_memory(dev,d,0,2)
    if raw[0] not in DAREU_RATES or DAREU_RATES[raw[0]] not in m.rates:raise ValueError('达尔优回报率无效')
    light=None;preserved=b'';error=''
    try:
        data=dareu_memory(dev,d,76,8)
        mode=data[0] if data[6] else 0
        brightness=(data[2]*10+127)//255
        if data[6] not in (0,1) or mode not in DAREU_MODES or not 1<=brightness<=10 or not 1<=data[4]<=5:
            raise ValueError('达尔优 DPI 指示灯数据无效')
        light=Light(mode,brightness,data[4]);preserved=data
    except (OSError,ValueError) as exc:error=str(exc)
    if dareu_query(dev,d,14)[6]!=profile:raise ValueError('读取期间配置层已切换')
    return State(mouse,m.name,DAREU_RATES[raw[0]],m.rates,profile+1,(d.vid,d.pid),light,preserved,error)


def dareu_write(dev,d,current,wanted):
    def scalar(address,value):dareu_query(dev,d,7,(0,address>>8,address&255,2,value,(0x55-value)&255))
    if wanted.rate!=current.rate:scalar(0,next(k for k,v in DAREU_RATES.items() if v==wanted.rate))
    if wanted.light!=current.light:
        l=wanted.light;data=bytearray(current.raw)
        if l.mode==0:
            scalar(82,0);data[6:8]=bytes([0,0x55])
            # Off changes only the enabled flag, preserving stored brightness/speed.
            wanted=replace(wanted,light=replace(current.light,mode=0))
        else:
            for address,value in ((82,1),(76,l.mode),(78,l.brightness*255//10),(80,l.speed)):
                scalar(address,value);offset=address-76;data[offset:offset+2]=bytes([value,(0x55-value)&255])
        wanted=replace(wanted,raw=bytes(data))
    return wanted
