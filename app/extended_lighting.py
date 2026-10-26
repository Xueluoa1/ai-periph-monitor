"""Read-back-capable lighting, explicitly separated from DPI indicators."""
from dataclasses import replace
import time
import extended_controls as ec
import dpi_extended as dx
import dpi_more as dm
import dpi_hecate as he
import dpi_pulsar as pu
import mouse_settings as ms

MODES=((0,'常亮'),(1,'呼吸'),(2,'颜色循环'),(4,'按键响应'))
OLD_ROG=(0x18E5,0x18E3,0x1908,0x1906,0x1949,0x1947,0x1960,0x195C,0x195E)
AGGREGATE=(0x18E5,0x18E3,0x1908,0x1906,0x1949,0x1947)
TWO_ZONES=(0x1949,0x1947,0x1960,0x195C,0x195E)
SINGLE_LOGO=(0x1A68,0x1A66,0x1B0C,0x1B0A,0x1B16)


def rog_spec(pid):
    if pid==0x19F4:return (),0
    zones=(0,) if pid in SINGLE_LOGO else (1,) if pid==0x1B63 else (0,1) if pid in TWO_ZONES else (0,1,2)
    return zones,4 if pid in OLD_ROG else 100


def rog_read(dev,d,s):
    pid=s.base.mouse.identity[2];ids,maximum=rog_spec(pid);result=[]
    aggregate=ms.rog_query(dev,d,s.selector,(0x12,3,0)) if pid in AGGREGATE else None
    for index,key in enumerate(ids):
        r=aggregate if aggregate is not None else ms.rog_query(dev,d,s.selector,(0x12,3,index))
        at=5+key*5 if aggregate is not None else 5
        if len(r)<at+5:raise ValueError('ROG 灯光回复不完整')
        mode,brightness=r[at:at+2];rgb=tuple(r[at+2:at+5])
        aux=bytes(r[21:24] if aggregate is not None else r[11:14])
        if len(aux)!=3 or brightness>maximum or mode not in (0,1,2,3,4,5,6,0xF0,0xFF):
            raise ValueError('ROG 灯效或亮度超出已知范围')
        modes=MODES
        if len(ids)==1:
            modes+=((6,'电量指示'),)
            if pid in (0x1B16,0x1B63):modes+=((0xF0,'关闭'),)
        # Per-zone commands do not offer global rainbow/comet or global off.
        result.append(ec.Zone(key,('Logo','滚轮','底部')[key],mode,modes,rgb,brightness,maximum,aux=aux))
    return tuple(result),b''


