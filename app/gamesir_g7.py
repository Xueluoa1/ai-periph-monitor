"""Battery-only G7 Pro 10B9 HID telemetry; see 适配说明-小鸡G7.md.

Independent implementation of published wire facts. No register writes, mode
switch, driver replacement, vibration or configuration commands are issued.
"""
import time

MODEL = '小鸡 GameSir G7 Pro'
HEARTBEAT = bytes((0x0F, 0xF2)) + bytes(62)


def matches(iface):
    return ((iface.vid, iface.pid) == (0x3537, 0x10B9)
            and (iface.usage_page, iface.usage) == (0xFFF0, 0x40)
            and iface.input_len == 64 and iface.output_len == 64)


def decode(raw):
    report = bytes(raw or b'')
    if len(report) != 64 or report[0] != 0x12:
        return None
    # The documented inactive/wrong-mode stream has zeroed stick axes.
    # Reject it instead of showing a disconnected controller as 0%.
    if not any(report[1:5]) or report[36] > 100:
        return None
    # Byte 35 bit 0 is described as charging/cable, not proof of charging.
    # Until hardware confirms its meaning, publish percentage only.
    return report[36], 'unknown', MODEL


def read(dev, iface):
    if not matches(iface):
        raise ValueError('小鸡 G7 Pro 电量接口不匹配')
    deadline = time.monotonic() + 1.0
    heartbeat_at = 0.0
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= heartbeat_at:
            dev.write_output(HEARTBEAT, use_hidd=False)
            heartbeat_at = now + 0.5
        raw = dev.read_input(timeout_ms=100, length=64)
        parsed = decode(raw)
        if parsed is not None:
            return parsed, raw
    return None, b''
