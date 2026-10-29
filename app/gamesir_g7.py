"""Battery-only GameSir G7 Pro / Cyclone 2 vendor HID telemetry.

Independent implementation of published wire facts. No register writes, mode
switch, driver replacement, vibration or configuration commands are issued.
"""
import time

MODEL = '小鸡 GameSir G7 Pro'
MODELS = {0x10B9: MODEL, 0x100B: '小鸡 GameSir 旋风2 手柄'}
HEARTBEAT = bytes((0x0F, 0xF2)) + bytes(62)
CYCLONE_QUERY = bytes((0x0F, 0x03)) + bytes(62)


def matches(iface):
    return (iface.vid == 0x3537 and iface.pid in MODELS
            and (iface.usage_page, iface.usage) == (0xFFF0, 0x40)
            and iface.input_len == 64 and iface.output_len == 64)


def decode(raw, model=MODEL):
    report = bytes(raw or b'')
    if len(report) != 64 or report[0] != 0x12:
        return None
    # The documented inactive/wrong-mode stream has zeroed stick axes.
    # Reject it instead of showing a disconnected controller as 0%.
    if not any(report[1:5]) or report[36] > 100:
        return None
    # Byte 35 bit 0 is described as charging/cable, not proof of charging.
    # Until hardware confirms its meaning, publish percentage only.
    return report[36], 'unknown', model


def read(dev, iface):
    if not matches(iface):
        raise ValueError('小鸡手柄电量接口不匹配')
    packet = HEARTBEAT if iface.pid == 0x10B9 else CYCLONE_QUERY
    deadline = time.monotonic() + 1.0
    heartbeat_at = 0.0
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= heartbeat_at:
            if dev.write_output(packet, use_hidd=False) is False:
                raise OSError('小鸡接收器拒绝电量查询')
            heartbeat_at = now + 0.5
        raw = dev.read_input(timeout_ms=100, length=64)
        parsed = decode(raw, MODELS[iface.pid])
        if parsed is not None:
            return parsed, raw
    return None, b''
