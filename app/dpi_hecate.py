"""G3M Pro configuration protocol, gated on the documented sensor layout."""
from contextlib import contextmanager
from dataclasses import replace
import time


def matches(d):
    return d.vid == 0x320F and d.pid in (0x706E, 0x706B) and (d.usage_page, d.usage, d.output_len, d.input_len) == (0xFF1C, 0x92, 64, 64)


def query(dev, command, offset=0, data=b'', length=None):
    from dpi_extended import input_exchange
    n = len(data) if length is None else length
    if not 0 <= n <= 56 or len(data) > n:
        raise ValueError('G3M 配置块长度无效')
    packet = bytearray(64)
    packet[0] = 4;packet[3] = command;packet[4] = n
    packet[5:7] = offset.to_bytes(2, 'little')
    packet[8:8+len(data)] = data
    packet[1:3] = sum(packet[3:]).to_bytes(2, 'little')
    def accept(r):
        return len(r) == 64 and r[0] == 4 and (r[3] == 255 or
            r[3] == command and r[5:7] == packet[5:7])
    r = input_exchange(dev, bytes(packet), accept)
    if r[3] == 255 or r[7] in (254, 255):
        raise ValueError('G3M 拒绝配置命令')
    if command in (3, 5) and r[4] != n:
        raise ValueError('G3M 配置块长度不匹配')
    return bytes(r[8:8+n])


@contextmanager
def session(dev):
    query(dev, 1)
    try:yield
    finally:
        time.sleep(.01)
        query(dev, 2)


def block(dev, command, length, offset=0, chunk=24):
    return b''.join(query(dev, command, offset+i, length=min(chunk, length-i)) for i in range(0, length, chunk))


def read(dev, mouse):
    from dpi_adapters import Config, validate
    from peripheral_controls import State
    with session(dev):
        header = block(dev, 3, 34)
        if not 1 <= header[6] <= 56 or int.from_bytes(header[9:11], 'little') not in (13193, 13205) or header[11] != 50 or header[14:16] != b'\x32\0' or not 1 <= header[12] <= 7:
            raise ValueError('G3M 传感器或 DPI 布局不在已确认范围')
        profile = query(dev, 5, length=1)[0]
        if profile != 0:
            raise ValueError('G3M 当前只核实了配置 1 的读写')
        raw = block(dev, 5, 84, profile*86, header[6])
        if query(dev, 5, length=1)[0] != profile:
            raise ValueError('读取期间 G3M 配置已切换')
    count = header[12]
    if raw[12] >= count or any(raw[14+i*9] not in (0, 1) for i in range(count)) or not raw[14+raw[12]*9]:
        raise ValueError('G3M 当前档位或启用标记无效')
    values = [50+50*int.from_bytes(raw[16+i*9:18+i*9], 'little') for i in range(count)]
    base = State(mouse, 'HECATE G3M Pro', None, (), profile+1, mouse.identity, raw=header)
    disabled = [str(i+1) for i in range(count) if not raw[14+i*9]]
    note = ('设备未启用第 '+ '、'.join(disabled)+' 档；可以编辑数值，但不会自动启用或切换到这些档位。') if disabled else ''
    return validate(Config(base, 'hecate', tuple((v, v) for v in values), raw[12], 50, 26000, 50, raw, note=note))


def write(dev, d, s, stage, value, activate):
    if activate and not s.raw[14+stage*9]:
        raise ValueError('此 DPI 档位未启用，请选择已启用档位')
    raw = bytearray(s.raw);pairs = list(s.stages)
    active = stage if activate else s.active
    if value is not None:
        raw[16+stage*9:18+stage*9] = (value//50-1).to_bytes(2, 'little')
        pairs[stage] = (value, value)
    raw[12] = active
    with session(dev):
        if block(dev, 3, 34) != s.base.raw or query(dev, 5, length=1) != b'\0':
            raise ValueError('G3M 设备头或配置编号已变化')
    # Runtime selection and persistence are distinct commands in this firmware.
    if active != s.active:
        with session(dev):query(dev, 0x28, data=bytes([active]))
    with session(dev):
        chunk = s.base.raw[6]
        for i in range(0, 84, chunk):
            query(dev, 6, i, bytes(raw[i:i+chunk]))
    return replace(s, raw=bytes(raw), stages=tuple(pairs), active=active)
