"""AM Infinity .97 polling and lighting, independently implemented from AM Master.

Only normal .97 USB/receiver identities and known configuration commands. No
bridge initialization, pairing, resets or firmware commands. See 适配说明-v27.md.
"""
from dataclasses import dataclass, replace
import math
import time

import am_battery as am
import am_dpi

RATES = (125, 250, 500, 1000, 2000, 4000, 8000)
GET_RATE, SET_RATE = 0x3011, 0x3010
READ_NV, SAVE_RGB = 0x0A0C, 0x0A0D
SET_LIGHT, REALTIME = 0x30B3, 0x30C0
GET_DONGLE, SET_DONGLE = 0x300E, 0x300D
LIGHT_NV, PATTERN_NV = 0x600A, 0x6200
MOUSE_MODES = {0: '常亮', 1: '呼吸', 2: '色彩循环'}
DONGLE_MODES = {1: '常亮', 2: '呼吸', 7: '流动', 4: '马赛克', 5: '极光', 6: '夜城', 3: '弧光'}


@dataclass(frozen=True)
class State:
    rate: int
    settings: bytes | None
    pattern: bytes | None
    dongle: bytes | None
    errors: tuple = ()


@dataclass(frozen=True)
class MouseLight:
    enabled: bool
    mode: int
    speed: int
    # Empty means preserve the complete existing animation, including unknown
    # colors; a first animation frame is not the original color palette.
    colors: tuple = ()


class Response:
    def __init__(self, target, command, nv=None):
        self.target, self.command, self.nv = target, command, nv
        self.pending = bytearray()

    def feed(self, raw):
        if not raw or raw[0] != am.REPORT_ID:
            return None
        if len(raw) < 3:
            raise ValueError('怒喵配置响应头不完整')
        size, target = raw[1:3]
        if target != self.target or not size:
            return None
        if size > 59 or size > len(raw)-3:
            raise ValueError('怒喵配置响应分片长度无效')
        # Consecutive RGB fragments can be identical; never deduplicate data.
        self.pending.extend(raw[3:3+size])
        while len(self.pending) >= 6:
            length = int.from_bytes(self.pending[2:4], 'little') + 4
            if self.pending[0] != 5 or not 6 <= length <= 4096:
                raise ValueError('怒喵配置响应帧无效')
            if len(self.pending) < length:
                return None
            frame = bytes(self.pending[:length]);del self.pending[:length]
            if frame[1] not in (0x5B, 0x5D) or int.from_bytes(frame[4:6], 'little') != self.command:
                continue
            payload = frame[6:]
            # Receiver lighting has no status byte: its first byte is enabled.
            if self.command != GET_DONGLE and (not payload or payload[0] != 0):
                raise ValueError(f'怒喵拒绝配置命令 {self.command:04X}')
            if self.nv is not None and (len(payload) < 5 or int.from_bytes(payload[1:3], 'little') != self.nv):
                continue
            return payload
        return None


def packets(command, payload, target, length):
    if target not in (0, 0x80) or not 62 <= length <= 1024:
        raise ValueError('怒喵配置通道无效')
    sizes = {GET_RATE: 0, SET_RATE: 2, READ_NV: 4, SET_LIGHT: 3,
             GET_DONGLE: 0, SET_DONGLE: 13, REALTIME: 7}
    if command == SAVE_RGB:
        if (len(payload) < 9 or payload[:3] != b'\x00\x62\x01'
                or int.from_bytes(payload[3:5], 'little') != len(payload)-6
                or len(payload) > 1002 or payload[5] != 16):
            raise ValueError('怒喵 RGB 图案数据无效')
    elif command not in sizes or len(payload) != sizes[command]:
        raise ValueError('未适配的怒喵配置命令或长度')
    if command in (GET_DONGLE, SET_DONGLE) and target != 0:
        raise ValueError('接收器灯光必须使用本地目标')
    if command == SET_RATE and int.from_bytes(payload, 'little') not in RATES:
        raise ValueError('回报率无效')
    if command == READ_NV and (int.from_bytes(payload[:2], 'little') not in (LIGHT_NV, PATTERN_NV) or payload[2:] != b'\xe8\x03'):
        raise ValueError('未适配的 NV 查询')
    frame = b'\x05\x5a' + (len(payload)+2).to_bytes(2, 'little') + command.to_bytes(2, 'little') + payload
    return [(bytes((20, len(frame[i:i+59]), target))+frame[i:i+59]).ljust(length, b'\0')
            for i in range(0, len(frame), 59)]


