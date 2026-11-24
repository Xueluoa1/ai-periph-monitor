"""M HUB V3 receiver-only device-info reads, independent protocol implementation."""
import time
from functools import reduce
from operator import xor

RECEIVERS = {0x1014, 0x1018, 0x1016}
NAMES = ('A5 V3 Pro', 'A5 V3 Ultra+', 'A5 V3 Ultra+ (3955)', 'K7 V2 Pro+',
         'K7 V2 Ultra+', 'A7 V3', 'A7 V3 Pro', 'A7 V3 Pro+', 'A7 V3 Ultra+',
         'K5 Pro', 'K5 Ultra', 'R7 Ultra', 'V7', 'G3 V3')
GENERIC = '迈从 M HUB V3 无线鼠标（共享接收器）'


def matches(d):
    return (d.vid == 0x3837 and d.pid in RECEIVERS and
            (d.usage_page, d.usage, d.input_len, d.output_len) == (0xFF01, 1, 64, 64))


def request():
    p = bytearray(64)
    p[:8] = bytes.fromhex('4d01010000090000')
    p[8] = reduce(xor, p[2:8], 0)
    return bytes(p)


def decode(raw):
    r = bytes(raw)
    if len(r) != 64 or r[:2] != b'\x4d\x01' or r[2] not in (0, 1) or r[4:6] != b'\0\x09':
        return None
    length = r[3]
    if length < 14 or 8+length+(r[2] == 1) > 64:
        return None
    if r[2] == 1 and reduce(xor, r[2:8+length], 0) != r[8+length]:
        return None
    data = r[8:8+length]
    # Receiver firmware can report either connected state 1 or 2.
    # Keep rejecting disconnected/unknown values instead of accepting any byte.
    if int.from_bytes(data[:2], 'little') != 0x3837 or data[10] not in (1, 2):
        return None
    if data[11] not in (0, 1) or data[12] > 100:
        return None
    return data[12], 'charging' if data[11] else 'discharging'


def read(dev, d):
    if not matches(d):
        raise ValueError('迈从 V3 接口不匹配')
    product = ' '.join((getattr(d, 'product', '') or '').split())
    name = next(('迈从 MCHOSE '+n+' 鼠标' for n in NAMES
                 if product.casefold() in (n.casefold(), ('MCHOSE '+n).casefold())), GENERIC)
    # The info PID is a configuration family, not a unique marketing model.
    dev.drain()
    dev.write_output(request(), use_hidd=False)
    deadline = time.monotonic()+1.5
    while time.monotonic() < deadline:
        raw = dev.read_input(timeout_ms=100, length=64)
        value = decode(raw or b'')
        if value:
            return (*value, name), bytes(raw)
    return None, b''
