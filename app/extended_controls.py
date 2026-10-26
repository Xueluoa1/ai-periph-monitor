"""Model-specific controls layered on the validated v31 DPI snapshots.

Each write checks a fresh snapshot and reads back the result, including unrelated
configuration bytes. Protocol facts and limitations are in 适配说明-v32.md.
"""
from dataclasses import dataclass, replace
import time
import dpi_extended as dx
import dpi_adapters as da
import dpi_more as dm
import dpi_hecate as he
import dpi_pulsar as pu
import peripheral_controls as pc
import mouse_settings as ms
from device_io import device_session

RATES = (125, 250, 500, 1000, 2000, 4000, 8000)
SCALAR = dict(zip(RATES, (8, 4, 2, 1, 16, 32, 64)))


@dataclass(frozen=True)
class Zone:
    key: int
    name: str
    mode: int
    modes: tuple = ()
    rgb: tuple | None = None
    brightness: int | None = None
    maximum: int = 100
    speed: int | None = None
    speeds: tuple = ()
    aux: bytes = b''
    write_only: bool = False
    channel: int = 0


@dataclass(frozen=True)
class State:
    config: object
    rate: int | None = None
    codes: tuple = ()
    zones: tuple = ()
    light_raw: bytes = b''
    meta: tuple = ()
    rate_error: str = ''
    light_error: str = ''


def supported(mouse):
    return mouse.protocol == 'dpi'


def pair(value):
    return bytes((value, (0x55-value) & 255))


def decode(code, codes):
    for hz, value in codes:
        if code == value:return hz
    raise ValueError('设备返回了尚未核实的回报率编码')