def exchange(dev, iface, command, payload=b'', *, no_reply=False):
    target = 0 if command in (GET_DONGLE, SET_DONGLE) else am.IDENTITIES[(iface.vid, iface.pid)]
    if command in (GET_DONGLE, SET_DONGLE) and iface.pid != 0x0703:
        raise ValueError('接收器灯光需要选择 2.4G 接收器')
    parser = Response(target, command, int.from_bytes(payload[:2], 'little') if command == READ_NV else None)
    for packet in packets(command, payload, target, iface.feature_len):
        dev.set_feature_report(packet)
        time.sleep(.001)
    if no_reply:
        time.sleep(.18)
        return None
    deadline = time.monotonic()+3
    while time.monotonic() < deadline:
        time.sleep(.02)
        result = parser.feed(dev.get_feature_report(am.REPORT_ID, iface.feature_len))
        if result is not None:
            return result
    raise TimeoutError(f'怒喵配置命令 {command:04X} 超时，请唤醒鼠标并关闭官方驱动后重试')


def read_nv(dev, iface, address):
    payload = exchange(dev, iface, READ_NV, address.to_bytes(2,'little')+b'\xe8\x03')
    if len(payload) < 6 or payload[0] or int.from_bytes(payload[1:3],'little') != address:
        raise ValueError('怒喵灯光 NV 响应无效')
    size = int.from_bytes(payload[3:5],'little')
    if not 1 <= size <= 1000 or len(payload) != size+5:
        raise ValueError('怒喵灯光 NV 长度不完整')
    return bytes(payload[5:])


def validate_settings(raw):
    if len(raw) < 3 or raw[0] not in (0,1) or raw[1] not in MOUSE_MODES or not 1 <= raw[2] <= 10:
        raise ValueError('怒喵鼠标灯光配置未识别')


def validate_pattern(raw):
    if len(raw) < 7 or raw[0] != 1 or raw[3] == 0 or int.from_bytes(raw[1:3],'little') != len(raw)-4 or (len(raw)-4)%3:
        raise ValueError('怒喵鼠标灯光图案未识别或不完整')


def validate_dongle(raw):
    if (len(raw) != 13 or raw[0] not in (0,1) or raw[1] not in DONGLE_MODES
            or not 81 <= raw[11] <= 100 or not 1 <= raw[12] <= 255
            or any(int.from_bytes(raw[i:i+2],'big') > 1530 or not 1 <= raw[i+2] <= 100 for i in (2,5,8))):
        raise ValueError('接收器灯光参数无效')


def read(dev, iface):
    am_dpi.validate_channel(dev, iface)
    profile = am_dpi.exchange(dev, iface, am_dpi.GET_PROFILE)
    raw = exchange(dev, iface, GET_RATE)
    if len(raw) != 3 or raw[0] or int.from_bytes(raw[1:],'little') not in RATES:
        raise ValueError('怒喵回报率响应无效')
    rate = int.from_bytes(raw[1:],'little')
    values = [None,None,None];errors=[]
    for index, label, query, validate in (
            (0,'鼠标灯光',lambda:read_nv(dev,iface,LIGHT_NV),validate_settings),
            (1,'灯光图案',lambda:read_nv(dev,iface,PATTERN_NV),validate_pattern),
            (2,'接收器灯光',lambda:exchange(dev,iface,GET_DONGLE),validate_dongle)):
        if index == 2 and iface.pid != 0x0703:continue
        try:
            value=query();validate(value);values[index]=value
        except (OSError,ValueError) as exc:
            errors.append(f'{label}：{exc}')
    if am_dpi.exchange(dev,iface,am_dpi.GET_PROFILE) != profile:
        raise ValueError('怒喵配置层已变化，请重新读取')
    return State(rate,*values,tuple(errors))


