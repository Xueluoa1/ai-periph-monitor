"""Exact model facts from G-Helper Models; name-only battery IDs are excluded."""
from dataclasses import dataclass

@dataclass(frozen=True)
class Spec:
    name: str
    maximum: int
    step: int = 50
    xy: bool = False
    colors: bool = False
    activate: bool = True
    profiles: int = 5
    variable_count: bool = False

MODELS={}
def add(ids,*args,**kw):
    for pid in ids:MODELS[pid]=Spec(*args,**kw)

add((0x18E5,0x18E3),'ROG Chakram',16000,100,activate=False,profiles=3)
add((0x1A1A,0x1A18),'ROG Chakram X',36000,xy=True,colors=True)
add((0x1A72,0x1A70),'ROG Gladius III Wireless AimPoint',36000,xy=True,colors=True)
add((0x1B0C,0x1B0A),'ROG Gladius III EVA-02',36000,xy=True,colors=True)
add((0x197F,0x197D,0x197B),'ROG Gladius III',26000)
add((0x1960,0x195C,0x195E),'ROG Keris',16000,100,activate=False,profiles=3)
add((0x1A68,0x1A66),'ROG Keris Wireless AimPoint',36000,xy=True,colors=True)
add((0x1908,0x1906),'ROG Pugio II',16000,100,activate=False,profiles=3)
add((0x1979,0x1977),'ROG Spatha X',19000,colors=True)
add((0x1949,0x1947),'ROG Strix Impact II Wireless',16000,100,activate=False,profiles=3)
add((0x19F4,),'ASUS TUF Gaming M4 Wireless',12000,100,profiles=3)
add((0x1B63,),'ROG Harpe Ace Mini',42000,xy=True,colors=True,variable_count=True)
add((0x1B16,),'ROG Keris II Ace',42000,xy=True,colors=True)


def match(d):
    return d.vid==0x0B05 and ((d.pid in MODELS and 'mi_00' in d.path.lower() and d.output_len==65)
        or (d.pid,d.usage_page,d.output_len)==(0x1ACE,0xFF01,64))


def read(dev,d,mouse):
    import mouse_settings as ms
    import peripheral_controls as pc
    from dpi_adapters import Config,validate
    paired=ms.omni_identity(pc.hw.enumerate_hid()).get(pc.hw.physical_group_key(d.path)) if d.pid==0x1ACE else d.pid
    if paired!=mouse.identity[2] or paired not in MODELS:raise ValueError('ROG 型号或接收器配对已变化')
    spec=MODELS[paired];rid=3 if d.pid==0x1ACE else 0
    q=lambda cmd:ms.rog_query(dev,d,rid,cmd)
    profile=q((0x12,0,0))
    if len(profile)<20 or profile[11]>=spec.profiles:raise ValueError('ROG 配置层无效')
    count=profile[19] if spec.variable_count else 4
    active=profile[12]-1
    if count not in (2,3,4) or not (0<=active<count or active==-1 and not spec.activate):raise ValueError('ROG 当前档位或档位数量无效')
    settings=q((0x12,4,0))
    data=q((0x12,4,2)) if spec.xy else settings
    stride=4 if spec.xy else 2;raw=bytes(data[5:5+4*stride])
    if len(raw)!=4*stride:raise ValueError('ROG DPI 表不完整')
    pairs=[]
    for i in range(count):
        x=(int.from_bytes(raw[i*stride:i*stride+2],'little')+1)*spec.step
        y=(int.from_bytes(raw[i*stride+2:i*stride+4],'little')+1)*spec.step if spec.xy else x
        pairs.append((x,y))
    colors=q((0x12,4,3))[5:17] if spec.colors else b''
    if spec.colors and len(colors)!=12:raise ValueError('未读取到需保留的 DPI 指示灯颜色')
    if q((0x12,0,0))[11:13]!=profile[11:13]:raise ValueError('读取期间配置或档位已变化')
    base=pc.State(mouse,spec.name,None,(),profile[11]+1,mouse.identity,raw=bytes(colors)+bytes(settings[13:19]))
    return validate(Config(base,'rog_more',tuple(pairs),active,100,spec.maximum,spec.step,raw,rid,
                           note='' if spec.activate else '该型号仅支持编辑档位数值；切换档位请使用鼠标按键。',can_activate=spec.activate))


def write(dev,d,s,stage,value,activate):
    from dataclasses import replace
    import mouse_settings as ms
    spec=MODELS[s.base.mouse.identity[2]];raw=bytearray(s.raw);pairs=list(s.stages)
    if value is not None:
        if pairs[stage][0]!=pairs[stage][1]:raise ValueError('此 ROG 写入协议同时设置 X/Y，当前分轴不同，请先使用原厂工具调整')
        encoded=(value//spec.step-1).to_bytes(2,'little')
        color=s.base.raw[stage*3:stage*3+3] if spec.colors else b''
        ms.rog_query(dev,d,s.selector,bytes([0x51,0x31,stage,0])+encoded+color)
        stride=4 if spec.xy else 2;raw[stage*stride:(stage+1)*stride]=encoded*(2 if spec.xy else 1)
        pairs[stage]=(value,value)
    active=stage if activate else s.active
    if active!=s.active:ms.rog_query(dev,d,s.selector,(0x51,0x31,9,0,active+1))
    ms.rog_query(dev,d,s.selector,(0x50,3))
    return replace(s,stages=tuple(pairs),active=active,raw=bytes(raw))
