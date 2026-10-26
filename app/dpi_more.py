"""RK M3 and Scyrox V8 exact-interface DPI transactions.

Independent implementations of protocol facts linked in 适配说明-v31.md.
"""
from contextlib import contextmanager
from dataclasses import replace
import itertools
import os
import time

_sequence = itertools.cycle(range(2, 201))


def family(d):
    if d.vid == 0x372E and d.pid in (0x1019, 0x102A) and d.usage_page == 0xFF00 and d.feature_len == 64:
        return 'rk'
    if d.vid == 0x3554 and d.pid in (0xF5F6, 0xF5F7, 0xF5F4) and (d.usage_page, d.usage, d.output_len) == (0xFF02, 2, 17) and d.input_len >= 17:
        return 'scyrox'
    return ''


def rk_packet(sequence, command, offset, data=b''):
    p = bytearray(64)
    p[0] = 3
    p[2:7] = bytes([0x50, len(data), sequence, command, offset])
    p[7:7+len(data)] = data
    p[1] = sum(p[2:63]) & 255
    return bytes(p)


def rk_block(dev, offset):
    sequence = next(_sequence)
    dev.set_feature_report(rk_packet(sequence, 0x4F, offset))
    for _ in range(12):
        time.sleep(.025)
        r = dev.get_feature_report(3, 64)
        if len(r) != 64 or r[:2] != b'\x03\x50' or r[4:6] != bytes([sequence, offset & 63]):
            continue
        if r[2]:
            raise ValueError('RK 拒绝配置读取')
        return bytes(r[6:16])
    raise TimeoutError('RK 配置未返回匹配的序号和地址')


def rk_read(dev, mouse):
    from dpi_adapters import Config, validate
    from peripheral_controls import State
    raw = b''.join(rk_block(dev, 0x40+i) for i in range(6))[:56]
    count, active = raw[2] & 15, (raw[2] >> 4)-1
    if raw[54:] != b'\xa5\x5a' or not 1 <= count <= 6 or not 0 <= active < count:
        raise ValueError('RK DPI 表头或结束标记无效')
    values = [int.from_bytes(raw[11+i*2:13+i*2], 'little') for i in range(count)]
    values = [(v+1)*50 if v < 600 else v for v in values]
    if any(not 50 <= v <= 26000 or v % 50 for v in values):
        raise ValueError('RK DPI 数据超出本型号已确认范围')
    if rk_block(dev, 0x40) != raw[:10]:
        raise ValueError('读取期间 RK 当前配置已变化')
    base = State(mouse, 'RK Royal Kludge M3', None, (), 0, mouse.identity)
    return validate(Config(base, 'rk', ((values[active], values[active]),), 0, 50, 26000, 50, raw,
                           selector=active, can_activate=False,
                           note=f'只修改设备当前第 {active+1} 档；其他档位请先用鼠标按键切换，再重新读取。'))


def scyrox_query(dev, d, command, address=0, data=b'', length=None):
    import mouse_settings as ms
    n = len(data) if length is None else length
    r = ms.atk_exchange(dev, d, command, (0, address >> 8, address & 255, n, *data),
                        lambda r: r[0] == command and (command not in (7, 8) or r[2:4] == address.to_bytes(2, 'big')))
    if r[1]:
        raise ValueError('鼠标拒绝配置命令')
    if command == 8 and r[4] != n:
        raise ValueError('鼠标配置数据长度不符')
    return bytes(r[5:15])


@contextmanager
def scyrox_session(dev, d, expected=None):
    # This is a transient driver session, not pairing or a firmware operation.
    scyrox_query(dev, d, 2, data=b'\x01')
    try:
        reply = scyrox_query(dev, d, 1, data=os.urandom(4)+bytes(4))
        identity = tuple(reply[4:7])
        if not any(identity) or identity == (255, 255, 255) or expected is not None and identity != expected:
            raise ValueError('Scyrox 身份回复无效或设备已经变化')
        yield identity
    finally:
        scyrox_query(dev, d, 2, data=b'\x00')


def scyrox_read(dev, d, mouse):
    import mouse_settings as ms
    from dpi_adapters import Config, validate
    from peripheral_controls import State
    with scyrox_session(dev, d) as identity:
        profile = scyrox_query(dev, d, 14)[0]
        if profile > 4:
            raise ValueError('Scyrox 板载配置编号无效')
        header = scyrox_query(dev, d, 8, length=6)[:6]
        if any(sum(header[i:i+2]) & 255 != 0x55 for i in (0, 2, 4)) or not 1 <= header[2] <= 8 or header[4] >= header[2]:
            raise ValueError('Scyrox DPI 表头校验失败')
        rows = [scyrox_query(dev, d, 8, 12+i*4, length=4)[:4] for i in range(header[2])]
        if any(row[2] & 0x33 for row in rows):
            raise ValueError('此 Scyrox 固件使用了未经确认的 DPI 编码')
        pairs = tuple(ms.atk_decode(row, 'step50') for row in rows)
        if scyrox_query(dev, d, 14)[0] != profile or scyrox_query(dev, d, 8, length=6)[:6] != header:
            raise ValueError('读取期间 Scyrox 档位或配置已变化')
    base = State(mouse, 'Scyrox V8', None, (), profile+1, (*mouse.identity, *identity))
    return validate(Config(base, 'scyrox', pairs, header[4], 50, 26000, 50, header+b''.join(rows)))


def write(dev, d, s, stage, value, activate):
    raw = bytearray(s.raw)
    pairs = list(s.stages)
    active = stage if activate else s.active
    if s.family == 'rk':
        i = s.selector
        raw[11+i*2:13+i*2] = (value//50-1).to_bytes(2, 'little')
        payload = bytes([raw[2]]) + bytes(raw[11+i*2:13+i*2]) + bytes(raw[27+i*3:30+i*3])
        dev.set_feature_report(rk_packet(0x50, 0x3A, 0, payload))
        time.sleep(.08)
        pairs[0] = (value, value)
    else:
        import mouse_settings as ms
        if value is not None and pairs[stage][0] != pairs[stage][1]:
            raise ValueError('此 Scyrox 分轴设置尚未核实，请使用原厂工具修改')
        with scyrox_session(dev, d, s.base.identity[2:]) as identity:
            if value is not None:
                row = ms.atk_encode(value, value, 'step50')
                scyrox_query(dev, d, 7, 12+stage*4, row)
                raw[6+stage*4:10+stage*4] = row
                pairs[stage] = (value, value)
            if active != s.active:
                data = bytes([active, (0x55-active) & 255])
                scyrox_query(dev, d, 7, 4, data)
                raw[4:6] = data
    return replace(s, raw=bytes(raw), stages=tuple(pairs), active=active)
