"""Battery reads for the logged MCHOSE G75 V2 GLW wireless keyboard."""
import time

from hid_win import HidP_Input, HidP_Output


NAME = '迈从 MCHOSE G75 V2 键盘'
REQUEST = bytes.fromhex('00 55 05 00 38 38 00 00 00').ljust(65, b'\0')


def is_g75_product(d):
    return (getattr(d, 'product', '') or '').strip().casefold() == 'mchose g75 v2 2.4g'


def matches(d):
    return ((d.vid, d.pid) == (0x3837, 0x3033)
            and is_g75_product(d)
            and (d.usage_page, d.usage, d.input_len, d.output_len, d.feature_len)
            == (1, 0, 65, 65, 0))


def decode(raw):
    # Windows includes report ID zero before the eight-byte GLW header.
    if len(raw) != 65 or raw[:4] != b'\0\xaa\x05\0' or raw[6:8] != b'\0\0':
        return None
    count = raw[5]
    if not 33 <= count <= 56:
        return None
    payload = raw[9:9 + count]
    if (sum(raw[5:9]) + sum(payload)) & 255 != raw[4] or not any(payload):
        return None
    value = raw[41]
    level = value & 0x7f
    if level <= 100:
        return level, 'charging' if value & 0x80 else 'discharging', NAME
    return None


def read(dev, d):
    if not matches(d) or any({int(c.ReportID) for c in dev.value_caps(kind)} != {0}
                             for kind in (HidP_Input, HidP_Output)):
        raise ValueError('G75 V2 接收器的配置接口或报告 ID 不符')
    dev.drain()
    dev.write_output(REQUEST, use_hidd=False)
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        raw = dev.read_input(timeout_ms=100, length=65)
        parsed = decode(raw or b'')
        if parsed is not None:
            return parsed, raw
    return None, b''
