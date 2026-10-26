"""AM Infinity .97 battery reader, verified against USB and receiver captures.

Uses only RACE_GET_BATTERY (0x30CF) over Feature Report 0x14.
Does not initialize pairing, switch links, change DPI or write configuration.
"""
import ctypes
import time

import hid_win as hw


IDENTITIES = {(0x0E8D, 0x0880): 0, (0x0E8D, 0x0703): 0x80}
REPORT_ID = 0x14
BATTERY_COMMAND = 0x30CF
MODEL_NAME = '怒喵 AM Infinity .97'


def supports_device(iface):
    return (iface.vid, iface.pid) in IDENTITIES


def matches_interface(iface):
    return supports_device(iface) and (iface.usage_page, iface.usage) == (0xFF13, 1)


def feature_report_ids(dev):
    ids = {int(cap.ReportID) for cap in dev.value_caps(hw.HidP_Feature)}
    if ctypes.sizeof(hw.HIDP_BUTTON_CAPS) != 72:
        raise ValueError('HID 能力表布局不匹配')
    count = int(dev.caps.NumberFeatureButtonCaps)
    if count:
        caps = (hw.HIDP_BUTTON_CAPS * count)()
        size = ctypes.c_ushort(count)
        status = hw.hid.HidP_GetButtonCaps(hw.HidP_Feature, ctypes.byref(caps),
                                         ctypes.byref(size), dev._preparsed)
        if status != hw.HIDP_STATUS_SUCCESS or size.value > count:
            raise ValueError('无法读取 HID Feature 报告能力')
        ids.update(int(cap.ReportID) for cap in caps[:size.value])
    return ids


def battery_request(target, length):
    if target not in IDENTITIES.values() or not 62 <= length <= 1024:
        raise ValueError('怒喵电量接口长度或目标不匹配')
    request = bytes((REPORT_ID, 6, target, 5, 0x5A, 2, 0, 0xCF, 0x30))
    return request.ljust(length, b'\0')


class BatteryResponse:
    def __init__(self, target):
        self.target = target
        self.pending = bytearray()
        self.previous = None

    def feed(self, raw):
        if not raw or raw == self.previous:
            return None
        self.previous = raw
        if raw[0] != REPORT_ID:
            return None
        if len(raw) < 3:
            raise ValueError('怒喵响应头不完整')
        count, target = raw[1:3]
        if not count or target != self.target:
            return None
        if count > 59 or count > len(raw) - 3:
            raise ValueError('怒喵响应分片长度无效')
        self.pending.extend(raw[3:3 + count])
        while len(self.pending) >= 6:
            if self.pending[0] != 5:
                raise ValueError('怒喵响应帧头无效')
            length = int.from_bytes(self.pending[2:4], 'little') + 4
            if not 6 <= length <= 1024:
                raise ValueError('怒喵响应帧长度无效')
            if len(self.pending) < length:
                return None
            frame = bytes(self.pending[:length])
            del self.pending[:length]
            command = int.from_bytes(frame[4:6], 'little')
            if frame[1] not in (0x5B, 0x5D) or command != BATTERY_COMMAND:
                continue
            return decode_battery(frame[6:])
        return None


def decode_battery(payload):
    if len(payload) < 5 or payload[0] != 0:
        raise ValueError('怒喵电量响应不完整或设备拒绝查询')
    charging, level, exists = payload[1], payload[2], payload[4]
    if charging not in (0, 1) or level > 100 or exists not in (0, 1):
        raise ValueError('怒喵电量或充电状态无效')
    if not exists:
        raise OSError('接收器当前未报告有效鼠标电池，请唤醒鼠标')
    return level, 'charging' if charging else 'discharging'


def read(iface, result_type):
    """Caller holds the application's cross-process device_session lock."""
    source = '怒喵 AM Infinity .97 电量协议'
    connection = 'USB' if iface.pid == 0x0880 else '2.4G'
    extra = {'model_name': MODEL_NAME, 'connection': connection}
    def failed(message):
        return result_type(ok=False, source=source, detail=message, extra=extra)
    if not matches_interface(iface):
        return failed('此接口不是 AM Infinity .97 电量配置接口')
    if not 62 <= iface.feature_len <= 1024:
        return failed('当前固件未提供所需电量接口，请通过官方 AM Master 检查鼠标和接收器固件')
    target = IDENTITIES[(iface.vid, iface.pid)]
    try:
        with hw.HidDevice(iface.path, write=True) as dev:
            if not dev.write_access:
                return failed('无法访问怒喵配置接口，请关闭官方驱动后重试')
            if REPORT_ID not in feature_report_ids(dev):
                return failed('当前固件未提供 Feature 0x14 电量通道，请通过官方 AM Master 检查固件')
            parser = BatteryResponse(target)
            dev.set_feature_report(battery_request(target, iface.feature_len))
            deadline = time.monotonic() + 0.8
            while time.monotonic() < deadline:
                time.sleep(0.05)
                raw = dev.get_feature_report(REPORT_ID, iface.feature_len)
                value = parser.feed(raw)
                if value is not None:
                    level, status = value
                    return result_type(ok=True, level=level, raw_level=level, status=status,
                                       source=source, detail=f'{connection} · 电量 {level}%',
                                       raw=raw, extra=extra)
            return failed('怒喵鼠标未返回电量，请唤醒鼠标；若官方驱动正在运行，请关闭后重试')
    except (OSError, ValueError) as exc:
        return failed(f'怒喵电量读取失败：{exc}')