def rate_info(dev, d, s):
    f, raw = s.family, s.raw
    codes = tuple(SCALAR.items());meta = ()
    if f == 'rog_more':
        rates = RATES[1:4] if s.base.mouse.identity[2] in (0x1A1A,0x1A18,0x1979,0x1977) else RATES[:4]
        codes = tuple((hz,RATES.index(hz)) for hz in rates);code = s.base.raw[-6]
    elif f == 'a5':
        rates = (125,500,1000) if d.pid == 0xF019 else RATES[:4]
        codes = tuple((hz,SCALAR[hz]) for hz in rates)
        data = pc.page(dev,2,1,0x80,1)
        if len(data)!=1:raise ValueError('A5 回报率回复长度无效')
        code = data[0]
    elif f == 'atlantis':
        codes = tuple(zip((500,1000,2000,4000),(2,0x10,0x20,0x40))) if d.pid in (0xF510,0xF517) else tuple(SCALAR.items())[:4]
        code = raw[0]
        # Older wireless firmware also uses the 1K scalar encoding.
        if code == 1 and d.pid in (0xF510,0xF517):code = 0x10
    elif f in ('vxe','scyrox','pulsar'):
        if sum(raw[:2]) & 255 != 0x55:raise ValueError('回报率配置校验失败')
        ceiling = (1000,4000,1000,8000,2000,8000)[s.base.identity[-2]] if f == 'pulsar' else 8000
        rates = (250,500,1000) if f == 'vxe' else tuple(hz for hz in RATES if hz<=ceiling)
        codes = tuple((hz,SCALAR[hz]) for hz in rates);code = raw[0]
    elif f == 'rk':
        codes = tuple((hz,c | c<<4) for hz,c in zip(RATES,(3,2,1,0,6,5,4)));code = raw[1]
    elif f == 'ajazz':
        codes = tuple((hz,i+1) for i,hz in enumerate(RATES[:4]));code = raw[9]
    elif f in ('wl4k','hecate'):
        rates = RATES[:6] if f == 'wl4k' else RATES[:5]
        codes = tuple((hz,i) for i,hz in enumerate(rates));code = raw[11]
    elif f == 'egg':
        v2 = s.base.identity[-1] in (0x1982,0x1984)
        rates = (125,1000,2000,4000) if v2 else (1000,2000,4000)
        codes = tuple((hz,8000//hz) for hz in rates)
        code = 8 if raw[21] == 128 and v2 else raw[21]
    elif f == 'logitech':
        index = s.base.mouse.identity[2];connection = int(index != 255)
        fid = 0x8061;fi = dx.feature_index(dev,index,fid)
        if not fi:fid = 0x8060;fi = dx.feature_index(dev,index,fid)
        if not fi:raise ValueError('设备没有上报 8060/8061 回报率功能')
        call = lambda fn,args=():dx.logitech_call(dev,index,fi,fn,args)
        if fid == 0x8061:
            mask = int.from_bytes(call(1,(connection,))[:2],'big')
            codes = tuple((hz,i) for i,hz in enumerate(RATES) if mask & (1<<i))
            code = call(2,(connection,))[0]
        else:
            mask = call(0)[0]
            codes = tuple(sorted((round(1000/(i+1)),i+1) for i in range(8) if mask & (1<<i)))
            code = call(1)[0]
        meta = (fid,fi,connection)
    else:raise ValueError('该接口没有已核实的回报率通道')
    rate = decode(code,codes)
    error = ''
    if f == 'ajazz':codes = tuple(item for item in codes if item[0]!=250)
    if f == 'wl4k' and raw[10]:
        codes = tuple(item for item in codes if item[0]<=1000)
        error = '当前性能模式限制回报率为 1000 Hz 及以下；不会自动改变性能模式。'
    if f == 'egg' and s.base.identity[-1] in (0x1982,0x1984) and raw[127]:
        codes = ();error = '玻璃模式已启用，暂不改写回报率。'
    if f == 'logitech' and s.base.identity[-1] == 1:
        codes = ();error = '当前为板载模式；请先在原厂软件切换软件控制模式，再重新读取。'
    return rate,codes,meta,error


def _read(mouse):
    import extended_lighting as lights
    config = dx.read(mouse);d = dx.interface(mouse)
    state = State(config)
    with pc.hw.HidDevice(d.path,write=True) as dev:
        try:
            rate,codes,meta,error = rate_info(dev,d,config)
            state = replace(state,rate=rate,codes=codes,meta=meta,rate_error=error)
        except (OSError,ValueError,TimeoutError,IndexError) as exc:
            state = replace(state,rate_error=str(exc))
        try:
            zones,raw = lights.read(dev,d,config)
            state = replace(state,zones=zones,light_raw=raw)
        except (OSError,ValueError,TimeoutError,IndexError) as exc:
            state = replace(state,light_error=str(exc))
        if config.family=='vxe' and ms.atk_profile(dev,d)!=config.base.profile:
            raise ValueError('读取灯光期间 VXE 配置层已变化，请重新读取')
    return state


def read(mouse):
    with device_session():return _read(mouse)


def fingerprint(s):
    return (da.fingerprint(s.config),s.rate,s.codes,s.zones,s.light_raw,s.meta,s.rate_error,s.light_error)


def persist_wl(dev,s,raw):
    dx.wl_query(dev,1)
    try:
        for i in range(0,117,24):
            if raw[i:i+24] != s.raw[i:i+24]:
                dx.wl_query(dev,6,((s.base.profile-1)<<7)+i,bytes(raw[i:i+24]))
    finally:dx.wl_query(dev,2)


def persist_hecate(dev,s,raw,rate=None):
    with he.session(dev):
        if he.block(dev,3,34)!=s.base.raw or he.query(dev,5,length=1)!=b'\0':
            raise ValueError('G3M 传感器或配置已经变化')
    if rate is not None:
        with he.session(dev):he.query(dev,0x29,data=bytes([rate]))
    with he.session(dev):
        chunk=s.base.raw[6]
        for i in range(0,84,chunk):he.query(dev,6,i,bytes(raw[i:i+chunk]))


def egg_write(dev,command,payload,length):
    dx.egg_command(dev,command,bytes([15,length,0,0,0]),payload)
    for _ in range(8):
        r=dev.get_feature_report(0xA1,64)
        if len(r)==64 and r[:2]==b'\xa1\x01':return
        if len(r)==64 and r[1] not in (0,3):raise ValueError('Endgame 拒绝配置写入')
        time.sleep(.08)
    raise TimeoutError('Endgame 未确认配置写入')


def write_rate(dev,d,state,rate):
    s=state.config;raw=bytearray(s.raw);code=dict(state.codes)[rate];f=s.family
    if f == 'rog_more':
        ms.rog_query(dev,d,s.selector,(0x51,0x31,4,0,code))
        ms.rog_query(dev,d,s.selector,(0x50,3))
        other=bytearray(s.base.raw);other[-6]=code
        s=replace(s,base=replace(s.base,raw=bytes(other)))
    elif f == 'a5':pc.page(dev,2,1,0,1,(code,))
    elif f == 'vxe':
        raw[:2]=pair(code)
        dx.vxe_write_row(dev,d,0,bytes(raw[:10] if d.pid==0xF58A else raw[:2]))
    elif f == 'atlantis':
        raw[:2]=pair(code);dx.atlantis_query(dev,d,7,0,bytes(raw[:2]))
    elif f == 'scyrox':
        raw[:2]=pair(code)
        with dm.scyrox_session(dev,d,s.base.identity[2:]):dm.scyrox_query(dev,d,7,0,bytes(raw[:2]))
    elif f == 'pulsar':
        raw[:2]=pair(code)
        with pu.session(dev,d,s.base.identity[2:]):pu.query(dev,d,7,0,bytes(raw[:2]))
    elif f == 'rk':
        raw[1]=code;dev.set_feature_report(dm.rk_packet(0x31,0x35,1,bytes([code])));time.sleep(.15)
    elif f == 'ajazz':
        raw[9]=code;packet=bytearray(raw);packet[:4]=bytes.fromhex('0f010a2f')
        dev.drain();dev.write_output(b'\xf0'+packet,use_hidd=False);time.sleep(.5)
    elif f == 'wl4k':
        raw[11]=code;persist_wl(dev,s,raw)
    elif f == 'hecate':
        raw[11]=code;persist_hecate(dev,s,raw,code)
    elif f == 'egg':
        raw[21]=code
        payload=bytes(raw[i] for i in (28,21,22,20,77,84,91,98,105,19))
        if s.base.identity[-1] in (0x1982,0x1984):payload+=bytes([raw[127]])
        egg_write(dev,0x15,payload,10)
    elif f == 'logitech':
        fid,fi,_=state.meta
        dx.logitech_call(dev,s.base.mouse.identity[2],fi,3 if fid==0x8061 else 2,(code,))
    else:raise ValueError('没有此型号的回报率写入器')
    return replace(state,config=replace(s,raw=bytes(raw)),rate=rate)


def validate_zone(old,new):
    if replace(new,mode=old.mode,rgb=old.rgb,brightness=old.brightness,speed=old.speed)!=old:
        raise ValueError('灯区身份或能力不能修改')
    if type(new.mode) is not int or (new.mode not in dict(old.modes) if old.modes else new.mode!=old.mode):
        raise ValueError('此灯区不支持所选灯效')
    if old.rgb is None:
        if new.rgb is not None:raise ValueError('此灯区没有颜色控制')
    elif not isinstance(new.rgb,tuple) or len(new.rgb)!=3 or any(type(v) is not int or not 0<=v<=255 for v in new.rgb):
        raise ValueError('颜色必须为有效的 RGB 值')
    if old.brightness is None:
        if new.brightness is not None:raise ValueError('此灯区没有亮度控制')
    elif type(new.brightness) is not int or not (0<=new.brightness<=old.maximum or new.brightness==old.brightness==-1 and new.mode in (0,2)):
        raise ValueError('亮度超出设备范围')
    if old.speed is None:
        if new.speed is not None:raise ValueError('此灯区没有速度控制')
    elif type(new.speed) is not int or (new.speed not in old.speeds and not (new.speed==-1 and new.mode in (0,1) and old.channel in (0x8070,0x8071))):
        raise ValueError('不支持此动画速度')


def apply(state,*,rate=None,zone=None):
    import extended_lighting as lights
    if (rate is None)==(zone is None):raise ValueError('每次只修改一项设置')
    if rate is not None:
        if type(rate) is not int or rate not in dict(state.codes):raise ValueError('此设备或连接不支持所选回报率')
    else:
        old=next((z for z in state.zones if z.key==zone.key),None)
        if old is None:raise ValueError('灯区不存在')
        validate_zone(old,zone)
        if state.config.family=='vxe' and zone.key==100 and zone.mode==2 and zone.brightness!=old.brightness:
            raise ValueError('此型号呼吸模式保留原亮度；请先在常亮模式设置亮度')
    mouse=state.config.base.mouse
    with device_session():
        fresh=_read(mouse)
        if fingerprint(fresh)!=fingerprint(state):raise ValueError('设备或设置已变化，请重新读取')
        if rate is not None and rate==state.rate or zone is not None and zone==old:return fresh
        d=dx.interface(mouse)
        with pc.hw.HidDevice(d.path,write=True) as dev:
            expected=write_rate(dev,d,fresh,rate) if rate is not None else lights.write(dev,d,fresh,zone)
        result=_read(mouse)
        if fingerprint(result)!=fingerprint(expected):
            raise ValueError('写入后未确认完整配置一致；设置可能已部分生效，请重新读取。没有自动重试写入')
        return result
