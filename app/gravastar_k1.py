"""K1 2.4G adapter derived from official v3.2 driver; awaiting hardware testing.

Protocol evidence: output/GravaStar-K1-协议静态分析.md. Never uses a VID-wide
fallback. Commands here only address the matched vendor collection.
"""
from dataclasses import dataclass
import ctypes
import time

import hid_win as hw
from device_io import device_session

IDENTITY = (0x3554, 0xFA09)
NAME = '重力星球 GravaStar Mercury K1'
SIGNATURES = {bytes.fromhex(s): NAME + suffix for s, suffix in (
    ('03 00 00 00 01 B4', ''), ('03 00 00 00 03 F5', ' DE'),
    ('03 00 00 00 04 01', ' JP'))}
RID = 0x13
# The first 18 LedOpt entries are built-in effects. Hardware 21 is a custom
# per-key profile: preserve it when reading, but do not edit that profile here.
MODES = tuple((i, f'灯效 {i:02d}') for i in range(1, 19))
COLOR_MODES = frozenset((1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 18))


def claims(d):
    return (d.vid, d.pid) == IDENTITY


def matches(d):
    return claims(d) and (d.usage_page, d.usage, d.input_len, d.output_len) == (0xFF02, 2, 20, 20)


def report_ids(dev, kind):
    ids = {int(c.ReportID) for c in dev.value_caps(kind)}
    count = int(getattr(dev.caps, 'NumberInputButtonCaps' if kind == hw.HidP_Input else 'NumberOutputButtonCaps'))
    if count:
        caps = (hw.HIDP_BUTTON_CAPS * count)()
        n = ctypes.c_ushort(count)
        status = hw.hid.HidP_GetButtonCaps(kind, ctypes.byref(caps), ctypes.byref(n), dev._preparsed)
        if status != hw.HIDP_STATUS_SUCCESS or n.value > count:
            raise ValueError('无法核对 K1 报告 ID')
        ids.update(int(c.ReportID) for c in caps[:n.value])
    return ids


def check_interface(dev, d):
    if not matches(d) or not dev.write_access:
        raise ValueError('K1 接口不符合已分析的 2.4G 配置通道')
    if any(RID not in report_ids(dev, kind) for kind in (hw.HidP_Input, hw.HidP_Output)):
        raise ValueError('K1 接口没有声明报告 0x13，已停止查询')


def frame(command, data=b'', count=1, index=0):
    if len(data) > 14 or not 0 <= index < count <= 127:
        raise ValueError('K1 分包参数无效')
    packet = bytearray(bytes((RID, command, count, index, len(data))) + data)
    packet.extend(bytes(20-len(packet)))
    packet[19] = sum(packet[:19]) & 255
    return bytes(packet)


def receive(dev, command, length, *, ack_index=None):
    deadline = time.monotonic() + 3
    parts = {}; total = None
    while time.monotonic() < deadline:
        r = dev.read_input(timeout_ms=100)
        if not r or r[0] != RID:
            continue
        if len(r) != 20:
            raise ValueError('K1 返回长度错误')
        if r[1] & 0x7F != command:
            continue
        if r[1] & 0x80 or r[2] & 0x80:
            raise ValueError('K1 拒绝命令或报告通信校验错误')
        if (sum(r[:19]) & 255) != r[19]:
            raise ValueError('K1 返回校验失败')
        count, index, size = r[2], r[3], r[4] & 15
        if not 1 <= count <= 127 or index >= count or size > 14 or r[4] >> 4:
            raise ValueError('K1 返回分包头无效')
        if ack_index is not None:
            if index == ack_index:
                return b''
            continue
        if count != (length+13)//14:
            raise ValueError('K1 返回分包数量不符')
        if total is not None and total != count:
            raise ValueError('K1 返回分包数量发生变化')
        total = count
        expected = min(14, length-index*14)
        if size != expected:
            raise ValueError('K1 返回数据长度不符')
        data = bytes(r[5:5+size])
        if index in parts and parts[index] != data:
            raise ValueError('K1 重复分包内容不一致')
        parts[index] = data
        if len(parts) == total:
            return b''.join(parts[i] for i in range(total))
    raise TimeoutError('K1 未返回完整数据，请唤醒键盘并关闭其他配置软件后重新读取')


def query(dev, command, length):
    if {5:6, 7:1, 0x4A:2, 0x44:128, 0x49:420}.get(command) != length:
        raise ValueError('不支持的 K1 查询')
    dev.drain()
    dev.write_output(frame(command), use_hidd=False)
    return receive(dev, command, length)


def write_blob(dev, command, data):
    if (command, len(data)) not in ((4, 128), (9, 420)):
        raise ValueError('不支持的 K1 设置数据')
    count = (len(data)+13)//14
    dev.drain()
    for i in range(count):
        time.sleep(.015)
        dev.write_output(frame(command, data[i*14:i*14+14], count, i), use_hidd=False)
        # No retry after a setting timeout: the hardware may already have applied it.
        receive(dev, command, 0, ack_index=i)


def identify(dev):
    if query(dev, 7, 1) != b'\x01':
        raise OSError('K1 接收器未报告键盘在线')
    signature = query(dev, 5, 6)
    if signature not in SIGNATURES:
        raise ValueError('接收器连接的不是已适配的 K1 标准版，已停止操作')
    return signature


