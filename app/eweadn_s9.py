"""Read-only mouse identification and battery queries from the E HUB BY driver."""
import time

from hid_win import HidP_Feature


NAME = '前行者 EWEADN 无线鼠标（等待配对型号）'
# E HUB deviceJson/by.json, receiver 372E:1026. These are model IDs, not serials.
MODELS = {
    9895604649998: 'S9 Pro',
    9895604650026: 'S9 Max',
    9895604650000: 'S9 Ultra',
    9895604650058: 'S9 Ultra+',
    9895604650045: 'S7 Pro',
}


def matches(d):
    return ((d.vid, d.pid) == (0x372e, 0x1026)
            and (d.usage_page, d.usage, d.input_len, d.output_len, d.feature_len)
            == (0xff00, 1, 0, 0, 64))


def _query(dev, command, sequence, offset=None):
    frame = bytearray(64)
    frame[0], frame[2], frame[4], frame[5] = 3, 0x50, sequence, command
    if offset is not None:
        frame[6] = offset
    frame[1] = sum(frame[2:]) & 255
    dev.set_feature_report(bytes(frame))
    for _ in range(20):
        time.sleep(0.035)
        raw = dev.get_feature_report(3, 64)
        if (len(raw) == 64 and raw[:3] == b'\x03\x50\x00' and raw[4] == sequence
                and (offset is None or raw[5] == offset & 0x3f)):
            return raw
    raise TimeoutError('前行者鼠标未返回匹配的只读查询回复')


def read(dev, d):
    if not matches(d) or {int(c.ReportID) for c in dev.value_caps(HidP_Feature)} != {3}:
        raise ValueError('前行者鼠标接收器的配置接口或报告 ID 不符')
    if _query(dev, 0x47, 1)[6] != 1:
        raise ValueError('前行者鼠标未在线，请唤醒后刷新')
    # E HUB uses sequence 2 for both reads; their distinct reply addresses
    # distinguish the identity from the subsequent battery and from cached data.
    identity = _query(dev, 0x4f, 2, 0x80)
    model_id = int.from_bytes(identity[8:14], 'big')
    if model_id not in MODELS:
        raise ValueError(f'前行者接收器返回未知型号 0x{model_id:012x}')
    raw = _query(dev, 0x4f, 2, 0x81)
    level = raw[6] & 0x7f
    if level > 100:
        return None, raw
    status = 'charging' if raw[6] & 0x80 else 'discharging'
    return (level, status, f'前行者 EWEADN {MODELS[model_id]} 鼠标'), raw
