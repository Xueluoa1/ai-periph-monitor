"""Wireless M7 Pro 3104/3395 status query on the shared SSCYPL receiver."""
import itertools
import time

from hid_win import HidP_Feature


RECEIVER = (0x248A, 0x5B2F)
NAME = 'M7 Pro 兼容无线鼠标（共用接收器）'
_sequence = itertools.count(1)


def matches(d):
    return ((d.vid, d.pid) == RECEIVER
            and (d.usage_page, d.usage, d.input_len, d.output_len, d.feature_len)
            == (0xFF00, 1, 0, 0, 33))


def decode(raw):
    if len(raw) != 33 or raw[0] != 0x0C or raw[2:4] != b'\x20\0' or not raw[6]:
        return None
    # The USB ID also ships on other mice. Accept the two sensor codes in
    # these drivers and a printable firmware model tag before using the fields.
    tag = raw[8:12].rstrip(b'\0 ')
    if not tag.isalnum() or raw[15] not in (0x04, 0x95):
        return None
    level, state = raw[18:20]
    if level > 100 or state not in (0, 1, 2):
        return None
    model = ('机械师 M7 Pro 兼容鼠标（M263）' if tag == b'M263'
             else f'2.4G 无线鼠标（型号代码 {tag.decode("ascii")}）')
    status = ('discharging', 'charging', 'full')[state]
    return (100 if state == 2 else level), status, model


def read(dev, iface):
    if not matches(iface) or {int(cap.ReportID) for cap in dev.value_caps(HidP_Feature)} != {0x0C}:
        raise ValueError('M7 Pro 接收器的电量接口或报告 ID 不符')
    for _ in range(2):
        request = bytes((0x0C, 1, 0x20, 0, next(_sequence) & 255)).ljust(33, b'\0')
        dev.set_feature_report(request)
        time.sleep(0.02)
        raw = bytes(dev.get_feature_report(0x0C, 33) or b'')
        parsed = decode(raw)
        # The receiver may return an empty status on its first read after idle.
        empty_status = len(raw) == 33 and raw[0] == 0x0C and raw[2:4] == b'\x20\0' and not raw[6]
        if any(raw) and not empty_status:
            return parsed, raw
    return None, raw