def battery_value(data):
    if len(data) != 2 or data[0] > 100 or data == b'\0\0' or data[1] == 255:
        raise ValueError('K1 未返回有效电量（空值不会当作 0%）')
    return data[0], bool(data[1] & 0xF0) and not bool(data[1] & 15)


def battery_read(d, result_type):
    source = 'GravaStar K1 2.4G（待实机验证）'
    extra = {'model_name': 'GravaStar K1 系列接收器（型号待确认）'}
    try:
        with hw.HidDevice(d.path, write=True) as dev:
            check_interface(dev, d)
            signature = identify(dev)
            extra['model_name'] = SIGNATURES[signature]
            raw = query(dev, 0x4A, 2)
            level, charging = battery_value(raw)
            if identify(dev) != signature:
                raise ValueError('读取期间接收器配对型号已变化')
            return result_type(ok=True, level=level, raw_level=level, raw=raw,
                status='charging' if charging else 'discharging', source=source, extra=extra)
    except (OSError, ValueError, TimeoutError) as exc:
        return result_type(ok=False, source=source, detail=str(exc), extra=extra)


@dataclass(frozen=True)
class State:
    mouse: object
    signature: bytes
    raw: bytes
    colors: bytes = b''
    color_error: str = ''

    @property
    def mode(self): return self.raw[10]

    def fields(self, mode):
        if mode not in range(1, 19):
            raise ValueError('请选择内置灯效')
        offset = 0x3A + (mode-1)*2
        rgb = tuple(self.colors[mode*21:mode*21+3]) if len(self.colors) == 420 else None
        return self.raw[offset+1] >> 4, self.raw[offset], self.raw[offset+1] & 15, rgb


def validate_config(raw):
    if len(raw) != 128 or raw[-2:] != b'\x5a\xa5' or raw[10] not in (*range(1, 19), 21):
        raise ValueError('K1 灯光配置版本或标记不符，已暂停设置')
    if raw[9] != int(raw[10] == 21):
        raise ValueError('K1 灯光配置标志不符')
    for i in range(18):
        if raw[0x3A+i*2] > 4 or raw[0x3B+i*2] >> 4 > 4:
            raise ValueError('K1 亮度或速度超出已核实的五档范围')


def interface(mouse):
    d = next((d for d in hw.enumerate_hid() if d.path == mouse.path), None)
    if d is None or not matches(d) or mouse.identity != IDENTITY:
        raise OSError('K1 配置接口已断开或变化')
    return d


def read_on(dev, mouse):
    signature = identify(dev)
    raw = query(dev, 0x44, 128)
    validate_config(raw)
    colors = b''; error = ''
    try:
        colors = query(dev, 0x49, 420)
    except (OSError, ValueError, TimeoutError) as exc:
        error = str(exc)
    if identify(dev) != signature:
        raise ValueError('读取期间 K1 配对型号发生变化')
    return State(mouse, signature, raw, colors, error)


def read(mouse):
    with device_session():
        d = interface(mouse)
        with hw.HidDevice(d.path, write=True) as dev:
            check_interface(dev, d)
            return read_on(dev, mouse)


def changes(s, mode, brightness, speed, rgb=None):
    validate_config(s.raw)
    if type(mode) is not int or mode not in range(1, 19):
        raise ValueError('请选择已适配的内置灯效')
    if any(type(v) is not int or not 0 <= v <= 4 for v in (brightness, speed)):
        raise ValueError('亮度与速度必须是 0–4 档')
    raw = bytearray(s.raw); colors = bytearray(s.colors)
    raw[9] = 0  # Built-in effect; custom profile uses hardware mode 21.
    raw[10] = mode
    at = 0x3A+(mode-1)*2
    raw[at] = speed
    raw[at+1] = (brightness << 4) | (raw[at+1] & 15)
    if rgb is not None:
        if mode not in COLOR_MODES or len(colors) != 420 or raw[at+1] & 15:
            raise ValueError('当前灯效未开放单色编辑，或颜色表读取失败')
        if len(rgb) != 3 or any(type(v) is not int or not 0 <= v <= 255 for v in rgb):
            raise ValueError('RGB 颜色无效')
        # First palette color only; preserve the remaining six colors and all
        # other effect palettes. Do not turn a multi-color profile into one color.
        colors[mode*21:mode*21+3] = bytes(rgb)
    return bytes(raw), bytes(colors)


def apply(s, mode, brightness, speed, rgb=None):
    target, colors = changes(s, mode, brightness, speed, rgb)
    with device_session():
        d = interface(s.mouse)
        with hw.HidDevice(d.path, write=True) as dev:
            check_interface(dev, d)
            fresh = read_on(dev, s.mouse)
            if (fresh.signature, fresh.raw, fresh.colors) != (s.signature, s.raw, s.colors):
                raise ValueError('K1 设置已变化，请重新读取后调整')
            try:
                if colors != s.colors:
                    write_blob(dev, 9, colors)
                    if query(dev, 0x49, 420) != colors:
                        raise ValueError('K1 颜色表读回不一致')
                if target != s.raw:
                    if identify(dev) != s.signature:
                        raise ValueError('K1 配对型号变化')
                    write_blob(dev, 4, target)
                result = read_on(dev, s.mouse)
                if (result.signature, result.raw, result.colors) != (s.signature, target, colors):
                    raise ValueError('K1 设置读回不一致')
                return result
            except (OSError, ValueError, TimeoutError) as exc:
                raise OSError(f'{exc}；设置可能已部分生效，请重新读取，不会自动重试写入') from exc
