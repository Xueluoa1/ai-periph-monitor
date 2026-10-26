"""Mi Gaming Mouse 2, independently implemented from public G-HUB wire facts.

Source: d.mihub.fun, index-BbzxWOJP.js (M002). See 适配说明-v1.0.2.md.
Only exact FFEF interfaces; no macro, reset, firmware or profile-switch commands.
"""
from dataclasses import dataclass, replace
import time
import hid_win as hw
from device_io import device_session

NAME = '小米电竞鼠标 2'
RATES = (125,250,500,1000,2000,4000,8000)
CODES = dict(zip(RATES,(1,2,4,8,16,32,64)))
GETS = frozenset((0x16,0x80,0x2B,0x2D,0x25))
SETS = frozenset((0x2C,0x2E,0x26))


def claims(d):
    return d.vid==0x2717 and d.pid in (0x5110,0x5111)


def matches(d):
    return claims(d) and (d.usage_page,d.usage,d.feature_len,d.input_len,d.output_len)==(0xFFEF,0,64,64,64)


def target(d):
    if not matches(d):raise ValueError('小米电竞鼠标 2 接口不匹配')
    return int(d.pid==0x5111)


def packet(d,cmd,payload=b''):
    if cmd not in GETS|SETS:raise ValueError('未开放的小米命令')
    # Official DPI setter declares 7 although the complete row follows it.
    length=7 if cmd==0x2E else len(payload)
    return bytes((5,target(d),cmd,length))+bytes(payload)+bytes(60-len(payload))


def decode_reply(raw,d,cmd):
    raw=bytes(raw)
    if len(raw)!=64 or raw[0]!=5 or raw[2]!=cmd:return None
    if raw[1]!=target(d):
        raise ValueError(f'设备未就绪或回复目标不匹配（状态 0x{raw[1]:02X}）')
    size=raw[3]
    if not 1<=size<=60:raise ValueError('小米回复长度无效')
    return raw[4:4+size]


def exchange(dev,d,cmd,payload=b''):
    dev.set_feature_report(packet(d,cmd,payload))
    time.sleep(.1)
    # Query response polling only; never resend a setting after a timeout.
    for _ in range(4):
        result=decode_reply(dev.get_feature_report(5,64),d,cmd)
        if result is not None:return result
        time.sleep(.05)
    raise TimeoutError('小米设备未返回匹配回复，请唤醒设备并关闭官方驱动后重读')


def battery(dev,d):
    data=exchange(dev,d,0x16)
    if len(data)!=3 or data[0] not in (0,1,2) or data[1]>100:
        raise ValueError('小米电量回复无效')
    return data[1], 'charging' if data[0]==2 else 'discharging'


def battery_read(d,result_type):
    extra={'model_name':NAME}
    try:
        target(d)
        with hw.HidDevice(d.path,write=True) as dev:level,status=battery(dev,d)
        return result_type(ok=True,level=level,raw_level=level,status=status,source='小米电竞鼠标 2 专用协议',extra=extra)
    except (OSError,ValueError) as exc:
        return result_type(ok=False,source='小米电竞鼠标 2 专用协议',detail=str(exc),extra=extra)


@dataclass(frozen=True)
class Config:
    profile: int
    count: int
    active: int
    rows: tuple
    rate_data: bytes

    @property
    def rate(self):return next(hz for hz,code in CODES.items() if code==self.rate_data[2])

    @property
    def rates(self):
        mode=self.rate_data[5]
        maximum=8000 if mode==4 else 4000 if mode==3 else 1000
        return tuple(r for r in RATES if r<=maximum)


def profile(dev,d):
    value=exchange(dev,d,0x80)
    if len(value)!=1 or not 1<=value[0]<=5:raise ValueError('小米配置层无效')
    return value[0]


