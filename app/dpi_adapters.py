"""DPI transactions independently implemented from documented packet fields.

Identity, profile, complete tables and unrelated control state are checked before
and after writes. No discovery-time writes, mode switches, or firmware access.
"""
from dataclasses import dataclass, replace
import time
import peripheral_controls as pc
import vendor_controls as vc
import razer_controls as rz
from control_models import MODELS
from device_io import device_session


@dataclass(frozen=True)
class Config:
    base: object
    family: str
    stages: tuple
    active: int
    minimum: int
    maximum: int
    step: int
    raw: bytes = b''
    selector: int = 0
    note: str = ''
    options: tuple = ()
    can_activate: bool = True


def supported(mouse):
    return mouse.identity in MODELS and MODELS[mouse.identity].family != 'dock'


def be(value): return value.to_bytes(2, 'big')
def le(value): return value.to_bytes(2, 'little')


def validate(s):
    if not s.stages or not (0 <= s.active < len(s.stages) or s.active==-1 and not s.can_activate):
        raise ValueError('设备返回的 DPI 档位或当前档位无效')
    if any(type(v) is not int or not s.minimum <= v <= s.maximum or v % s.step
           for pair in s.stages for v in pair):
        raise ValueError('设备 DPI 数据不在已确认的范围或步进内')
    return s


def page_limits(m):
    maximum = 45000 if m.name.startswith('RAWM') else 42000 if m.name.startswith('Attack Shark') else 30000
    count = 5 if 'TYPE33' in m.name or m.name.startswith('Vancer') else 6
    return maximum, count


def page_read(dev, d, base):
    m = MODELS[d.vid, d.pid]
    maximum, count_max = page_limits(m)
    luna = m.name.startswith('LunaFury')
    raw = pc.page(dev,m.target,1,0x81,10,(base.profile,count_max),full=luna)
    if len(raw)<2 or raw[0]!=base.profile or not 1<=raw[1]<=count_max or len(raw)<2+raw[1]*4:
        raise ValueError('DPI 表不完整或所属配置层不符')
    raw = raw[:26] if luna else raw[:2+raw[1]*4]
    stages = tuple((int.from_bytes(raw[i:i+2],'big'),int.from_bytes(raw[i+2:i+4],'big'))
                   for i in range(2,2+raw[1]*4,4))
    active = pc.page(dev,m.target,1,0x82,2,(base.profile,))
    if len(active)!=2 or active[0]!=base.profile:raise ValueError('当前 DPI 档位回复无效')
    if pc._profile(dev,m.target)!=base.profile:raise ValueError('读取期间配置层已切换')
    return Config(base,'page',stages,active[1]-1,50,maximum,50,raw)


def incott_query(dev, command, args=(), axis=None):
    try:dev.get_feature_report(9,64)
    except OSError:pass
    dev.set_feature_report(bytes([9,command,*args]).ljust(9,b'\0'))
    for _ in range(10):
        time.sleep(.04)
        r=dev.get_feature_report(9,64)
        if len(r)<9 or r[:2]!=bytes([9,command]):continue
        if command==0x82 and (r[2]!=args[0] or axis is not None and r[8]!=axis):continue
        return r
    raise TimeoutError('未收到匹配的 Incott DPI 响应')


def incott_read(dev,d,base):
    cycle=incott_query(dev,0x83,(6,))
    count,active=cycle[2:4]
    if not 1<=count<=6 or active>=count:raise ValueError('Incott 档位循环无效')
    stages=[]
    for index in range(count):
        pair=[]
        for axis in (1,2):
            r=incott_query(dev,0x82,(index,axis),axis)
            pair.append((int.from_bytes(r[3:5],'little')+1)*50)
        stages.append(tuple(pair))
    maximum=45000 if 0xF1 in base.identity[2][3:5] else 32000
    if incott_query(dev,0x83,(6,))[2:4]!=cycle[2:4]:raise ValueError('读取期间 DPI 档位已变化')
    return Config(base,'incott',tuple(stages),active,50,maximum,50,bytes([count,active]))


def dareu_block(dev,d,address,length):
    chunks=[]
    for offset in range(0,length,8):
        n=min(8,length-offset);at=address+offset
        chunks.append(vc.dareu_query(dev,d,8,(0,at>>8,at&255,n))[6:6+n])
    return b''.join(chunks)