def make_pattern(light):
    colors=light.colors
    if not 1 <= len(colors) <= 8 or any(len(c)!=3 or any(type(v) is not int or not 0<=v<=255 for v in c) for c in colors):
        raise ValueError('请提供 1–8 个有效 RGB 颜色')
    if light.mode in (0,1) and len(colors)!=1:
        raise ValueError('当前常亮和呼吸灯适配使用一个颜色')
    # The official period is 500–4000ms, descending as speed rises; 16ms/frame.
    frames=max(2,math.floor((500+(10-light.speed)*3500/9)/16+.5))
    if light.mode == 0:
        data=bytes(colors[0])
    elif light.mode == 1:
        data=bytes(math.floor(v*max(0,1-abs(2*i/frames-1))+.5)
                   for i in range(1,frames+1) for v in colors[0])
    else:
        frames=max(frames,len(colors))
        data=b''.join(bytes(c)*(frames//len(colors)+(i<frames%len(colors))) for i,c in enumerate(colors))
    return bytes((1,))+len(data).to_bytes(2,'little')+b'\x10'+data


def expected(state, rate=None, light=None, dongle=None):
    if rate is not None and (type(rate) is not int or rate not in RATES):
        raise ValueError('怒喵回报率必须为 125–8000 Hz 的可选档位')
    settings,pattern=state.settings,state.pattern
    if light is not None:
        if (not isinstance(light,MouseLight) or type(light.enabled) is not bool
                or type(light.mode) is not int or light.mode not in MOUSE_MODES
                or type(light.speed) is not int or not 1 <= light.speed <= 10):
            raise ValueError('鼠标灯效或速度无效')
        if settings is None:
            raise ValueError('鼠标灯光未读取成功，请重新读取')
        if (light.mode,light.speed) != tuple(settings[1:3]) and not light.colors:
            raise ValueError('修改灯效或速度时，请填写颜色以生成完整灯光图案')
        if light.colors:
            if pattern is None:raise ValueError('灯光图案未读取成功，暂不能覆盖')
            pattern=make_pattern(light)
        settings=bytes((int(light.enabled),light.mode,light.speed))+settings[3:]
    if dongle is not None:
        validate_dongle(dongle)
        if state.dongle is None:raise ValueError('接收器灯光未读取成功')
    return replace(state,rate=state.rate if rate is None else rate,settings=settings,
                   pattern=pattern,dongle=state.dongle if dongle is None else bytes(dongle))


def write(dev, iface, before, after):
    am_dpi.validate_channel(dev,iface)
    if after.rate != before.rate:
        exchange(dev,iface,SET_RATE,after.rate.to_bytes(2,'little'))
    if after.settings != before.settings:
        exchange(dev,iface,SET_LIGHT,after.settings[:3],no_reply=True)
    if after.pattern != before.pattern:
        p=after.pattern
        exchange(dev,iface,SAVE_RGB,b'\x00\x62'+p,no_reply=iface.pid==0x0703)
        if after.settings[0] and after.settings[1] == 0:
            # Instant solid-color preview follows persistent pattern save.
            exchange(dev,iface,REALTIME,b'\x80\x00\x10\x01'+p[4:7],no_reply=True)
    if after.dongle != before.dongle:
        exchange(dev,iface,SET_DONGLE,after.dongle,no_reply=True)


def fingerprint(state):
    return state.rate,state.settings,state.pattern,state.dongle