def read_config(dev,d):
    p=profile(dev,d)
    mode=exchange(dev,d,0x2B,bytes((p,)))
    if len(mode)!=3 or mode[0]!=p or not 1<=mode[1]<=5 or not 0<=mode[2]<mode[1]:
        raise ValueError('小米 DPI 档位信息无效')
    rows=[]
    for stage in range(mode[1]):
        data=exchange(dev,d,0x2D,bytes((p,stage)))
        if len(data)!=10 or data[:2]!=bytes((p,stage)) or data[7] not in (0,1):
            raise ValueError('小米 DPI 回复与档位不匹配')
        if not 100<=int.from_bytes(data[2:4],'little')<=40000:
            raise ValueError('小米 X DPI 无效')
        if data[7] and not 100<=int.from_bytes(data[8:10],'little')<=40000:
            raise ValueError('小米 Y DPI 无效')
        rows.append(data[1:])
    rate=exchange(dev,d,0x25,bytes((p,)))
    if (len(rate)!=7 or rate[:2]!=bytes((p,7)) or rate[2] not in CODES.values()
            or rate[3:5]!=b'\0\0' or rate[5] not in (2,3,4) or rate[6] not in (0,1)):
        raise ValueError('小米回报率配置尚未识别')
    if profile(dev,d)!=p or exchange(dev,d,0x2B,bytes((p,)))!=mode:
        raise ValueError('读取期间配置层或档位变化，请重读')
    return Config(p,mode[1],mode[2],tuple(rows),rate)


def interface(mouse):
    d=next((d for d in hw.enumerate_hid() if d.path==mouse.path),None)
    if d is None:raise OSError('设备已断开')
    if not matches(d) or (d.vid,d.pid)!=mouse.identity:raise ValueError('设备接口已变化')
    return d


def to_dpi(mouse,config):
    import mouse_settings as ms
    stages=[]
    for row in config.rows:
        x=int.from_bytes(row[1:3],'little')
        y=int.from_bytes(row[7:9],'little') if row[6] else x
        stages.append((x,y))
    return ms.DpiState(mouse,stages,config.active,100,40000,1,profile=config.profile,
        colors=[row[3:6] for row in config.rows],protocol_data=config)


def read(mouse):
    with device_session():
        d=interface(mouse)
        with hw.HidDevice(d.path,write=True) as dev:return to_dpi(mouse,read_config(dev,d))


def expected(config,stage=None,value=None,activate=False,rate=None,rgb=None):
    if stage is not None and (type(stage) is not int or not 0<=stage<config.count):raise ValueError('档位无效')
    if value is not None and (type(value) is not int or not 100<=value<=40000):raise ValueError('DPI 范围为 100–40000')
    if (value is not None or rgb is not None or activate) and stage is None:raise ValueError('请选择档位')
    if rate is not None and (type(rate) is not int or rate not in config.rates):
        raise ValueError('当前性能模式不支持此回报率，请先在官方驱动中切换模式并重新读取')
    if rgb is not None and (len(rgb)!=3 or any(type(v) is not int or not 0<=v<=255 for v in rgb)):
        raise ValueError('颜色无效')
    rows=list(config.rows)
    if value is not None or rgb is not None:
        row=bytearray(rows[stage])
        if value is not None:
            if row[6]:raise ValueError('此档启用了独立 X/Y DPI，请在官方驱动调整；软件切换档位仍可用')
            row[1:3]=value.to_bytes(2,'little');row[7:9]=row[1:3]
        if rgb is not None:row[3:6]=bytes(rgb)
        rows[stage]=bytes(row)
    rate_data=config.rate_data if rate is None else config.rate_data[:2]+bytes((CODES[rate],))+config.rate_data[3:]
    return replace(config,rows=tuple(rows),active=stage if activate else config.active,rate_data=rate_data)


def apply(snapshot,stage=None,value=None,activate=False,rate=None,rgb=None):
    old=snapshot.protocol_data
    new=expected(old,stage,value,activate,rate,rgb) # Validate before device I/O.
    with device_session():
        current=read(snapshot.mouse)
        if current.protocol_data!=old:raise ValueError('设备配置已变化，请重新读取')
        if new==old:return current
        d=interface(snapshot.mouse)
        try:
            with hw.HidDevice(d.path,write=True) as dev:
                if new.rows!=old.rows:exchange(dev,d,0x2E,bytes((old.profile,))+new.rows[stage])
                if new.active!=old.active:exchange(dev,d,0x2C,bytes((old.profile,old.count,new.active)))
                if new.rate_data!=old.rate_data:exchange(dev,d,0x26,new.rate_data)
            result=read(snapshot.mouse)
        except (OSError,ValueError) as exc:
            raise RuntimeError(f'设置未完整确认：{exc}；部分改动可能已生效，未自动重试') from exc
        if result.protocol_data!=new:raise RuntimeError('写后读回不一致，请重新读取；部分改动可能已生效')
        return result