def dareu_axis(low,high):
    if high not in (0,0x44,0x88):raise ValueError('未知达尔优 DPI 编码')
    return (low+(high//0x44)*256+1)*50


def dareu_read(dev,d,base):
    header=dareu_block(dev,d,2,4)
    if any(sum(header[i:i+2])&255!=0x55 for i in (0,2)) or not 1<=header[0]<=5 or header[2]>=header[0]:
        raise ValueError('达尔优 DPI 表头校验失败')
    raw=dareu_block(dev,d,12,header[0]*4)
    if any(sum(raw[i:i+4])&255!=0x55 for i in range(0,len(raw),4)):raise ValueError('达尔优 DPI 校验失败')
    stages=tuple((dareu_axis(raw[i],raw[i+2]),dareu_axis(raw[i+1],raw[i+2])) for i in range(0,len(raw),4))
    if vc.dareu_query(dev,d,14)[6]+1!=base.profile:raise ValueError('配置层已变化')
    return Config(base,'dareu',stages,header[2],100,26000,50,header+raw)


RAZER_MAX = {pid:30000 for pid in rz.PRODUCTS}
for maximum, ids in ((16000,(0x4C,0x6F,0x70,0x72,0x73,0x77,0x80,0x83)),
                     (20000,(0x86,0x88,0x8F,0x90,0x91)),(7200,(0x62,)),(18000,(0x94,0xB9)),
                     (12000,(0x9A,)),(14000,(0x9C,)),(26000,(0x99,)),
                     (35000,(0xC0,0xC1,0xCB,0xDE,0xDF)),(45000,(0xBE,0xBF))):
    for pid in ids:RAZER_MAX[pid]=maximum


def razer_read(dev,d,base):
    p=rz.match(d);store=1
    # Stored five-stage tables are optional on older firmwares. A validated
    # current-value query is an independent read/write feature, not fake stages.
    current=rz.query(dev,p,4,0x85,7,(store,))
    if current[0]!=store:raise ValueError('雷蛇 DPI 存储区回复不匹配')
    try:raw=rz.query(dev,p,4,0x86,38,(store,))
    except (OSError,ValueError):
        return Config(base,'razer_current',((int.from_bytes(current[1:3],'big'),int.from_bytes(current[3:5],'big')),),
                      0,100,RAZER_MAX[d.pid],1,current,store,'此固件未返回档位表；仅调整当前 DPI。',can_activate=False)
    if raw[0]!=store or not 1<=raw[2]<=5 or not 1<=raw[1]<=raw[2]:raise ValueError('雷蛇 DPI 表头无效')
    if any(raw[3+i*7]!=i for i in range(raw[2])):raise ValueError('雷蛇 DPI 档位编号无效')
    stages=tuple((int.from_bytes(raw[4+i*7:6+i*7],'big'),int.from_bytes(raw[6+i*7:8+i*7],'big')) for i in range(raw[2]))
    return Config(base,'razer',stages,raw[1]-1,100,RAZER_MAX[d.pid],1,raw,store)


def _read(mouse):
    if mouse.protocol=='dpi':
        import dpi_extended
        return dpi_extended.read(mouse)
    if not supported(mouse):raise ValueError('此设备没有已适配的 DPI 设置通道')
    base=pc._read(mouse)
    d=pc.interface(mouse);m=MODELS[mouse.identity]
    with pc.hw.HidDevice(d.path,write=True) as dev:
        if m.family=='page':s=page_read(dev,d,base)
        elif m.family=='incott':s=incott_read(dev,d,base)
        elif m.family=='dareu':s=dareu_read(dev,d,base)
        elif m.family=='razer':s=razer_read(dev,d,base)
        elif m.family=='mchose':
            raw=base.raw;slot=base.identity[2]
            stages=tuple((v,v) for i in range(raw[16]) for v in [int.from_bytes(raw[4+i*2:6+i*2],'little')])
            s=Config(base,'mchose',stages,raw[slot]&15,50,42000 if base.identity[1] in (0x4019,0x4021) else 26000,50,raw)
        elif m.family=='vaxee':
            stage=vc.vaxee_query(dev,2,1)[0]
            if not 1<=stage<=4:raise ValueError('VAXEE 当前档位无效')
            raw=vc.vaxee_query(dev,4,3,(stage,))
            if len(raw)!=3 or raw[0]!=stage:raise ValueError('VAXEE DPI 档位回复不匹配')
            if vc.vaxee_query(dev,2,1)[0]!=stage:raise ValueError('读取期间 VAXEE 当前档位已变化')
            value=int.from_bytes(raw[1:],'little')
            s=Config(base,'vaxee',((value,value),),0,100,26000,50,raw,stage,f'修改设备当前第 {stage} 档；档位切换使用鼠标按键。',can_activate=False)
        else:raise ValueError('该设备不是可调整 DPI 的鼠标')
    return validate(s)


def view(mouse,s):
    from mouse_settings import DpiState
    return DpiState(mouse,list(s.stages),s.active,s.minimum,s.maximum,s.step,
                    profile=s.base.profile,protocol_data=s)


def read(mouse):
    with device_session():return view(mouse,_read(mouse))


def fingerprint(s):
    return (pc.fingerprint(s.base),s.family,s.stages,s.active,s.raw,s.selector,s.minimum,s.maximum,s.step,s.options,s.can_activate)


def write(dev,d,s,stage,value,activate):
    if s.base.mouse.protocol=='dpi':
        import dpi_extended
        return dpi_extended.write(dev,d,s,stage,value,activate)
    pairs=list(s.stages)
    if value is not None:
        x,y=pairs[stage]
        # A single-value editor follows equal axes but preserves a separate Y.
        pairs[stage]=(value,value if x==y else y)
    active=stage if activate else s.active
    changed=tuple(pairs)!=s.stages
    wanted=replace(s,stages=tuple(pairs),active=active)
    raw=bytearray(s.raw)
    if s.family=='page':
        m=MODELS[d.vid,d.pid]
        if changed:
            raw[2+stage*4:6+stage*4]=be(pairs[stage][0])+be(pairs[stage][1])
            pc.page(dev,m.target,1,1,len(raw),raw)
        if active!=s.active:pc.page(dev,m.target,1,2,2,(s.base.profile,active+1))
    elif s.family=='mchose':
        slot=s.base.identity[2]
        if changed:raw[4+stage*2:6+stage*2]=le(value)
        raw[slot]=(raw[slot]&0xF0)|active
        dev.set_feature_report(pc.mchose_packet(0x12,0x57,bytes(raw)))
        wanted=replace(wanted,base=replace(s.base,raw=bytes(raw)))
        time.sleep(.4)
    elif s.family=='incott':
        if changed:
            axis=0 if s.stages[stage][0]==s.stages[stage][1] else 1
            encoded=value//50-1
            dev.set_feature_report(bytes([9,2,stage,encoded&255,encoded>>8,0,0,0,axis]))
        if active!=s.active:dev.set_feature_report(bytes([9,3,len(pairs),active]).ljust(9,b'\0'))
        raw[1]=active;time.sleep(.1)
    elif s.family=='vaxee':
        if changed:
            raw[1:3]=le(value)
            vc.vaxee_query(dev,4,3,raw,write=True)
    elif s.family=='dareu':
        if changed:
            x,y=pairs[stage]
            a,b=x//50-1,y//50-1
            if a//256!=b//256:raise ValueError('此编码不能单独保存跨区间的 X/Y，请使用原厂分轴设置')
            p=bytes([a&255,b&255,(a//256)*0x44]);p+=bytes([(0x55-sum(p))&255])
            address=12+stage*4
            vc.dareu_query(dev,d,7,(0,0,address,4,*p));raw[4+stage*4:8+stage*4]=p
        if active!=s.active:
            p=bytes([active,(0x55-active)&255]);vc.dareu_query(dev,d,7,(0,0,4,2,*p));raw[2:4]=p
    elif s.family=='razer':
        if changed:raw[4+stage*7:8+stage*7]=be(pairs[stage][0])+be(pairs[stage][1])
        raw[1]=active+1
        rz.query(dev,rz.match(d),4,6,38,raw)
    elif s.family=='razer_current':
        raw[1:5]=be(pairs[0][0])+be(pairs[0][1])
        rz.query(dev,rz.match(d),4,5,7,raw)
    return replace(wanted,raw=bytes(raw))


def apply(snapshot,stage,value=None,activate=True):
    s=snapshot.protocol_data
    if not isinstance(s,Config) or type(stage) is not int or not 0<=stage<len(s.stages):raise ValueError('请选择已读取的 DPI 档位')
    if value is not None and (type(value) is not int or not s.minimum<=value<=s.maximum or value%s.step):
        raise ValueError(f'DPI 范围 {s.minimum}–{s.maximum}，步进 {s.step}')
    if value is not None and s.options and value not in s.options:raise ValueError('该 DPI 不在设备上报的可用值中')
    if activate and not s.can_activate:raise ValueError('此通道不支持软件切换档位，请使用鼠标按键')
    with device_session():
        current=_read(snapshot.mouse)
        if fingerprint(current)!=fingerprint(s):raise ValueError('设备配置已变化，请重新读取后再修改')
        if (value is None or value==s.stages[stage][0]) and (not activate or stage==s.active):return view(snapshot.mouse,current)
        if snapshot.mouse.protocol=='dpi':
            import dpi_extended
            d=dpi_extended.interface(snapshot.mouse)
        else:d=pc.interface(snapshot.mouse)
        try:
            with pc.hw.HidDevice(d.path,write=True) as dev:
                wanted=write(dev,d,current,stage,value,activate)
            after=_read(snapshot.mouse)
        except (OSError,ValueError) as exc:
            raise RuntimeError(f'DPI 设置未完整确认：{exc}；部分改动可能已生效，请重新读取，未自动重试') from exc
        if fingerprint(after)!=fingerprint(wanted):raise RuntimeError('DPI 或其他配置读回不一致；部分修改可能已生效，请重新读取')
        return view(snapshot.mouse,after)