def vxe_zones(raw,count):
    if len(raw)!=8+((count+1)//2)*8:raise ValueError('VXE 指示灯配置长度不符')
    effect=raw[0];enabled=raw[6]
    for i in (0,2,4,6):
        if raw[i:i+2]!=b'\0\0' and sum(raw[i:i+2])&255!=0x55:raise ValueError('VXE 灯光校验失败')
    if effect not in (0,1,2) or enabled not in (0,1) or raw[4] not in (1,3,5):raise ValueError('未知 VXE 灯效编码')
    bright=-1 if raw[2:4]==b'\0\0' else (0x10,0x80,0xFF).index(raw[2])
    mode=0 if effect==0 or effect==1 and not enabled else effect
    zones=[ec.Zone(100,'DPI 指示灯效果',mode,((0,'关闭'),(1,'常亮'),(2,'呼吸')),
                   brightness=bright,maximum=2,speed=(1,3,5).index(raw[4]),speeds=(0,1,2))]
    for i in range(count):
        data=raw[8+i*4:12+i*4]
        if sum(data)&255!=0x55:raise ValueError('VXE 档位颜色校验失败')
        zones.append(ec.Zone(i,f'第 {i+1} 档 DPI 指示颜色',0,rgb=tuple(data[:3])))
    return tuple(zones)


LOGITECH_MODES={0:'关闭',1:'常亮',3:'颜色循环',10:'单色呼吸',21:'颜色循环（扩展）'}
PERIODS=(1000,2000,3000,5000,10000)


def logitech_current(zone,current):
    mode=current[0]
    rgb=tuple(current[1:4]) if mode in (1,10) else (-1,-1,-1)
    speed=int.from_bytes(current[4:6],'big') if mode==10 else int.from_bytes(current[6:8],'big') if mode in (3,21) else -1
    return replace(zone,mode=mode,rgb=rgb,speed=speed)


def logitech_read(dev,s):
    if s.base.identity[-1]==1:raise ValueError('罗技板载模式下不接管灯光，请先通过原厂软件切换软件控制模式')
    index=s.base.mouse.identity[2];channel=0x8071;fi=dx.feature_index(dev,index,channel)
    if not fi:channel=0x8070;fi=dx.feature_index(dev,index,channel)
    if not fi:raise ValueError('设备未提供 8070/8071 灯区功能')
    call=lambda fn,args=():dx.logitech_call(dev,index,fi,fn,args)
    info=call(0,(255,255,0)) if channel==0x8071 else call(0)
    count=info[2] if channel==0x8071 else info[0]
    if len(info)<5 or not 1<=count<=8:raise ValueError('设备没有返回有效灯区')
    readable=channel==0x8070 and bool(int.from_bytes(info[3:5],'big')&1)
    zones=[];raw=[]
    for i in range(count):
        z=call(0 if channel==0x8071 else 1,(i,255,0))
        if len(z)<4 or not 1<=z[3]<=32:raise ValueError('罗技灯区能力无效')
        effects=[];modes=[]
        for j in range(z[3]):
            e=call(0 if channel==0x8071 else 2,(i,j,0))
            if len(e)<8:raise ValueError('罗技灯效能力回复不完整')
            effect=int.from_bytes(e[2:4],'big')
            if effect in LOGITECH_MODES:
                effects.extend((effect,e[1]));modes.append((effect,LOGITECH_MODES[effect]))
        if not modes:continue
        location=int.from_bytes(z[1:3],'big')
        zone=ec.Zone(i,f'灯区 {i+1}（位置 {location}）',-1,tuple(modes),(-1,-1,-1),
                     speed=-1,speeds=PERIODS,aux=bytes([fi,*effects]),write_only=not readable,channel=channel)
        if readable:
            r=call(14,(i,))
            if len(r)<12 or r[0]!=i:raise ValueError('罗技当前灯区回复不匹配')
            current=bytes(r[1:12]);zone=logitech_current(zone,current);raw.append(current)
        zones.append(zone)
    if not zones:raise ValueError('未发现本版支持的设备灯效')
    return tuple(zones),b''.join(raw)


def read(dev,d,s):
    f=s.family
    if f=='rog_more':return rog_read(dev,d,s)
    if f=='logitech':return logitech_read(dev,s)
    if f=='vxe':
        raw=ms.atk_read(dev,d,0x4C,8)
        raw+=b''.join(ms.atk_read(dev,d,44+i*8,8) for i in range((len(s.stages)+1)//2))
        return vxe_zones(raw,len(s.stages)),raw
    if f=='rk':
        i=s.selector
        return (ec.Zone(i,f'当前第 {i+1} 档 DPI 指示颜色',0,rgb=tuple(s.raw[27+i*3:30+i*3])),),b''
    if f in ('hecate','wl4k'):
        return tuple(ec.Zone(i,f'第 {i+1} 档 DPI 指示颜色',0,rgb=tuple(s.raw[20+i*9:23+i*9])) for i in range(len(s.stages))),b''
    if f=='pulsar':
        if s.base.identity[-1] not in (0,3):return (),b''
        with pu.session(dev,d,s.base.identity[2:]):raw=pu.query(dev,d,0x15)
        if len(raw)!=10 or raw[0] not in (0,1):raise ValueError('Pulsar 接收器灯光回复无效')
        zones=[ec.Zone(100,'接收器灯光开关',raw[0],((0,'关闭'),(1,'开启')))]
        zones.extend(ec.Zone(i,f'接收器颜色 {i+1}',0,rgb=tuple(raw[1+i*3:4+i*3])) for i in range(3))
        return tuple(zones),raw
    if f=='egg' and s.base.identity[-1] in (0x1982,0x1984):
        if s.raw[24] not in (0,1):raise ValueError('Endgame 抬起指示灯状态未知')
        return (ec.Zone(0,'抬起鼠标时指示灯',s.raw[24],((0,'关闭'),(1,'开启'))),),b''
    return (),b''


def write(dev,d,state,z):
    s=state.config;f=s.family;raw=bytearray(s.raw);light=bytearray(state.light_raw)
    zones=tuple(z if old.key==z.key else old for old in state.zones)
    if f=='rog_more':
        ms.rog_query(dev,d,s.selector,bytes([0x51,0x28,z.key,0,z.mode,z.brightness,*z.rgb])+z.aux)
        ms.rog_query(dev,d,s.selector,(0x50,3))
    elif f=='vxe':
        if z.key==100:
            light[:2]=b'\0\0' if z.mode==0 else ec.pair(z.mode)
            if z.mode==0:light[2:4]=b'\0\0'
            elif z.mode==1:light[2:4]=ec.pair((0x10,0x80,0xFF)[z.brightness])
            light[4:6]=ec.pair((1,3,5)[z.speed]);light[6:8]=ec.pair(int(z.mode!=0))
            dx.vxe_write_row(dev,d,0x4C,bytes(light[:8]))
        else:
            offset=8+z.key*4;light[offset:offset+4]=bytes((*z.rgb,(0x55-sum(z.rgb))&255))
            group=z.key//2*8
            dx.vxe_write_row(dev,d,44+group,bytes(light[8+group:16+group]))
        zones=vxe_zones(bytes(light),len(s.stages))
    elif f=='rk':
        i=s.selector;raw[27+i*3:30+i*3]=bytes(z.rgb)
        payload=bytes([raw[2]])+bytes(raw[11+i*2:13+i*2])+bytes(z.rgb)
        dev.set_feature_report(dm.rk_packet(0x50,0x3A,0,payload));time.sleep(.1)
    elif f in ('hecate','wl4k'):
        raw[20+z.key*9:23+z.key*9]=bytes(z.rgb)
        if f=='hecate':ec.persist_hecate(dev,s,raw)
        else:ec.persist_wl(dev,s,raw)
    elif f=='pulsar':
        if z.key==100:light[0]=z.mode
        else:light[1+z.key*3:4+z.key*3]=bytes(z.rgb)
        with pu.session(dev,d,s.base.identity[2:]):pu.query(dev,d,0x14,data=bytes(light))
    elif f=='egg':
        raw[24]=z.mode
        payload=bytes(raw[i] for i in (23,24,25,26,27,17,30,29))+raw[51:71]
        ec.egg_write(dev,0x14,payload,28)
    elif f=='logitech':
        effects=dict(zip(z.aux[1::2],z.aux[2::2]));params=bytearray(10)
        if z.mode in (1,10):params[:3]=bytes(z.rgb)
        if z.mode==10:params[3:5]=z.speed.to_bytes(2,'big');params[6]=100
        elif z.mode in (3,21):params[5:7]=z.speed.to_bytes(2,'big');params[7]=100
        elif z.mode==1 and z.channel==0x8071:params[3]=2
        payload=bytes([z.key,effects[z.mode]])+params
        if z.channel==0x8071:
            dx.logitech_call(dev,s.base.mouse.identity[2],z.aux[0],5,(1,3,4))
            dx.logitech_call(dev,s.base.mouse.identity[2],z.aux[0],1,payload+b'\x01')
        else:dx.logitech_call(dev,s.base.mouse.identity[2],z.aux[0],3,payload)
        if z.write_only:
            # An ACK is not a readback. Keep the current effect unknown.
            zones=state.zones
        else:
            index=next(i for i,item in enumerate(state.zones) if item.key==z.key)
            current=bytes([z.mode])+params;light[index*11:index*11+11]=current
            zones=tuple(logitech_current(item,current) if item.key==z.key else item for item in zones)
    else:raise ValueError('此型号没有已核实的灯光写入器')
    return replace(state,config=replace(s,raw=bytes(raw)),zones=zones,light_raw=bytes(light))
