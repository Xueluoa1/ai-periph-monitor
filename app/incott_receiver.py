"""Incott Feature 09 queries and receiver status reports, including Windows' report ID."""
import time


MODELS = {1: 'Ghero', 2: 'G23', 3: 'G24', 6: 'Zero 29', 8: 'G23V2', 9: 'Zero 39', 14: 'G23V2'}


def has_feature_channel(iface):
    return ((iface.vid, iface.pid, iface.usage_page, iface.usage) == (0x093A, 0x522C, 0xFF05, 1)
            and 9 <= iface.feature_len <= 64 and iface.input_len >= 8)


def legacy_receiver(iface):
    # Verified product string on the 8K receiver whose battery is pushed as
    # Input 09. Generic PixArt strings cannot distinguish other brands here.
    return ((iface.vid, iface.pid, iface.usage_page) == (0x093A, 0x522C, 0xFF05)
            and iface.input_len >= 8
            and (iface.product or '').strip().casefold() == 'incott 8k wireless mouse')


def matches(iface):
    return has_feature_channel(iface) or legacy_receiver(iface)


def packet(iface, command, args=()):
    raw = bytes((9, command, *args))
    if len(raw) > 9 or not 9 <= iface.feature_len <= 64:
        raise ValueError('因科特 Feature 09 报告长度不匹配')
    return raw.ljust(iface.feature_len, b'\0')


def query(dev, iface, command, args=(), axis=None):
    request = packet(iface, command, args)
    try:
        dev.get_feature_report(9, iface.feature_len)
    except OSError:
        pass
    dev.set_feature_report(request)
    for _ in range(10):
        time.sleep(.05)
        raw = dev.get_feature_report(9, iface.feature_len)
        if len(raw) < 9 or raw[:2] != bytes((9, command)):
            continue
        if command == 0x82 and (raw[2] != args[0] or axis is not None and raw[8] != axis):
            continue
        return raw
    raise TimeoutError(f'因科特没有返回匹配的 0x{command:02X} 响应')


def model_name(identity):
    if (len(identity) < 9 or identity[:3] != b'\x09\x8f\x01'
            or identity[3] not in MODELS):
        raise ValueError('未确认因科特配对型号')
    return 'Incott ' + MODELS[identity[3]] + (' Pro' if 0xF1 in identity[5:7] else '')


def decode_status(raw):
    if len(raw) < 9 or raw[:2] != b'\x09\x89':
        return None
    value = raw[2] & 0x7f
    # The wireless receiver can return zero when the mouse is off or out of range.
    if not 1 <= value <= 100:
        return None
    return value, 'charging' if raw[2] & 0x80 else 'discharging', ''


def read(dev, iface, notification):
    if not has_feature_channel(iface):
        return notification(None) if legacy_receiver(iface) else (None, b'')
    try:
        identity = query(dev, iface, 0x8F)
    except OSError:
        if legacy_receiver(iface):
            return notification(None)
        raise
    model = model_name(identity)
    try:
        raw = query(dev, iface, 0x89)
    except OSError:
        raw = b''
    result = decode_status(raw)
    if result is None:
        result, raw = notification(identity[2:7])
    if result is None:
        return None, raw
    return (result[0], result[1], '因科特 ' + model + ' 鼠标'), raw
