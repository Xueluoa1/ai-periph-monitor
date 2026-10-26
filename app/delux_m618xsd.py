"""M618XSD 320F:221D battery queries, independently derived from its driver.

Only the logged FF1C:0092 collection is used. See 适配说明-M618XSD.md.
"""
import time

MODEL = '多彩 DELUX M618XSD 鼠标'
ONLINE_QUERY = bytes.fromhex('04aa00aa00000000') + bytes(56)
BATTERY_QUERY = bytes.fromhex('0420001a06000000') + bytes(56)


def matches(iface):
    return ((iface.vid, iface.pid, iface.usage_page, iface.usage) ==
            (0x320F, 0x221D, 0xFF1C, 0x92)
            and (iface.input_len, iface.output_len, iface.feature_len) == (64, 64, 0))


def response(raw, command):
    r = bytes(raw or b'')
    return (len(r) == 64 and r[0] == 4 and r[3] == command
            and r[5:7] == b'\0\0' and r[7] not in (0xFE, 0xFF))


def decode(raw):
    r = bytes(raw or b'')
    if (not response(r, 0x1A) or r == BATTERY_QUERY or r[4] != 6
            or r[8] > 100 or r[9] not in (0, 1, 2)):
        return None
    # Driver UI: 1 = charging, 2 = battery use, 0 = powered/full icon.
    # The powered icon alone does not establish whether it is charging.
    return r[8], {0: 'unknown', 1: 'charging', 2: 'discharging'}[r[9]], MODEL


def _query(dev, packet):
    dev.drain()
    if dev.write_output(packet, use_hidd=False) is False:
        return b''
    deadline = time.monotonic() + 1.2
    while time.monotonic() < deadline:
        raw = bytes(dev.read_input(timeout_ms=100, length=64) or b'')
        if raw != packet and response(raw, packet[3]):
            return raw
    return b''


def read(dev, iface):
    if not matches(iface):
        raise ValueError('M618XSD 电量接口不匹配')
    # Receiver presence is not mouse presence. The driver's online response
    # must contain FF before a battery result may be published.
    online = _query(dev, ONLINE_QUERY)
    if not online or online[8] != 0xFF:
        return None, online
    raw = _query(dev, BATTERY_QUERY)
    return decode(raw), raw
