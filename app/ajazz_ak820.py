"""AK820 Max Plus XY receiver battery protocol; see 适配说明-AK820MaxPlus.md.

The shared 1A2C:8FFF receiver ID is not a unique model identifier. The model
association comes from the user's log and the matching vendor driver config.
No lighting, pairing, reset, firmware or keyboard-event commands are used.
"""
import time

MODEL = '黑爵 AJAZZ AK820 Max Plus / XY 2.4G 键盘'
BATTERY_QUERY = bytes.fromhex('00aa55cc330e') + bytes(59)


def matches(iface):
    return ((iface.vid, iface.pid, iface.usage_page, iface.usage) ==
            (0x1A2C, 0x8FFF, 0xFF02, 2)
            and (iface.input_len, iface.output_len, iface.feature_len) == (65, 65, 0))


def decode(raw):
    r = bytes(raw or b'')
    if (len(r) != 65 or r[:6] != BATTERY_QUERY[:6]
            or r[6] > 100 or r[7] not in (1, 3)):
        return None
    # Driver accepts connection mode 1 (wireless) or 3 (USB). Mode 0 is
    # disconnected. These are not charge flags; preserve unknown charging.
    return r[6], 'unknown', MODEL


def read(dev, iface):
    if not matches(iface):
        raise ValueError('AK820 Max Plus / XY 电量接口不匹配')
    dev.drain()
    if dev.write_output(BATTERY_QUERY, use_hidd=False) is False:
        return None, b''
    deadline = time.monotonic() + 0.8
    while time.monotonic() < deadline:
        raw = bytes(dev.read_input(timeout_ms=100, length=65) or b'')
        parsed = decode(raw)
        if parsed is not None:
            return parsed, raw
        if len(raw) == 65 and raw[:6] == BATTERY_QUERY[:6] and raw[7] == 0 and raw != BATTERY_QUERY:
            return None, raw
    return None, b''
